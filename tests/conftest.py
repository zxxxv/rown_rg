import pytest


@pytest.fixture
def app():
    from src.main import app

    return app
