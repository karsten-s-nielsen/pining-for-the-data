import importlib.util
import sys
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"
NAME_POOLS_DIR = Path(__file__).parent.parent.parent / "name_pools"
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def name_pools_dir() -> Path:
    return NAME_POOLS_DIR


@pytest.fixture(scope="session")
def load_script():
    """Return a loader that imports scripts/<name>.py as a module.

    scripts/ is not a package, so tests load script modules by path. Note:
    loading a script executes its top-level imports (e.g.
    ``from mock_api.upload import upload_game``), so even "pure logic" tests
    transitively import the boto3-backed module. That's fine — no AWS calls
    occur at import time.
    """

    def _load(name: str):
        path = _SCRIPTS_DIR / f"{name}.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    return _load
