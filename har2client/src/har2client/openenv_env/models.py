"""OpenEnv action and observation types for HAR2Client."""

from __future__ import annotations

from typing import Any, Literal

from openenv.core.env_server.types import Action, Observation
from pydantic import Field


class H2CAction(Action):
    """`submit` a full `client.py` (ends the episode), or `probe` the practice server (Case 2, when probe_budget > 0)."""

    type: Literal["submit", "probe"] = Field(..., description="'submit' a client or 'probe' the practice server")
    code: str | None = Field(default=None, description="the full client.py text, for 'submit'")
    request: dict[str, Any] | None = Field(default=None, description="{method, path, headers, body} for 'probe'")


class H2CObservation(Observation):
    """The visible task files at reset; the probe response or the grading info after a step."""

    task_id: str = Field(default="", description="task id")
    case: str = Field(default="build", description="'build' (Cases 1-2) or 'repair' (Case 3)")
    goal: str = Field(default="", description="goal.md: the client methods to implement")
    observation: str = Field(default="", description="the recording (HAR text) the model reads")
    observation_format: str = Field(default="har", description="format of `observation`")
    files: dict[str, str] = Field(default_factory=dict, description="extra visible files (repair: client_v1.py, failure output)")
    probe_budget: int = Field(default=0, description="probes allowed in this episode")
    response: dict[str, Any] | None = Field(default=None, description="practice-server response after a probe")
    info: dict[str, Any] = Field(default_factory=dict, description="grading info after submit (pass rate, failures)")
