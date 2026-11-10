"""Persisted Goal JSON contracts; optional fields never add storage defaults."""

from typing import NotRequired, TypedDict


class WaitMetadata(TypedDict):
    label: str
    client: str
    thread: str
    log_file: str
    lock_file: str
    startup_file: str
    since: str
    watch_id: str
    phase: str
    event_note: NotRequired[str]


class WatchEvent(TypedDict):
    event_id: str
    event: str
    status: str | None
    at: str


class AgentAssignment(TypedDict):
    phase: str
    dispatch_token: str
    prepared_at: str
    agent_id: NotRequired[str]
    assigned_at: NotRequired[str]


class NodeAttempt(TypedDict):
    outcome: str
    result: str
    failed_at: str
    started_at: NotRequired[str]
    assignment: NotRequired[AgentAssignment]
    last_event: NotRequired[WatchEvent]


class Verification(TypedDict):
    summary: str
    checks: list[str]
    verified_at: str


class GoalEvent(TypedDict):
    seq: int
    at: str
    operation: str
    source: NotRequired[str]
    node_id: NotRequired[str]
    details: NotRequired[dict[str, object]]


class GoalNode(TypedDict):
    title: str
    kind: str
    depends_on: list[str]
    status: str
    acceptance: list[str]
    read_only: bool
    write_paths: list[str]
    inputs: dict[str, str]
    expected_artifacts: list[str]
    artifacts: list[str]
    result: str | None
    wait: WaitMetadata | None
    last_event: WatchEvent | None
    assignment: AgentAssignment | None
    attempts: list[NodeAttempt]
    started_at: NotRequired[str]
    completed_at: NotRequired[str]
    failed_at: NotRequired[str]


class ReadyNode(GoalNode):
    id: str


class GoalState(TypedDict):
    objective: str
    status: str
    client: str
    thread: str | None
    verification: Verification | None
    created_at: str
    updated_at: str
    nodes: dict[str, GoalNode]
    events: list[GoalEvent]
    result: NotRequired[str]
    completed_at: NotRequired[str]
