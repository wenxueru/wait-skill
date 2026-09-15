#!/usr/bin/env python3
"""Persist and validate an event-driven wait-goal dependency graph."""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import time
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict, cast

NODE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
NODE_KINDS = {"agent", "local", "external"}
NODE_STATUSES = {"pending", "dispatching", "running", "waiting", "completed", "failed", "cancelled"}
GOAL_STATUSES = {"open", "paused", "completed", "cancelled"}
WAIT_EVENTS = {"ready", "terminal", "timeout", "query_failed"}
WAIT_PHASES = {"prepared", "active"}
CLIENTS = {"claude", "codewiz", "codex", "copilot", "cursor"}
DEFAULT_STATE_ROOT = Path("/tmp/.wait-goal")


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


class WatchEvent(TypedDict):
    event_id: str
    event: str
    status: str | None
    at: str


class AgentAssignmentFields(TypedDict):
    phase: str
    dispatch_token: str
    prepared_at: str


class AgentAssignment(AgentAssignmentFields, total=False):
    agent_id: str
    assigned_at: str


class NodeAttemptFields(TypedDict):
    outcome: str
    result: str
    failed_at: str


class NodeAttempt(NodeAttemptFields, total=False):
    started_at: str
    assignment: AgentAssignment
    last_event: WatchEvent


class Verification(TypedDict):
    summary: str
    checks: list[str]
    verified_at: str


class GoalEventFields(TypedDict):
    seq: int
    at: str
    operation: str


class GoalEvent(GoalEventFields, total=False):
    node_id: str
    details: dict[str, object]


class GoalNodeFields(TypedDict):
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


class GoalNode(GoalNodeFields, total=False):
    started_at: str
    completed_at: str
    failed_at: str


class ReadyNode(GoalNode):
    id: str


class GoalStateFields(TypedDict):
    objective: str
    status: str
    client: str
    thread: str | None
    verification: Verification | None
    created_at: str
    updated_at: str
    nodes: dict[str, GoalNode]
    events: list[GoalEvent]


class GoalState(GoalStateFields, total=False):
    result: str
    completed_at: str


