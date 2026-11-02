"""The OpenEnv wrapper gives the same reward as the Gym-style environments: reference client 1.0, empty client 0.0."""

import pytest

pytest.importorskip("openenv")

from har2client.openenv_env.environment import Har2ClientEnvironment  # noqa: E402
from har2client.openenv_env.models import H2CAction  # noqa: E402


def test_build_episode_reference_scores_one_and_empty_scores_zero():
    env = Har2ClientEnvironment()
    try:
        obs = env.reset(seed=0, case="build", curriculum="L0")
        assert obs.task_id and obs.goal and obs.observation and obs.done is False
        out = env.step(H2CAction(type="submit", code=env.reference_code()))
        assert out.done is True and out.reward == pytest.approx(1.0)
        env.reset(seed=0, case="build", curriculum="L0")
        assert env.step(H2CAction(type="submit", code="")).reward == pytest.approx(0.0)
    finally:
        env.close()


def test_repair_episode_shows_client_v1_and_grades_the_reference():
    env = Har2ClientEnvironment()
    try:
        obs = env.reset(seed=0, case="repair", curriculum="R0")
        assert "client_v1" in obs.files
        assert env.step(H2CAction(type="submit", code=env.reference_code())).reward == pytest.approx(1.0)
    finally:
        env.close()


def test_probe_action_returns_a_practice_response():
    env = Har2ClientEnvironment()
    try:
        env.reset(seed=0, case="build", curriculum="L0", probe_budget=2)
        out = env.step(H2CAction(type="probe", request={"method": "GET", "path": "/", "headers": {}}))
        assert out.done is False and out.response is not None
    finally:
        env.close()


def test_app_serves_schema_and_a_websocket_episode():
    from fastapi.testclient import TestClient

    from har2client.openenv_env.app import app

    with TestClient(app) as http:
        assert http.get("/schema").status_code == 200
        with http.websocket_connect("/ws") as ws:  # stateful episodes run over the WebSocket session
            ws.send_json({"type": "reset", "data": {"seed": 0, "case": "build", "curriculum": "L0"}})
            reset = ws.receive_json()
            assert reset["data"]["observation"]["task_id"], reset
            ws.send_json({"type": "step", "data": {"type": "submit", "code": ""}})
            step = ws.receive_json()
            assert step["data"]["done"] is True and step["data"]["reward"] == 0.0, step
