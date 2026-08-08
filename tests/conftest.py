from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_pool_status_text():
    return (FIXTURES / "pool" / "pool.status").read_text()


@pytest.fixture
def fixture_user_text():
    return (FIXTURES / "users" / "bitaxe-gamma01").read_text()


@pytest.fixture
def fixture_log_lines():
    return (FIXTURES / "ckpool-log-slice.log").read_text().splitlines()


@pytest.fixture
def log_dir(tmp_path):
    """A copy of the fixture status files the tests may freely write around."""
    import shutil

    dst = tmp_path / "logs"
    shutil.copytree(FIXTURES / "pool", dst / "pool")
    shutil.copytree(FIXTURES / "users", dst / "users")
    shutil.copytree(FIXTURES / "workers", dst / "workers")
    return dst