class GoalError(ValueError):
    """Raised when a graph operation would create invalid state."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def absolute_path(value: str | Path) -> str:
    return os.path.abspath(os.path.expanduser(os.fspath(value)))


def default_state_path(start: Path | None = None) -> Path:
    project = (start or Path.cwd()).resolve()
    for candidate in (project, *project.parents):
        if (candidate / ".git").exists():
            project = candidate
            break
    project_name = (re.sub(r"[^A-Za-z0-9._-]+", "-", project.name).strip("-.") or "project")[:48]
    project_hash = hashlib.sha256(os.fspath(project).encode()).hexdigest()[:12]
    return DEFAULT_STATE_ROOT / f"{project_name}-{project_hash}" / f"goal-{uuid.uuid4().hex[:8]}.json"


def canonical_path(value: str | Path) -> str:
    return os.path.realpath(absolute_path(value)).casefold()


def require_distinct_paths(**paths: str | Path) -> None:
    seen: dict[str, str] = {}
    for name, path in paths.items():
        canonical = canonical_path(path)
        if previous := seen.get(canonical):
            raise GoalError(f"{name} must differ from {previous}")
        seen[canonical] = name


def require_string(value: object, label: str, *, allow_none: bool = False) -> None:
    if allow_none and value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise GoalError(f"{label} must be a non-empty string")


def require_string_list(value: object, label: str, *, nonempty: bool = False) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise GoalError(f"{label} must be a list of non-empty strings")
    if nonempty and not value:
        raise GoalError(f"{label} must not be empty")


def require_fields(value: Mapping[str, object], label: str, fields: Sequence[str]) -> None:
    missing = [field for field in fields if field not in value]
    if missing:
        raise GoalError(f"{label} has missing fields: {missing}")


def require_string_map(value: object, label: str) -> None:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not key.strip() or not isinstance(item, str) or not item.strip()
        for key, item in value.items()
    ):
        raise GoalError(f"{label} must be an object of non-empty strings")


def paths_overlap(first: str, second: str) -> bool:
    """Conservatively compare lexical paths across common filesystem semantics."""
    first_path = Path(os.path.normpath(first).casefold())
    second_path = Path(os.path.normpath(second).casefold())
    if first_path.is_absolute() != second_path.is_absolute():
        return True
    return first_path == second_path or first_path in second_path.parents or second_path in first_path.parents


def lock_is_held(path: Path) -> bool:
    """Return whether another process currently owns an advisory file lock."""
    try:
        lock = path.open("r", encoding="utf-8")
    except OSError:
        return False
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
        return False
    finally:
        lock.close()


def startup_receipt_deadline(
    receipt: object,
    wait: WaitMetadata,
    node_id: str,
    watch_id: str,
) -> float:
    """Validate watcher identity and return its finite activation deadline."""
    if not isinstance(receipt, dict):
        raise GoalError("watcher startup receipt does not match the prepared wait")
    deadline = receipt.get("activation_deadline")
    matches = (
        receipt.get("event") == "watcher_started"
        and receipt.get("event_id") == watch_id
        and receipt.get("goal_node") == node_id
        and receipt.get("client", "codex") == wait.get("client", "codex")
        and receipt.get("thread") == wait["thread"]
        and receipt.get("log_file") == wait["log_file"]
        and receipt.get("lock_file") == wait["lock_file"]
    )
    valid_deadline = isinstance(deadline, (int, float)) and not isinstance(deadline, bool) and math.isfinite(deadline)
    if not matches or not valid_deadline:
        raise GoalError("watcher startup receipt does not match the prepared wait")
    return float(deadline)


class GoalGraph:
    """Validated goal state and dependency-graph operations."""

    def __init__(self, state: object) -> None:
        if not isinstance(state, dict):
            raise GoalError("state must be a JSON object")
        if "version" in state:
            raise GoalError("state must not contain a version field")
        state.setdefault("client", "codex")
        self.state = cast(GoalState, state)

    @classmethod
    def create(cls, objective: str, thread: str | None, client: str = "codex") -> GoalGraph:
        now = utc_now()
        state: GoalState = {
            "objective": objective,
            "status": "open",
            "client": client,
            "thread": thread,
            "verification": None,
            "created_at": now,
            "updated_at": now,
            "nodes": {},
            "events": [],
        }
        graph = cls(state)
        graph.record_event("init")
        graph.validate()
        return graph

    @property
    def nodes(self) -> dict[str, GoalNode]:
        nodes = self.state.get("nodes")
        if not isinstance(nodes, dict):
            raise GoalError("nodes must be an object")
        return cast(dict[str, GoalNode], nodes)

    @property
    def events(self) -> list[GoalEvent]:
        events = self.state.get("events")
        if not isinstance(events, list):
            raise GoalError("events must be a list")
        return cast(list[GoalEvent], events)

    def record_event(
        self,
        operation: str,
        *,
        node_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        event: GoalEvent = {
            "seq": len(self.events) + 1,
            "at": utc_now(),
            "operation": operation,
        }
        if node_id is not None:
            event["node_id"] = node_id
        if details:
            event["details"] = details
        self.events.append(event)

    @staticmethod
    def validate_wait(node_id: str, node: GoalNode) -> None:
        wait = node.get("wait")
        if wait is not None:
            if node["kind"] != "external":
                raise GoalError(f"node {node_id!r} with wait metadata must be external")
            if not isinstance(wait, dict):
                raise GoalError(f"node {node_id!r} wait metadata must be an object")
            for field in (
                "label",
                "thread",
                "log_file",
                "lock_file",
                "startup_file",
                "since",
                "watch_id",
            ):
                require_string(wait.get(field), f"node {node_id!r} wait.{field}")
            if wait.get("client", "codex") not in CLIENTS:
                raise GoalError(f"node {node_id!r} has invalid wait client")
            phase = wait.get("phase")
            if not isinstance(phase, str) or phase not in WAIT_PHASES:
                raise GoalError(f"node {node_id!r} has invalid wait phase")
            expected_status = "running" if phase == "prepared" else "waiting"
            if node["status"] != expected_status:
                raise GoalError(f"node {node_id!r} in wait phase {phase!r} must be {expected_status}")
        elif node["status"] == "waiting":
            raise GoalError(f"waiting node {node_id!r} must contain wait metadata")

        event = node.get("last_event")
        if event is None:
            return
        if not isinstance(event, dict):
            raise GoalError(f"node {node_id!r} has invalid last_event")
        event_name = event.get("event")
        if not isinstance(event_name, str) or event_name not in WAIT_EVENTS:
            raise GoalError(f"node {node_id!r} has invalid last_event")
        require_string(event.get("event_id"), f"node {node_id!r} last_event.event_id")
        require_string(event.get("at"), f"node {node_id!r} last_event.at")
        require_string(event.get("status"), f"node {node_id!r} last_event.status", allow_none=True)

    def validate_verification(self) -> None:
        verification = self.state.get("verification")
        if verification is None:
            return
        if not isinstance(verification, dict):
            raise GoalError("verification must be an object or null")
        require_string(verification.get("summary"), "verification.summary")
        require_string_list(verification.get("checks"), "verification.checks", nonempty=True)
        require_string(verification.get("verified_at"), "verification.verified_at")
        nodes = self.state.get("nodes")
        if (
            not isinstance(nodes, dict)
            or not nodes
            or any(not isinstance(node, dict) or node.get("status") != "completed" for node in nodes.values())
        ):
            raise GoalError("verification requires every node to be completed")

    def validate(self) -> None:
        require_fields(
            self.state,
            "state",
            (
                "objective",
                "status",
                "client",
                "thread",
                "verification",
                "created_at",
                "updated_at",
                "nodes",
                "events",
            ),
        )
        require_string(self.state.get("objective"), "objective")
        goal_status = self.state.get("status")
        if not isinstance(goal_status, str) or goal_status not in GOAL_STATUSES:
            raise GoalError("invalid goal status")
        client = self.state.get("client")
        if not isinstance(client, str) or client not in CLIENTS:
            raise GoalError("invalid client")
        require_string(self.state.get("thread"), "thread", allow_none=True)
        require_string(self.state.get("created_at"), "created_at")
        require_string(self.state.get("updated_at"), "updated_at")
        if "result" in self.state:
            require_string(self.state["result"], "result")
        if "completed_at" in self.state:
            require_string(self.state["completed_at"], "completed_at")

        nodes = self.nodes
        for node_id, node in nodes.items():
            self._validate_node(node_id, node, nodes)

        self._validate_events()
        self.validate_verification()
        if self.state["status"] == "completed":
            if self.state.get("verification") is None:
                raise GoalError("completed goal must contain verification")
            require_string(self.state.get("result"), "completed goal result")
            require_string(self.state.get("completed_at"), "completed goal completed_at")
        elif "result" in self.state or "completed_at" in self.state:
            raise GoalError("only a completed goal may contain result or completed_at")
        self._validate_acyclic()

    def _validate_node(
        self,
        node_id: object,
        node: object,
        nodes: dict[str, GoalNode],
    ) -> None:
        if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id):
            raise GoalError(f"invalid node id: {node_id!r}")
        if not isinstance(node, dict):
            raise GoalError(f"node {node_id!r} must be an object")
        node = cast(GoalNode, node)
        require_fields(
            node,
            f"node {node_id!r}",
            (
                "title",
                "kind",
                "depends_on",
                "status",
                "acceptance",
                "read_only",
                "write_paths",
                "inputs",
                "expected_artifacts",
                "artifacts",
                "result",
                "wait",
                "last_event",
                "assignment",
                "attempts",
            ),
        )
        require_string(node.get("title"), f"node {node_id!r} title")
        kind = node.get("kind")
        if not isinstance(kind, str) or kind not in NODE_KINDS:
            raise GoalError(f"node {node_id!r} has invalid kind")
        status_value = node.get("status")
        if not isinstance(status_value, str) or status_value not in NODE_STATUSES:
            raise GoalError(f"node {node_id!r} has invalid status")
        require_string_list(node.get("depends_on"), f"node {node_id!r} depends_on")
        require_string_list(node.get("acceptance"), f"node {node_id!r} acceptance")
        if not isinstance(node.get("read_only"), bool):
            raise GoalError(f"node {node_id!r} read_only must be a boolean")
        require_string_list(node.get("write_paths"), f"node {node_id!r} write_paths")
        if node["read_only"] and node["write_paths"]:
            raise GoalError(f"read-only node {node_id!r} must not declare write paths")
        require_string_map(node.get("inputs"), f"node {node_id!r} inputs")
        require_string_list(node.get("expected_artifacts"), f"node {node_id!r} expected_artifacts")
        require_string_list(node.get("artifacts"), f"node {node_id!r} artifacts")
        require_string(node.get("result"), f"node {node_id!r} result", allow_none=True)
        for field in ("started_at", "completed_at", "failed_at"):
            if field in node:
                require_string(node[field], f"node {node_id!r} {field}")
        status = node["status"]
        if status in {"running", "waiting", "completed"}:
            require_string(node.get("started_at"), f"node {node_id!r} started_at")
        elif status in {"pending", "dispatching"} and "started_at" in node:
            raise GoalError(f"unstarted node {node_id!r} must not contain started_at")
        if status == "dispatching" and node["kind"] != "agent":
            raise GoalError(f"dispatching node {node_id!r} must be an agent")
        if status == "completed":
            require_string(node.get("result"), f"completed node {node_id!r} result")
            require_string(node.get("completed_at"), f"completed node {node_id!r} completed_at")
            missing_artifacts = self._missing_artifacts(node, node["artifacts"])
            if missing_artifacts:
                raise GoalError(f"completed node {node_id!r} has missing artifacts: {missing_artifacts}")
        elif "completed_at" in node:
            raise GoalError(f"only a completed node may contain completed_at: {node_id!r}")
        elif node["artifacts"]:
            raise GoalError(f"only a completed node may contain artifacts: {node_id!r}")
        if status == "failed":
            require_string(node.get("result"), f"failed node {node_id!r} result")
            require_string(node.get("failed_at"), f"failed node {node_id!r} failed_at")
        elif status != "completed" and node.get("result") is not None:
            raise GoalError(f"active or cancelled node {node_id!r} must not contain result")
        if status != "failed" and "failed_at" in node:
            raise GoalError(f"only a failed node may contain failed_at: {node_id!r}")

        assignment = node.get("assignment")
        assignment_phase = None
        if assignment is not None:
            if not isinstance(assignment, dict):
                raise GoalError(f"node {node_id!r} assignment must be an object or null")
            if node["kind"] != "agent":
                raise GoalError(f"non-agent node {node_id!r} must not have an assignment")
            phase = assignment.get("phase")
            if not isinstance(phase, str) or phase not in {"prepared", "active"}:
                raise GoalError(f"node {node_id!r} assignment has invalid phase")
            assignment_phase = phase
            require_string(assignment.get("dispatch_token"), f"node {node_id!r} assignment.dispatch_token")
            require_string(assignment.get("prepared_at"), f"node {node_id!r} assignment.prepared_at")
            if phase == "active":
                require_string(assignment.get("agent_id"), f"node {node_id!r} assignment.agent_id")
                require_string(assignment.get("assigned_at"), f"node {node_id!r} assignment.assigned_at")
            elif "agent_id" in assignment or "assigned_at" in assignment:
                raise GoalError(f"prepared assignment for node {node_id!r} must not contain an agent ID")
        if node["kind"] == "agent":
            if node["status"] == "dispatching" or (node["status"] == "cancelled" and assignment_phase == "prepared"):
                expected_phase = "prepared"
            elif "started_at" in node:
                expected_phase = "active"
            else:
                expected_phase = None
            if assignment_phase != expected_phase:
                raise GoalError(f"agent node {node_id!r} assignment does not match its status")
        attempts = node.get("attempts")
        if not isinstance(attempts, list):
            raise GoalError(f"node {node_id!r} attempts must be a list")
        for index, attempt in enumerate(attempts):
            self._validate_attempt(node_id, index, attempt)
        missing = [item for item in node["depends_on"] if item not in nodes]
        if missing:
            raise GoalError(f"node {node_id!r} has missing dependencies: {missing}")
        invalid_inputs = {name: source for name, source in node["inputs"].items() if source not in node["depends_on"]}
        if invalid_inputs:
            raise GoalError(f"node {node_id!r} inputs must reference direct dependencies: {invalid_inputs}")
        self.validate_wait(node_id, node)

    def _validate_events(self) -> None:
        first_event = self.events[0] if self.events else None
        if not isinstance(first_event, dict) or first_event.get("operation") != "init":
            raise GoalError("events must begin with init")
        for expected_seq, event in enumerate(self.events, start=1):
            label = f"event {expected_seq}"
            if not isinstance(event, dict):
                raise GoalError(f"{label} must be an object")
            require_fields(event, label, ("seq", "at", "operation"))
            if event.get("seq") != expected_seq:
                raise GoalError(f"{label} has non-contiguous sequence")
            require_string(event.get("at"), f"{label} at")
            require_string(event.get("operation"), f"{label} operation")
            if "node_id" in event:
                require_string(event["node_id"], f"{label} node_id")
            if "details" in event and not isinstance(event["details"], dict):
                raise GoalError(f"{label} details must be an object")

    @staticmethod
    def _validate_attempt(node_id: str, index: int, attempt: object) -> None:
        label = f"node {node_id!r} attempt {index}"
        if not isinstance(attempt, dict):
            raise GoalError(f"{label} must be an object")
        require_fields(attempt, label, ("outcome", "result", "failed_at"))
        if attempt.get("outcome") != "failed":
            raise GoalError(f"{label} has invalid outcome")
        require_string(attempt.get("result"), f"{label} result")
        require_string(attempt.get("failed_at"), f"{label} failed_at")
        if "started_at" in attempt:
            require_string(attempt["started_at"], f"{label} started_at")
        assignment = attempt.get("assignment")
        if assignment is not None:
            if not isinstance(assignment, dict):
                raise GoalError(f"{label} assignment must be an object")
            require_string(assignment.get("agent_id"), f"{label} assignment.agent_id")
            require_string(assignment.get("assigned_at"), f"{label} assignment.assigned_at")
        event = attempt.get("last_event")
        if event is not None:
            if not isinstance(event, dict):
                raise GoalError(f"{label} has invalid last_event")
            event_name = event.get("event")
            if not isinstance(event_name, str) or event_name not in WAIT_EVENTS:
                raise GoalError(f"{label} has invalid last_event")
            require_string(event.get("event_id"), f"{label} last_event.event_id")
            require_string(event.get("at"), f"{label} last_event.at")
            require_string(event.get("status"), f"{label} last_event.status", allow_none=True)

    def _validate_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()
        nodes = self.nodes
        for root in nodes:
            if root in visited:
                continue
            visiting.add(root)
            stack = [(root, iter(nodes[root]["depends_on"]))]
            while stack:
                node_id, dependencies = stack[-1]
                try:
                    dependency = next(dependencies)
                except StopIteration:
                    stack.pop()
                    visiting.remove(node_id)
                    visited.add(node_id)
                    continue
                if dependency in visiting:
                    raise GoalError(f"dependency cycle includes {dependency!r}")
                if dependency not in visited:
                    visiting.add(dependency)
                    stack.append((dependency, iter(nodes[dependency]["depends_on"])))

    def get_node(self, node_id: str) -> GoalNode:
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            raise GoalError(f"unknown node: {node_id}") from exc

    @staticmethod
    def node_is_ready(node: GoalNode, nodes: dict[str, GoalNode]) -> bool:
        return node["status"] == "pending" and all(
            nodes[dependency]["status"] == "completed" for dependency in node["depends_on"]
        )

    def _raw_ready_nodes(self) -> list[ReadyNode]:
        nodes = self.nodes
        return [cast(ReadyNode, {**node, "id": node_id}) for node_id, node in nodes.items() if self.node_is_ready(node, nodes)]

    def _nodes_with_status(self, *statuses: str) -> dict[str, GoalNode]:
        return {node_id: node for node_id, node in self.nodes.items() if node["status"] in statuses}

    def orphaned_wait_ids(self) -> list[str]:
        """Return waiting nodes whose watcher no longer owns its lock."""
        return [
            node_id
            for node_id, node in self._nodes_with_status("waiting").items()
            if not lock_is_held(Path(cast(WaitMetadata, node["wait"])["lock_file"]))
        ]

    @staticmethod
    def nodes_conflict(first: GoalNode, second: GoalNode) -> bool:
        if first["read_only"] or second["read_only"]:
            return False
        if not first["write_paths"] or not second["write_paths"]:
            return True
        return any(
            paths_overlap(first_path, second_path)
            for first_path in first["write_paths"]
            for second_path in second["write_paths"]
        )

    @classmethod
    def _conflicting_node_ids(cls, node: GoalNode, others: Mapping[str, GoalNode]) -> list[str]:
        return [node_id for node_id, other in others.items() if cls.nodes_conflict(node, other)]

    @staticmethod
    def _missing_artifacts(node: GoalNode, artifacts: Sequence[str]) -> list[str]:
        return sorted(set(node["expected_artifacts"]) - set(artifacts))

    def ready_nodes(self) -> list[ReadyNode]:
        if self.state["status"] != "open" or self._nodes_with_status("dispatching"):
            return []
        active = self._nodes_with_status("running", "waiting")
        selected: dict[str, ReadyNode] = {}
        for candidate in self._raw_ready_nodes():
            if self._conflicting_node_ids(candidate, active) or self._conflicting_node_ids(candidate, selected):
                continue
            selected[candidate["id"]] = candidate
        return list(selected.values())

    def lint(self) -> list[dict[str, object]]:
        issues: list[dict[str, object]] = []
        ready = self._raw_ready_nodes() if self.state["status"] == "open" else []
        active = self._nodes_with_status("dispatching", "running", "waiting")
        earlier: dict[str, GoalNode] = {}
        for node in ready:
            conflicting = [
                *self._conflicting_node_ids(node, active),
                *self._conflicting_node_ids(node, earlier),
            ]
            if conflicting:
                issues.append({
                    "code": "write_path_conflict",
                    "severity": "warning",
                    "node_id": node["id"],
                    "conflicts_with": conflicting,
                    "message": "node is ready but must not run concurrently",
                })
            earlier[node["id"]] = node
        for node_id, node in self.nodes.items():
            if not node["read_only"] and not node["write_paths"]:
                issues.append({
                    "code": "unknown_write_scope",
                    "severity": "warning",
                    "node_id": node_id,
                    "message": "empty write scope is conservatively serialized",
                })
            if node["kind"] == "agent" and not node["acceptance"]:
                issues.append({
                    "code": "missing_acceptance",
                    "severity": "warning",
                    "node_id": node_id,
                    "message": "agent node has no acceptance checks",
                })
            blockers = [
                dependency for dependency in node["depends_on"] if self.nodes[dependency]["status"] in {"failed", "cancelled"}
            ]
            if node["status"] == "pending" and blockers:
                issues.append({
                    "code": "terminal_dependency",
                    "severity": "error",
                    "node_id": node_id,
                    "blocked_by": blockers,
                    "message": "pending node cannot become ready",
                })
        for node_id in self.orphaned_wait_ids():
            issues.append({
                "code": "orphaned_wait",
                "severity": "error",
                "node_id": node_id,
                "message": "waiting node no longer has a live watcher",
            })
        return issues

    def activity(self) -> str:
        if self.state["status"] != "open":
            return str(self.state["status"])
        nodes = self.nodes
        statuses = {node["status"] for node in nodes.values()}
        if "dispatching" in statuses:
            return "dispatching"
        if "waiting" in statuses and self.orphaned_wait_ids():
            return "orphaned_wait"
        if self.ready_nodes():
            return "ready"
        if "running" in statuses:
            return "running"
        if "waiting" in statuses:
            return "waiting"
        if statuses == {"completed"}:
            return "verified" if self.state.get("verification") else "awaiting_verification"
        if statuses:
            return "blocked"
        return "idle"

    def add_node(
        self,
        node_id: str,
        title: str,
        kind: str,
        depends_on: list[str],
        before: str | None,
        acceptance: list[str],
        read_only: bool,
        write_paths: list[str],
        inputs: dict[str, str],
        expected_artifacts: list[str],
        reason: str | None,
    ) -> None:
        if not NODE_ID.fullmatch(node_id):
            raise GoalError("node id must use letters, digits, dot, underscore, or hyphen")
        if self.state["status"] != "open":
            raise GoalError(f"cannot add a node while goal is {self.state['status']}")
        if reason is not None:
            require_string(reason, "reason")
        nodes = self.nodes
        if node_id in nodes:
            raise GoalError(f"node already exists: {node_id}")
        missing = [dependency for dependency in depends_on if dependency not in nodes]
        if missing:
            raise GoalError(f"dependencies must be added first: {missing}")

        target = self.get_node(before) if before else None
        if target is not None and target["status"] != "pending":
            raise GoalError(f"cannot insert before node {before} in {target['status']}")

        dependencies = list(dict.fromkeys(depends_on))
        nodes[node_id] = {
            "title": title,
            "kind": kind,
            "depends_on": dependencies,
            "status": "pending",
            "acceptance": list(dict.fromkeys(acceptance)),
            "read_only": read_only,
            "write_paths": list(dict.fromkeys(write_paths)),
            "inputs": dict(inputs),
            "expected_artifacts": list(dict.fromkeys(expected_artifacts)),
            "artifacts": [],
            "result": None,
            "wait": None,
            "last_event": None,
            "assignment": None,
            "attempts": [],
        }
        if target is not None:
            target["depends_on"] = list(dict.fromkeys([*target["depends_on"], node_id]))
        self.state["verification"] = None
        details: dict[str, object] = {
            "depends_on": dependencies,
            "kind": kind,
        }
        if before is not None:
            details["before"] = before
        if reason is not None:
            details["reason"] = reason
        self.record_event("add_node", node_id=node_id, details=details)

    def prepare_agent(self, node_id: str) -> str:
        if self.state["status"] != "open":
            raise GoalError(f"cannot prepare an agent while goal is {self.state['status']}")
        if dispatching := self._nodes_with_status("dispatching"):
            raise GoalError(f"cannot prepare an agent while dispatch is unresolved: {list(dispatching)}")
        node = self.get_node(node_id)
        if node["kind"] != "agent":
            raise GoalError(f"node {node_id} is {node['kind']}, not agent")
        if not self.node_is_ready(node, self.nodes):
            raise GoalError(f"node {node_id} is not ready")
        active = self._nodes_with_status("running", "waiting")
        conflicts = self._conflicting_node_ids(node, active)
        if conflicts:
            raise GoalError(f"node {node_id} has write-path conflicts with active nodes: {conflicts}")
        dispatch_token = uuid.uuid4().hex
        node["status"] = "dispatching"
        node["assignment"] = {
            "phase": "prepared",
            "dispatch_token": dispatch_token,
            "prepared_at": utc_now(),
        }
        self.record_event("prepare_agent", node_id=node_id, details={"dispatch_token": dispatch_token})
        return dispatch_token

    def start_node(
        self,
        node_id: str,
        agent_id: str | None = None,
        dispatch_token: str | None = None,
    ) -> None:
        if self.state["status"] != "open":
            raise GoalError(f"cannot start a node while goal is {self.state['status']}")
        node = self.get_node(node_id)
        if node["kind"] == "agent":
            if node["status"] != "dispatching":
                raise GoalError(f"agent node {node_id} is not prepared for dispatch")
            assignment = cast(AgentAssignment, node["assignment"])
            require_string(agent_id, "agent_id")
            require_string(dispatch_token, "dispatch_token")
            if assignment["dispatch_token"] != dispatch_token:
                raise GoalError(f"dispatch token {dispatch_token!r} does not match prepared assignment")
            assignment.update({"phase": "active", "agent_id": cast(str, agent_id), "assigned_at": utc_now()})
        else:
            if agent_id is not None or dispatch_token is not None:
                raise GoalError("--agent-id and --dispatch-token are only valid for agent nodes")
            if dispatching := self._nodes_with_status("dispatching"):
                raise GoalError(f"cannot start a node while dispatch is unresolved: {list(dispatching)}")
            if not self.node_is_ready(node, self.nodes):
                raise GoalError(f"node {node_id} is not ready")
            active = self._nodes_with_status("running", "waiting")
            conflicts = self._conflicting_node_ids(node, active)
            if conflicts:
                raise GoalError(f"node {node_id} has write-path conflicts with active nodes: {conflicts}")
        node["status"] = "running"
        node["started_at"] = utc_now()
        self.record_event("start_node", node_id=node_id)

    def abort_agent(self, node_id: str, dispatch_token: str) -> None:
        if self.state["status"] != "open":
            raise GoalError(f"cannot abort an agent dispatch while goal is {self.state['status']}")
        node = self.get_node(node_id)
        if node["kind"] != "agent" or node["status"] != "dispatching":
            raise GoalError(f"node {node_id} does not have a prepared agent dispatch")
        assignment = cast(AgentAssignment, node["assignment"])
        if assignment.get("dispatch_token") != dispatch_token:
            raise GoalError(f"dispatch token {dispatch_token!r} does not match prepared assignment")
        node["status"] = "pending"
        node["assignment"] = None
        self.record_event("abort_agent", node_id=node_id, details={"dispatch_token": dispatch_token})

    def prepare_external_wait(
        self,
        node_id: str,
        label: str,
        log_file: str,
        lock_file: str,
        startup_file: str,
    ) -> str:
        if self.state["status"] != "open":
            raise GoalError(f"cannot prepare a wait while goal is {self.state['status']}")
        require_string(self.state.get("thread"), "goal session")
        node = self.get_node(node_id)
        if node["kind"] != "external":
            raise GoalError(f"node {node_id} is {node['kind']}, not external")
        if node["status"] != "running":
            raise GoalError(f"node {node_id} is {node['status']}, not running")
        if node["wait"] is not None:
            raise GoalError(f"node {node_id} already has a prepared wait")
        watch_id = uuid.uuid4().hex
        node["wait"] = {
            "label": label,
            "client": self.state["client"],
            "thread": cast(str, self.state["thread"]),
            "log_file": absolute_path(log_file),
            "lock_file": absolute_path(lock_file),
            "startup_file": absolute_path(startup_file),
            "since": utc_now(),
            "watch_id": watch_id,
            "phase": "prepared",
        }
        self.record_event("prepare_wait", node_id=node_id, details={"watch_id": watch_id})
        return watch_id

    def activate_external_wait(self, node_id: str, watch_id: str) -> None:
        if self.state["status"] != "open":
            raise GoalError(f"cannot activate a wait while goal is {self.state['status']}")
        node = self.get_node(node_id)
        wait = node.get("wait")
        if node["status"] != "running" or not isinstance(wait, dict):
            raise GoalError(f"node {node_id} does not have a prepared wait")
        if wait.get("phase") != "prepared":
            raise GoalError(f"node {node_id} wait is already active")
        if wait.get("watch_id") != watch_id:
            raise GoalError(f"watch id {watch_id!r} does not match the prepared wait")
        try:
            receipt = json.loads(Path(wait["startup_file"]).read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError) as exc:
            raise GoalError("watcher startup receipt is not available") from exc
        activation_deadline = startup_receipt_deadline(receipt, cast(WaitMetadata, wait), node_id, watch_id)
        if time.time() > activation_deadline:
            raise GoalError("watcher startup receipt has expired")
        if not lock_is_held(Path(wait["lock_file"])):
            raise GoalError("watcher no longer holds its lock")
        wait["phase"] = "active"
        node["status"] = "waiting"
        self.record_event("activate_wait", node_id=node_id, details={"watch_id": watch_id})

    def abort_external_wait(self, node_id: str, watch_id: str) -> None:
        if self.state["status"] not in {"open", "paused"}:
            raise GoalError(f"cannot abort a wait while goal is {self.state['status']}")
        node = self.get_node(node_id)
        wait = node.get("wait")
        recoverable = node["kind"] == "external" and node["status"] in {"running", "waiting"} and isinstance(wait, dict)
        if not recoverable:
            raise GoalError(f"node {node_id} does not have a prepared or active wait")
        if wait.get("watch_id") != watch_id:
            raise GoalError(f"watch id {watch_id!r} does not match current wait")
        node["status"] = "running"
        node["wait"] = None
        self.record_event("abort_wait", node_id=node_id, details={"watch_id": watch_id})

    def wake_node(
        self,
        node_id: str,
        event_id: str,
        event: str,
        external_status: str | None,
    ) -> tuple[str, bool]:
        node = self.get_node(node_id)
        last_event = node.get("last_event")
        if isinstance(last_event, dict) and last_event.get("event_id") == event_id:
            if last_event.get("event") != event or last_event.get("status") != external_status:
                raise GoalError(f"event id {event_id!r} was already used differently")
            return str(node["status"]), True

        if node["kind"] != "external":
            raise GoalError(f"node {node_id} is {node['kind']}, not external")
        if node["status"] != "waiting":
            raise GoalError(f"node {node_id} is {node['status']}, not waiting")
        wait = cast(WaitMetadata, node["wait"])
        if event_id != wait["watch_id"]:
            raise GoalError(f"event id {event_id!r} does not match active watch {wait['watch_id']!r}")
        node["last_event"] = {
            "event_id": event_id,
            "event": event,
            "status": external_status,
            "at": utc_now(),
        }
        node["status"] = "running"
        node["wait"] = None
        self.record_event(
            "wake_node",
            node_id=node_id,
            details={"event": event, "event_id": event_id, "status": external_status},
        )
        return str(node["status"]), False

    def complete_node(self, node_id: str, summary: str, artifacts: list[str]) -> None:
        node = self.get_node(node_id)
        if node["status"] != "running":
            raise GoalError(f"node {node_id} is {node['status']}, not running")
        artifacts = list(dict.fromkeys(artifacts))
        missing = self._missing_artifacts(node, artifacts)
        if missing:
            raise GoalError(f"node {node_id} is missing expected artifacts: {missing}")
        node["status"] = "completed"
        node["result"] = summary
        node["artifacts"] = artifacts
        node["completed_at"] = utc_now()
        self.state["verification"] = None
        self.record_event("complete_node", node_id=node_id, details={"artifacts": artifacts})

    def fail_node(self, node_id: str, summary: str) -> None:
        node = self.get_node(node_id)
        if node["status"] in {"completed", "failed", "cancelled"}:
            raise GoalError(f"node {node_id} cannot fail from {node['status']}")
        if node["status"] == "dispatching":
            raise GoalError(f"abort agent dispatch {node_id} before marking it failed")
        node["status"] = "failed"
        node["result"] = summary
        node["failed_at"] = utc_now()
        node["wait"] = None
        self.state["verification"] = None
        self.record_event("fail_node", node_id=node_id)

    def retry_node(self, node_id: str) -> None:
        if self.state["status"] != "open":
            raise GoalError(f"cannot retry a node while goal is {self.state['status']}")
        node = self.get_node(node_id)
        if node["status"] != "failed":
            raise GoalError(f"node {node_id} is {node['status']}, not failed")
        attempt: NodeAttempt = {
            "outcome": "failed",
            "result": cast(str, node["result"]),
            "failed_at": node["failed_at"],
        }
        if "started_at" in node:
            attempt["started_at"] = node["started_at"]
        assignment = node.get("assignment")
        if assignment is not None:
            attempt["assignment"] = cast(AgentAssignment, dict(assignment))
        last_event = node.get("last_event")
        if last_event is not None:
            attempt["last_event"] = cast(WatchEvent, dict(last_event))
        node["attempts"].append(attempt)
        node["status"] = "pending"
        node["result"] = None
        node["wait"] = None
        node["last_event"] = None
        node["assignment"] = None
        node.pop("started_at", None)
        node.pop("failed_at", None)
        self.state["verification"] = None
        self.record_event("retry_node", node_id=node_id)

    def verify(self, summary: str, checks: list[str]) -> None:
        if self.state["status"] != "open":
            raise GoalError(f"cannot verify a goal while it is {self.state['status']}")
        nodes = self.nodes
        if not nodes:
            raise GoalError("cannot verify a goal with no nodes")
        incomplete = [node_id for node_id, node in nodes.items() if node["status"] != "completed"]
        if incomplete:
            raise GoalError(f"cannot verify; incomplete nodes: {incomplete}")
        self.state["verification"] = {
            "summary": summary,
            "checks": list(dict.fromkeys(checks)),
            "verified_at": utc_now(),
        }
        self.record_event("verify_goal", details={"checks": checks})

    def pause(self) -> None:
        if self.state["status"] != "open":
            raise GoalError(f"cannot pause a {self.state['status']} goal")
        self.state["status"] = "paused"
        self.record_event("pause_goal")

    def resume(self) -> None:
        if self.state["status"] != "paused":
            raise GoalError(f"goal is {self.state['status']}, not paused")
        self.state["status"] = "open"
        self.record_event("resume_goal")

    def cancel(self) -> None:
        if self.state["status"] == "completed":
            raise GoalError("cannot cancel a completed goal")
        self.state["status"] = "cancelled"
        for node in self.nodes.values():
            if node["status"] not in {"completed", "failed"}:
                node["status"] = "cancelled"
                node["wait"] = None
        self.record_event("cancel_goal")

    def finish(self, summary: str) -> None:
        if self.state["status"] != "open":
            raise GoalError(f"cannot finish a goal while it is {self.state['status']}")
        if self.state.get("verification") is None:
            raise GoalError("cannot finish before objective verification")
        self.state["status"] = "completed"
        self.state["result"] = summary
        self.state["completed_at"] = utc_now()
        self.record_event("finish_goal")


class GoalStore:
    """Load and atomically update one durable goal file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(os.path.realpath(absolute_path(path)))

    def load(self) -> GoalGraph:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise GoalError(f"state file not found: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise GoalError(f"invalid state JSON: {exc}") from exc
        graph = GoalGraph(state)
        graph.validate()
        return graph

    def create(self, graph: GoalGraph) -> None:
        with self._locked():
            if self.path.exists():
                raise GoalError(f"state file already exists: {self.path}")
            graph.validate()
            self.write(graph.state)

    def write(self, state: GoalState) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary)

    @contextmanager
    def edit(self) -> Iterator[GoalGraph]:
        with self._locked():
            graph = self.load()
            original_state = copy.deepcopy(graph.state)
            yield graph  # noqa: RUF075 - failed edits must not be committed
            if graph.state != original_state:
                graph.validate()
                graph.state["updated_at"] = utc_now()
                self.write(graph.state)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self.path.with_name(self.path.name + ".lock")
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


