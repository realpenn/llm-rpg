from fastapi.testclient import TestClient

from llm_rpg.api.main import app
from llm_rpg.config import Settings


def test_settings_defaults_parse() -> None:
    settings = Settings(admin_user_ids="1, 2", _env_file=None)

    assert settings.admin_user_ids == [1, 2]
    assert settings.llm_structured_mode == "auto"


def test_healthz() -> None:
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
