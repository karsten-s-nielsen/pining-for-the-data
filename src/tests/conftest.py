from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
NAME_POOLS_DIR = Path(__file__).parent.parent.parent / "name_pools"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def name_pools_dir() -> Path:
    return NAME_POOLS_DIR