def parse_input(value: str) -> tuple[str, str]:
    name, separator, source = value.partition("=")
    if not separator or not name.strip() or not source.strip():
        raise argparse.ArgumentTypeError("input must use NAME=DEPENDENCY_ID")
    return name.strip(), source.strip()


def command_init(args: argparse.Namespace) -> None:
    state = args.state or default_state_path()
    graph = GoalGraph.create(args.objective, args.thread, getattr(args, "client", "codex"))
    store = GoalStore(state)
    store.create(graph)
    print_json({"state_file": os.fspath(store.path), **graph.state})


def command_add(args: argparse.Namespace) -> None:
    inputs = dict(args.input)
    if len(inputs) != len(args.input):
        raise GoalError("input names must be unique")
    with GoalStore(args.state).edit() as graph:
        before = getattr(args, "before", None)
        graph.add_node(
            args.id,
            args.title,
            args.kind,
            args.depends_on,
            before,
            args.acceptance,
            args.read_only,
            args.write_path,
            inputs,
            args.expects_artifact,
            args.reason,
        )
    print_json({"id": args.id, "status": "pending", "before": before})


def command_ready(args: argparse.Namespace) -> None:
    print_json(GoalStore(args.state).load().ready_nodes())


def command_check(args: argparse.Namespace) -> None:
    issues = GoalStore(args.state).load().lint()
    print_json({
        "ok": not any(issue["severity"] == "error" for issue in issues),
        "issues": issues,
    })


