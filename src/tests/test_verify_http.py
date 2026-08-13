import pytest


@pytest.fixture(scope="module")
def hmod(load_script):
    return load_script("_verify_http")  # shared conftest fixture


def test_parse_content_range_total_well_formed(hmod) -> None:
    assert hmod.parse_content_range_total("bytes 0-0/123456") == 123456
    assert hmod.parse_content_range_total("bytes */123") == 123
    assert hmod.parse_content_range_total("bytes 0-0/7 ") == 7  # trailing whitespace tolerated


def test_parse_content_range_total_malformed_or_missing(hmod) -> None:
    assert hmod.parse_content_range_total("") == -1  # header absent -> callers pass ""
    assert hmod.parse_content_range_total("bytes 0-0/*") == -1  # unknown total
    assert hmod.parse_content_range_total("nonsense") == -1


def test_no_follow_suppresses_redirect(hmod) -> None:
    """redirect_request returning None is load-bearing.

    It makes http_error_302 fall through to the default handler and raise
    HTTPError(code=302), which callers catch to read Location. Following the
    redirect would forward the Authorization header to presigned S3, which
    rejects requests carrying two auth mechanisms.
    """
    handler = hmod.NoFollow()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://s3.example/presigned") is None


def test_get_json_is_public_api(hmod) -> None:
    """The verify scripts import get_json / get_json_with_status by those names."""
    assert callable(hmod.get_json)
    assert callable(hmod.get_json_with_status)
    assert not hasattr(hmod, "_get_json")


class _FakeResponse:
    """Minimal stand-in for the http.client.HTTPResponse context manager."""

    def __init__(self, body: bytes, status: int) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def test_get_json_with_status_returns_body_and_status(hmod, monkeypatch) -> None:
    """The status is the superset half of the contract — verify_gradient_load.py needs it."""
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["timeout"] = timeout
        return _FakeResponse(b'{"matches": [1, 2]}', 200)

    monkeypatch.setattr(hmod.urllib.request, "urlopen", fake_urlopen)

    payload, status = hmod.get_json_with_status("https://api.example/v1", "/p/matches", "tok")

    assert payload == {"matches": [1, 2]}
    assert status == 200
    assert captured["url"] == "https://api.example/v1/p/matches"
    assert captured["auth"] == "Bearer tok"
    assert captured["timeout"] == 30


def test_get_json_delegates_to_get_json_with_status(hmod, monkeypatch) -> None:
    """One implementation builds the request; get_json is a thin accessor over it.

    Patching get_json_with_status and observing get_json change behaviour is what
    proves there is no second request-building path to drift out of sync.
    """
    calls: list[tuple] = []

    def fake_with_status(api: str, path: str, token: str):
        calls.append((api, path, token))
        return {"players": ["sentinel"]}, 206

    monkeypatch.setattr(hmod, "get_json_with_status", fake_with_status)

    result = hmod.get_json("https://api.example/v1", "/p/players", "tok")

    assert result == {"players": ["sentinel"]}  # status dropped, body passed through
    assert calls == [("https://api.example/v1", "/p/players", "tok")]


def test_get_json_decodes_utf8_body(hmod, monkeypatch) -> None:
    """End-to-end through the real delegation chain, not the patched one."""
    monkeypatch.setattr(
        hmod.urllib.request,
        "urlopen",
        lambda req, timeout=None: _FakeResponse('{"providers": ["å"]}'.encode(), 200),
    )
    assert hmod.get_json("https://api.example/v1", "/providers", "tok") == {"providers": ["å"]}
