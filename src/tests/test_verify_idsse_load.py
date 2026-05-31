import pytest


@pytest.fixture(scope="module")
def vmod(load_script):
    return load_script("verify_idsse_load")  # shared conftest fixture (review R3)


def test_parse_content_range_total(vmod) -> None:
    assert vmod.parse_content_range_total("bytes 0-0/123456") == 123456
    assert vmod.parse_content_range_total("") == -1
    assert vmod.parse_content_range_total("bytes */123") == 123
