"""Versioned contracts shared by engine and runner."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROTOCOL_VERSION = "5"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_ATTENTION = "needs_attention"
    RECONCILING = "reconciling"
    WAITING_APPROVAL = "waiting_approval"


class Artifact(Contract):
    path: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)


class StepResult(Contract):
    status: Literal[
        "succeeded", "needs_input", "failed", "cancelled", "needs_attention"
    ]
    summary: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[Artifact] = Field(default_factory=list)
    workspace_revision: str | None = None
    error: str | None = None
    log_manifest: dict[str, Any] | None = None


class Lease(Contract):
    protocol_version: Literal["1", "2", "3", "4", "5"] = "1"
    lease_id: str
    runner_id: str
    attempt_id: str
    generation: int = Field(ge=1)
    expires_at: str


class Registration(Contract):
    protocol_version: Literal["1", "2", "3", "4", "5"] = "1"
    runner_id: str
    instance_id: str | None = None
    capacity: int = Field(default=1, ge=1)
    adapters: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    action_contracts: dict[str, int] = Field(default_factory=dict)
    log_retention: bool = False

    @model_validator(mode="after")
    def validate_action_contracts(self):
        if any(not name or version < 1 for name, version in self.action_contracts.items()):
            raise ValueError("Action contracts require a name and positive version")
        if self.protocol_version == "1" and self.action_contracts:
            raise ValueError("Protocol version 1 cannot advertise action contracts")
        return self


class AssignmentRequest(Contract):
    lease_id: str
    generation: int = Field(ge=1)


class Completion(AssignmentRequest):
    result: StepResult


class StopConfirmation(AssignmentRequest):
    stopped: bool = True
    uncertain: bool = True
    result: StepResult | None = None


class RunnerEvent(AssignmentRequest):
    sequence: int = Field(ge=1)
    payload: dict[str, Any]
