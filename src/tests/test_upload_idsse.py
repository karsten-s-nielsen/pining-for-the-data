import pytest


@pytest.fixture(scope="module")
def mod(load_script):
    return load_script("upload_idsse_bundesliga")


def _listing() -> list[dict]:
    return [
        {
            "name": "DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-ABC123.xml",
            "download_url": "https://x/1",
            "computed_md5": "aaa",
            "size": 12000,
        },
        {
            "name": "DFL_03_02_events_raw_DFL-COM-000001_DFL-MAT-ABC123.xml",
            "download_url": "https://x/2",
            "computed_md5": "bbb",
            "size": 500000,
        },
        {
            "name": "DFL_04_03_positions_raw_observed_DFL-COM-000001_DFL-MAT-ABC123.xml",
            "download_url": "https://x/3",
            "computed_md5": "ccc",
            "size": 900000,
        },
    ]


class TestManifest:
    def test_manifest_from_listing(self, mod) -> None:
        manifest = mod.manifest_from_listing(_listing())
        assert manifest["version_url"].endswith("/versions/1")
        assert manifest["files"] == {
            "DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-ABC123.xml": "aaa",
            "DFL_03_02_events_raw_DFL-COM-000001_DFL-MAT-ABC123.xml": "bbb",
            "DFL_04_03_positions_raw_observed_DFL-COM-000001_DFL-MAT-ABC123.xml": "ccc",
        }

    def test_verify_matches(self, mod) -> None:
        manifest = mod.manifest_from_listing(_listing())
        # No exception when listing matches the manifest.
        mod.verify_listing(_listing(), manifest)

    def test_verify_detects_md5_drift(self, mod) -> None:
        manifest = mod.manifest_from_listing(_listing())
        drifted = _listing()
        drifted[0]["computed_md5"] = "ZZZ"
        with pytest.raises(ValueError, match="md5 mismatch"):
            mod.verify_listing(drifted, manifest)

    def test_verify_detects_count_drift(self, mod) -> None:
        manifest = mod.manifest_from_listing(_listing())
        with pytest.raises(ValueError, match="file count"):
            mod.verify_listing(_listing()[:2], manifest)


def test_complete_matches_filters_incomplete(mod) -> None:
    names = [
        "DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-ABC123.xml",
        "DFL_03_02_events_raw_DFL-COM-000001_DFL-MAT-ABC123.xml",
        "DFL_04_03_positions_raw_observed_DFL-COM-000001_DFL-MAT-ABC123.xml",
        "DFL_02_01_matchinformation_DFL-COM-000001_DFL-MAT-NOPE99.xml",  # lone metadata
    ]
    complete = mod.complete_matches(names)
    assert list(complete) == ["DFL-MAT-ABC123"]


class _Info:
    match_id = "DFL-MAT-ABC123"
    date = "2099-08-15"
    home = "Test Home FC"
    away = "Test Away FC"


def test_upload_all_orchestration(monkeypatch, mod) -> None:
    """fetch->verify->download->stage->upload_game wired correctly, all I/O mocked."""
    listing = _listing()
    manifest = mod.manifest_from_listing(listing)

    monkeypatch.setattr(mod, "fetch_file_listing", lambda *_a, **_k: listing)
    monkeypatch.setattr(mod, "load_manifest", lambda *_a, **_k: manifest)

    def fake_download(entry, dest):
        dest.write_text(f"<xml for {entry['name']}/>", encoding="utf-8")

    monkeypatch.setattr(mod, "download_verified", fake_download)
    monkeypatch.setattr(mod, "read_match_information", lambda _p: _Info())

    calls = []
    staged_seen: list[str] = []

    def fake_upload(**kw):
        # game_dir is a TemporaryDirectory deleted once upload_all returns, so
        # capture the staged filenames here, at call time.
        staged_seen.extend(sorted(p.name for p in kw["game_dir"].iterdir()))
        calls.append(kw)

    monkeypatch.setattr(mod, "upload_game", fake_upload)

    mod.upload_all(bucket="test-bucket", limit=None)

    assert len(calls) == 1
    kw = calls[0]
    assert kw["provider"] == "idsse"
    assert kw["game_id"] == "DFL-MAT-ABC123"
    assert kw["visibility"] == "public"
    assert kw["provenance"] == "redistributed"
    assert kw["date"] == "2099-08-15"
    assert kw["home"] == "Test Home FC"
    assert kw["away"] == "Test Away FC"
    assert kw["source_name"] == mod.SOURCE_NAME
    assert kw["source_licence"] == "CC-BY 4.0"
    assert staged_seen == ["events.xml", "metadata.xml", "tracking.xml"]
