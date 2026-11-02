"""FastAPI app that serves HAR2Client over OpenEnv's HTTP and WebSocket protocol (/reset, /step, /state, /schema, /ws)."""

from openenv.core.env_server.http_server import create_app

from har2client.openenv_env.environment import Har2ClientEnvironment
from har2client.openenv_env.models import H2CAction, H2CObservation

app = create_app(Har2ClientEnvironment, H2CAction, H2CObservation, env_name="har2client", max_concurrent_envs=4)


def main(host: str = "0.0.0.0", port: int = 8000) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
