import os
import pytest
from pathlib import Path

# Use a separate test database file to avoid locking conflicts with the running server
test_db_path = Path.cwd() / "data" / "test_memory_pytest.db"
os.environ["DATABASE_PATH"] = str(test_db_path)

@pytest.fixture(autouse=True)
def clean_test_db():
    # Delete the test db before each test to guarantee isolation
    for path in [test_db_path, test_db_path.with_suffix(".db-shm"), test_db_path.with_suffix(".db-wal")]:
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass
    yield
    # Clean up after the test completes
    for path in [test_db_path, test_db_path.with_suffix(".db-shm"), test_db_path.with_suffix(".db-wal")]:
        if path.exists():
            try:
                path.unlink()
            except Exception:
                pass

@pytest.fixture
def anyio_backend():
    return 'asyncio'