def command_events(args: argparse.Namespace) -> None:
    print_json(GoalStore(args.state).load().events)


def command_start(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.start_node(args.id, args.agent_id, args.dispatch_token)
    print_json({"id": args.id, "status": "running", "agent_id": args.agent_id})


def command_prepare_agent(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        dispatch_token = graph.prepare_agent(args.id)
    print_json({"id": args.id, "status": "dispatching", "dispatch_token": dispatch_token})


def command_abort_agent(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.abort_agent(args.id, args.dispatch_token)
    print_json({"id": args.id, "status": "pending", "dispatch_token": args.dispatch_token})


def command_wait(args: argparse.Namespace) -> None:
    require_distinct_paths(
        state=args.state,
        log_file=args.log_file,
        lock_file=args.lock_file,
        startup_file=args.startup_file,
    )
    with GoalStore(args.state).edit() as graph:
        watch_id = graph.prepare_external_wait(
            args.id,
            args.label,
            args.log_file,
            args.lock_file,
            args.startup_file,
        )
        wait = cast(WaitMetadata, graph.get_node(args.id)["wait"])
    print_json({
        "id": args.id,
        "status": "running",
        "wait": "prepared",
        "watch_id": watch_id,
        "state": absolute_path(args.state),
        "log_file": wait["log_file"],
        "lock_file": wait["lock_file"],
        "startup_file": wait["startup_file"],
    })


def command_activate_wait(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.activate_external_wait(args.id, args.watch_id)
    print_json({"id": args.id, "status": "waiting", "watch_id": args.watch_id})


def command_abort_wait(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.abort_external_wait(args.id, args.watch_id)
    print_json({"id": args.id, "status": "running", "watch_id": args.watch_id})


def command_wake(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        status, duplicate = graph.wake_node(args.id, args.event_id, args.event, args.external_status)
    print_json({"id": args.id, "status": status, "event": args.event, "duplicate": duplicate})


def command_complete(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.complete_node(args.id, args.summary, args.artifact)
    print_json({"id": args.id, "status": "completed"})


def command_fail(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.fail_node(args.id, args.summary)
    print_json({"id": args.id, "status": "failed"})


def command_retry(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.retry_node(args.id)
    print_json({"id": args.id, "status": "pending"})


def command_verify(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.verify(args.summary, args.check)
    print_json({"status": "open", "activity": "verified"})


def command_pause(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.pause()
    print_json({"status": "paused"})


def command_resume(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.resume()
    print_json({"status": "open"})


def command_cancel(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.cancel()
    print_json({"status": "cancelled"})


def command_finish(args: argparse.Namespace) -> None:
    with GoalStore(args.state).edit() as graph:
        graph.finish(args.summary)
    print_json({"status": "completed"})


def command_show(args: argparse.Namespace) -> None:
    graph = GoalStore(args.state).load()
    print_json({**graph.state, "activity": graph.activity()})


def add_state_argument(command: argparse.ArgumentParser, *, required: bool = True) -> None:
    command.add_argument(
        "--state",
        type=Path,
        required=required,
        help="Goal JSON path; init defaults to a per-project path under /tmp/.wait-goal",
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init", help="Create a new goal state")
    add_state_argument(init, required=False)
    init.add_argument("--objective", required=True)
    init.add_argument("--client", choices=sorted(CLIENTS), default="codex")
    init.add_argument(
        "--session",
        "--thread",
        dest="thread",
        help="Agent session ID used by external watchers; --thread is a compatibility alias",
    )
    init.set_defaults(handler=command_init)

    add = commands.add_parser("add", help="Add a node and optionally insert it before a pending node")
    add_state_argument(add)
    add.add_argument("--id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--kind", choices=sorted(NODE_KINDS), default="local")
    add.add_argument("--depends-on", action="append", default=[])
    add.add_argument("--before", help="Make a pending node depend on this new node")
    add.add_argument("--acceptance", action="append", default=[])
    access = add.add_mutually_exclusive_group()
    access.add_argument(
        "--read-only",
        action="store_true",
        help="Declare that the node does not write workspace files",
    )
    access.add_argument("--write-path", action="append", default=[])
    add.add_argument(
        "--input",
        action="append",
        type=parse_input,
        default=[],
        metavar="NAME=DEPENDENCY_ID",
    )
    add.add_argument("--expects-artifact", action="append", default=[])
    add.add_argument("--reason", help="Why this node was added to the graph")
    add.set_defaults(handler=command_add)

    ready = commands.add_parser("ready", help="List nodes whose dependencies are complete")
    add_state_argument(ready)
    ready.set_defaults(handler=command_ready)

    check = commands.add_parser("check", help="Report graph and scheduling issues")
    add_state_argument(check)
    check.set_defaults(handler=command_check)

    events = commands.add_parser("events", help="Print the append-only event history")
    add_state_argument(events)
    events.set_defaults(handler=command_events)

    prepare_agent = commands.add_parser("prepare-agent", help="Reserve an agent node before dispatch")
    add_state_argument(prepare_agent)
    prepare_agent.add_argument("--id", required=True)
    prepare_agent.set_defaults(handler=command_prepare_agent)

    abort_agent = commands.add_parser("abort-agent", help="Release an unassigned agent dispatch")
    add_state_argument(abort_agent)
    abort_agent.add_argument("--id", required=True)
    abort_agent.add_argument("--dispatch-token", required=True)
    abort_agent.set_defaults(handler=command_abort_agent)

    start = commands.add_parser("start", help="Mark a local/external node running or attach a prepared agent")
    add_state_argument(start)
    start.add_argument("--id", required=True)
    start.add_argument("--agent-id", help="Runtime child-agent ID; required for agent nodes")
    start.add_argument("--dispatch-token", help="Token returned by prepare-agent; required for agent nodes")
    start.set_defaults(handler=command_start)

    wait = commands.add_parser("wait", help="Prepare an external wait before watcher startup")
    add_state_argument(wait)
    wait.add_argument("--id", required=True)
    wait.add_argument("--label", required=True)
    wait.add_argument("--log-file", required=True)
    wait.add_argument("--lock-file", required=True)
    wait.add_argument("--startup-file", required=True)
    wait.set_defaults(handler=command_wait)

    activate_wait = commands.add_parser("activate-wait", help="Activate a prepared wait after watcher startup")
    add_state_argument(activate_wait)
    activate_wait.add_argument("--id", required=True)
    activate_wait.add_argument("--watch-id", required=True)
    activate_wait.set_defaults(handler=command_activate_wait)

    abort_wait = commands.add_parser("abort-wait", help="Invalidate a prepared or active external wait")
    add_state_argument(abort_wait)
    abort_wait.add_argument("--id", required=True)
    abort_wait.add_argument("--watch-id", required=True)
    abort_wait.set_defaults(handler=command_abort_wait)

    wake = commands.add_parser("wake", help="Record an idempotent watcher event")
    add_state_argument(wake)
    wake.add_argument("--id", required=True)
    wake.add_argument("--event-id", required=True)
    wake.add_argument("--event", choices=sorted(WAIT_EVENTS), required=True)
    wake.add_argument("--external-status")
    wake.set_defaults(handler=command_wake)

    complete = commands.add_parser("complete", help="Complete a verified running node")
    add_state_argument(complete)
    complete.add_argument("--id", required=True)
    complete.add_argument("--summary", required=True)
    complete.add_argument("--artifact", action="append", default=[])
    complete.set_defaults(handler=command_complete)

    fail = commands.add_parser("fail", help="Fail a node")
    add_state_argument(fail)
    fail.add_argument("--id", required=True)
    fail.add_argument("--summary", required=True)
    fail.set_defaults(handler=command_fail)

    retry = commands.add_parser("retry", help="Reset a failed node and archive its attempt")
    add_state_argument(retry)
    retry.add_argument("--id", required=True)
    retry.set_defaults(handler=command_retry)

    verify = commands.add_parser("verify", help="Record objective-level verification")
    add_state_argument(verify)
    verify.add_argument("--summary", required=True)
    verify.add_argument("--check", action="append", required=True)
    verify.set_defaults(handler=command_verify)

    for name, handler in (
        ("pause", command_pause),
        ("resume", command_resume),
        ("cancel", command_cancel),
    ):
        operation = commands.add_parser(name)
        add_state_argument(operation)
        operation.set_defaults(handler=handler)

    finish = commands.add_parser("finish", help="Complete an objectively verified goal")
    add_state_argument(finish)
    finish.add_argument("--summary", required=True)
    finish.set_defaults(handler=command_finish)

    show = commands.add_parser("show", help="Print state with derived activity")
    add_state_argument(show)
    show.set_defaults(handler=command_show)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    command_parser = parser()
    arguments = command_parser.parse_args(argv)
    try:
        arguments.handler(arguments)
    except GoalError as exc:
        command_parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
