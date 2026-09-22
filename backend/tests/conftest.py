import pytest
from unittest.mock import Mock
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import load_settings
from app.database import Database
from app.service import ExperimentService
from app.access import Principal, Signatures, require_principal


class Clock:
    def __init__(self):
        self.value = 1_800_000_000_000

    def __call__(self):
        return self.value

    def advance(self, milliseconds):
        self.value += milliseconds


@pytest.fixture
def settings(tmp_path):
    return load_settings().model_copy(update={"database_path": str(tmp_path / "experiment.sqlite3")})


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def service(settings, clock):
    database = Database(settings)
    database.initialize()
    return ExperimentService(database, clock)


@pytest.fixture
def client(settings, clock):
    app = create_app(settings, clock, signatures_factory=lambda: Mock(spec=Signatures), m3_interpreter_factory=lambda *_: None)
    app.dependency_overrides[require_principal] = lambda: Principal("test-researcher", "RESEARCHER", "researcher", 1)
    with TestClient(app) as client:
        yield client
