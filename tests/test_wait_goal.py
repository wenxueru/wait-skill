from __future__ import annotations

import copy
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from argparse import Namespace
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
import wait_runtime  # noqa: E402
SCRIPT = Path(__file__).parents[1] / "src" / "wait_goal.py"
SPEC = importlib.util.spec_from_file_location("wait_goal", SCRIPT)
assert SPEC and SPEC.loader
wait_goal = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wait_goal)


class WaitGoalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = Path(self.directory.name) / "goal.json"
        self.invoke(
            wait_goal.command_init,
            state=self.state,
            objective="ship safely",
            thread="thread-1",
        )

    def invoke(self, handler: Callable[[Namespace], None], **arguments: object) -> None:
        with redirect_stdout(StringIO()):
            handler(Namespace(**arguments))

    def test_init_persists_client_and_session(self) -> None:
        state = Path(self.directory.name) / "claude.json"
        args = wait_goal.parser().parse_args([
            "init",
            "--state",
            str(state),
            "--objective",
            "ship safely",
            "--client",
            "claude",
            "--session",
            "session-1",
        ])
        self.invoke(args.handler, **{key: value for key, value in vars(args).items() if key != "handler"})

        saved = json.loads(state.read_text(encoding="utf-8"))
        self.assertEqual(saved["client"], "claude")
        self.assertEqual(saved["thread"], "session-1")

    def test_init_defaults_to_a_per_project_temporary_state(self) -> None:
        project = Path(self.directory.name) / "demo"
        nested = project / "src"
        nested.mkdir(parents=True)
        (project / ".git").mkdir()

        first = wait_goal.default_state_path(nested)
        second = wait_goal.default_state_path(project)
        self.assertEqual(first.parent, second.parent)
        self.assertEqual(first.parents[1], wait_goal.DEFAULT_STATE_ROOT)

        state = first.parent / "goal.json"
        args = wait_goal.parser().parse_args([
            "init",
            "--objective",
            "ship safely",
            "--session",
            "session-1",
        ])
        output = StringIO()
        with patch.object(wait_goal, "default_state_path", return_value=state), redirect_stdout(output):
            args.handler(args)

        self.assertTrue(state.exists())
        self.assertEqual(json.loads(output.getvalue())["state_file"], os.path.realpath(state))

    def add(
        self,
        node_id: str,
        dependencies: list[str] | None = None,
        *,
        kind: str = "local",
        before: str | None = None,
        acceptance: list[str] | None = None,
        read_only: bool | None = None,
        write_paths: list[str] | None = None,
        inputs: list[tuple[str, str]] | None = None,
        expected_artifacts: list[str] | None = None,
        reason: str | None = None,
    ) -> None:
        self.invoke(
            wait_goal.command_add,
            state=self.state,
            id=node_id,
            title=node_id,
            kind=kind,
            depends_on=dependencies or [],
            before=before,
            acceptance=acceptance or [],
            read_only=not write_paths if read_only is None else read_only,
            write_path=write_paths or [],
            input=inputs or [],
            expects_artifact=expected_artifacts or [],
            reason=reason,
        )

    def start(
        self,
        node_id: str,
        agent_id: str | None = None,
        dispatch_token: str | None = None,
    ) -> None:
        self.invoke(
            wait_goal.command_start,
            state=self.state,
            id=node_id,
            agent_id=agent_id,
            dispatch_token=dispatch_token,
        )

    def prepare_agent(self, node_id: str) -> str:
        output = StringIO()
        with redirect_stdout(output):
            wait_goal.command_prepare_agent(Namespace(state=self.state, id=node_id))
        return json.loads(output.getvalue())["dispatch_token"]

    def prepare_wait(self, node_id: str) -> str:
        root = Path(self.directory.name)
        startup_file = root / f"{node_id}.started.json"
        output = StringIO()
        with redirect_stdout(output):
            wait_goal.command_wait(
                Namespace(
                    state=self.state,
                    id=node_id,
                    label=node_id,
                    event_note="Recheck the node and continue",
                    log_file=str(root / f"{node_id}.json"),
                    lock_file=str(root / f"{node_id}.lock"),
                    startup_file=str(startup_file),
                    timeout=None,
                    remote=None,
                    program=None,
                )
            )
        response = json.loads(output.getvalue())
        wait = self.load()["nodes"][node_id]["wait"]
        for field in ("log_file", "lock_file", "startup_file"):
            self.assertEqual(response[field], wait[field])
        self.assertEqual(response["state"], str(self.state))
        args, command = wait_runtime.parse_job_args([*response["start_argv"], "--", "true"])
        self.assertEqual(command, ["true"])
        self.assertEqual(args.event_id, wait["watch_id"])
        self.assertEqual(str(args.goal_state), response["state"])
        self.assertEqual(args.goal_node, node_id)
        self.assertEqual(str(args.log_file), wait["log_file"])
        self.assertEqual(str(args.lock_file), wait["lock_file"])
        self.assertEqual(str(args.startup_file), wait["startup_file"])
        self.assertEqual(args.client, wait["client"])
        self.assertEqual(args.thread, wait["thread"])
        return wait["watch_id"]

    def write_startup_receipt(self, node_id: str, watch_id: str) -> None:
        startup_file = Path(self.directory.name) / f"{node_id}.started.json"
        wait = self.load()["nodes"][node_id]["wait"]
        receipt = {
            "event": "watcher_started",
            "event_id": watch_id,
            "goal_node": node_id,
            "thread": wait["thread"],
            "log_file": wait["log_file"],
            "lock_file": wait["lock_file"],
            "activation_deadline": time.time() + 60,
        }
        startup_file.write_text(json.dumps(receipt) + "\n", encoding="utf-8")

    def wait(self, node_id: str) -> str:
        watch_id = self.prepare_wait(node_id)
        self.write_startup_receipt(node_id, watch_id)
        with patch.object(wait_goal, "lock_is_held", return_value=True):
            self.invoke(
                wait_goal.command_activate_wait,
                state=self.state,
                id=node_id,
                watch_id=watch_id,
            )
        return watch_id

    def complete(self, node_id: str, artifacts: list[str] | None = None) -> None:
        self.invoke(
            wait_goal.command_complete,
            state=self.state,
            id=node_id,
            summary=f"{node_id} complete",
            artifact=artifacts or [],
        )

    def test_dependency_and_objective_verification_control_finish(self) -> None:
        self.add("inspect")
        self.add("implement", ["inspect"])
        self.assertEqual(self.ready_ids(), ["inspect"])

        with self.assertRaises(wait_goal.GoalError):
            self.complete("implement")

        self.start("inspect")
        self.complete("inspect")
        self.assertEqual(self.ready_ids(), ["implement"])

        self.start("implement")
        self.complete("implement")
        self.assertEqual(self.graph().activity(), "awaiting_verification")

        with self.assertRaises(wait_goal.GoalError):
            self.invoke(wait_goal.command_finish, state=self.state, summary="done")

        self.invoke(
            wait_goal.command_verify,
            state=self.state,
            summary="original request satisfied",
            check=["tests pass"],
        )
        self.assertEqual(self.graph().activity(), "verified")
        self.invoke(wait_goal.command_finish, state=self.state, summary="done")
        self.assertEqual(self.load()["status"], "completed")

    def test_verification_rejects_blank_checks(self) -> None:
        self.add("work")
        self.start("work")
        self.complete("work")

        with self.assertRaises(wait_goal.GoalError):
            self.invoke(
                wait_goal.command_verify,
                state=self.state,
                summary="verified",
                check=["   "],
            )

    def test_external_wait_wakes_to_running_and_is_idempotent(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        watch_id = self.wait("deploy")
        with patch.object(wait_goal, "lock_is_held", return_value=True):
            self.assertEqual(self.graph().activity(), "waiting")

        event = {
            "state": self.state,
            "id": "deploy",
            "event_id": watch_id,
            "event": "ready",
            "external_status": "Ready",
        }
        self.invoke(wait_goal.command_wake, **event)
        state_after_first_wake = self.load()
        self.invoke(wait_goal.command_wake, **event)
        self.assertEqual(self.load(), state_after_first_wake)
        self.assertEqual(state_after_first_wake["nodes"]["deploy"]["status"], "running")

    def test_only_external_nodes_can_wait(self) -> None:
        self.add("build")
        self.start("build")
        with self.assertRaises(wait_goal.GoalError):
            self.wait("build")

    def test_wait_is_prepared_before_activation(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        watch_id = self.prepare_wait("deploy")
        node = self.load()["nodes"]["deploy"]
        self.assertEqual(node["status"], "running")
        self.assertEqual(node["wait"]["phase"], "prepared")
        for field in ("log_file", "lock_file", "startup_file"):
            self.assertTrue(Path(node["wait"][field]).is_absolute())

        with self.assertRaises(wait_goal.GoalError):
            self.invoke(
                wait_goal.command_activate_wait,
                state=self.state,
                id="deploy",
                watch_id=watch_id,
            )
        self.write_startup_receipt("deploy", watch_id)
        startup_file = Path(self.directory.name) / "deploy.started.json"
        receipt = json.loads(startup_file.read_text(encoding="utf-8"))
        receipt["thread"] = "wrong-thread"
        startup_file.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaises(wait_goal.GoalError):
            self.invoke(
                wait_goal.command_activate_wait,
                state=self.state,
                id="deploy",
                watch_id=watch_id,
            )

        self.write_startup_receipt("deploy", watch_id)
        receipt = json.loads(startup_file.read_text(encoding="utf-8"))
        receipt["activation_deadline"] = 0
        startup_file.write_text(json.dumps(receipt), encoding="utf-8")
        with self.assertRaises(wait_goal.GoalError):
            self.invoke(
                wait_goal.command_activate_wait,
                state=self.state,
                id="deploy",
                watch_id=watch_id,
            )

        self.write_startup_receipt("deploy", watch_id)
        with (
            patch.object(wait_goal, "lock_is_held", return_value=False),
            self.assertRaises(wait_goal.GoalError),
        ):
            self.invoke(
                wait_goal.command_activate_wait,
                state=self.state,
                id="deploy",
                watch_id=watch_id,
            )

        with patch.object(wait_goal, "lock_is_held", return_value=True):
            self.invoke(
                wait_goal.command_activate_wait,
                state=self.state,
                id="deploy",
                watch_id=watch_id,
            )
        node = self.load()["nodes"]["deploy"]
        self.assertEqual(node["status"], "waiting")
        self.assertEqual(node["wait"]["phase"], "active")

    def test_orphaned_wait_is_reported_by_check_and_show(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        self.wait("deploy")

        with patch.object(wait_goal, "lock_is_held", return_value=False):
            issues = self.graph().lint()
            self.assertEqual(self.graph().activity(), "orphaned_wait")

            check_output = StringIO()
            with redirect_stdout(check_output):
                wait_goal.command_check(Namespace(state=self.state))
            show_output = StringIO()
            with redirect_stdout(show_output):
                wait_goal.command_show(Namespace(state=self.state))

        self.assertEqual(
            issues,
            [{
                "code": "orphaned_wait",
                "severity": "error",
                "node_id": "deploy",
                "message": "waiting node no longer has a live watcher",
            }],
        )
        self.assertFalse(json.loads(check_output.getvalue())["ok"])
        self.assertEqual(json.loads(show_output.getvalue())["activity"], "orphaned_wait")

    def test_abort_wait_recovers_prepared_and_active_watchers(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        prepared_watch = self.prepare_wait("deploy")
        self.invoke(
            wait_goal.command_abort_wait,
            state=self.state,
            id="deploy",
            watch_id=prepared_watch,
        )
        node = self.load()["nodes"]["deploy"]
        self.assertEqual(node["status"], "running")
        self.assertIsNone(node["wait"])

        active_watch = self.wait("deploy")
        with self.assertRaises(wait_goal.GoalError):
            self.invoke(
                wait_goal.command_abort_wait,
                state=self.state,
                id="deploy",
                watch_id=prepared_watch,
            )
        self.invoke(
            wait_goal.command_abort_wait,
            state=self.state,
            id="deploy",
            watch_id=active_watch,
        )
        node = self.load()["nodes"]["deploy"]
        self.assertEqual(node["status"], "running")
        self.assertIsNone(node["wait"])
        self.assertEqual(self.load()["events"][-1]["operation"], "abort_wait")

    def test_wait_paths_must_be_distinct(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        shared = str(Path(self.directory.name) / "shared")
        with self.assertRaises(wait_goal.GoalError):
            self.invoke(
                wait_goal.command_wait,
                state=self.state,
                id="deploy",
                label="deploy",
                log_file=shared,
                lock_file=shared,
                startup_file=str(Path(self.directory.name) / "startup"),
            )
        self.assertIsNone(self.load()["nodes"]["deploy"]["wait"])

    def test_external_wait_requires_goal_thread(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        state = self.load()
        state["thread"] = None
        self.state.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(wait_goal.GoalError):
            self.prepare_wait("deploy")

    def test_paused_goal_cannot_prepare_external_wait(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        self.invoke(wait_goal.command_pause, state=self.state)

        with self.assertRaises(wait_goal.GoalError):
            self.prepare_wait("deploy")

        self.assertIsNone(self.load()["nodes"]["deploy"]["wait"])

    def wait_namespace(self, program: list[str] | None = None, **overrides: object) -> Namespace:
        fields: dict[str, object] = {
            "state": self.state,
            "id": "deploy",
            "label": "deployment",
            "event_note": "Recheck deployment and continue",
            "log_file": None,
            "lock_file": None,
            "startup_file": None,
            "timeout": 120.0,
            "remote": None,
            "program": program,
        }
        fields.update(overrides)
        return Namespace(**fields)

    def test_wait_generates_distinct_paths_when_not_given(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        output = StringIO()
        with redirect_stdout(output):
            wait_goal.command_wait(self.wait_namespace())
        response = json.loads(output.getvalue())
        wait = self.load()["nodes"]["deploy"]["wait"]
        for field in ("log_file", "lock_file", "startup_file"):
            self.assertEqual(response[field], wait[field])
        self.assertNotEqual(wait["log_file"], wait["lock_file"])
        self.assertNotEqual(wait["log_file"], wait["startup_file"])
        self.assertIn("start_argv", response)

    def test_wait_program_submits_and_verifies_receipt(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        payloads: list[dict[str, object]] = []

        def fake_request(payload: dict[str, object], socket_path: object = None, timeout: float = 5.0) -> dict[str, object]:
            payloads.append(payload)
            wait = self.load()["nodes"]["deploy"]["wait"]
            Path(wait["startup_file"]).write_text(json.dumps({
                "label": wait["label"],
                "event_id": wait["watch_id"],
                "event": "watcher_started",
                "goal_node": "deploy",
                "client": wait["client"],
                "thread": wait["thread"],
                "log_file": wait["log_file"],
                "lock_file": wait["lock_file"],
                "activation_deadline": time.time() + 60,
            }), encoding="utf-8")
            return {"ok": True, "watch_id": wait["watch_id"], "state": "active"}

        output = StringIO()
        with patch("waitctl.request", side_effect=fake_request), redirect_stdout(output):
            wait_goal.command_wait(self.wait_namespace(program=["--", "true"]))
        response = json.loads(output.getvalue())
        self.assertTrue(response["submitted"])
        self.assertEqual(response["receipt"], "verified")
        self.assertGreater(response["activation_deadline"], time.time())

        self.assertEqual(len(payloads), 1)
        payload = payloads[0]
        self.assertEqual(payload["operation"], "submit")
        argv = payload["argv"]
        assert isinstance(argv, list)
        wait = self.load()["nodes"]["deploy"]["wait"]
        for option, expected in (
            ("--event-id", wait["watch_id"]),
            ("--goal-state", response["state"]),
            ("--goal-node", "deploy"),
            ("--client", wait["client"]),
            ("--session", wait["thread"]),
            ("--label", "deployment"),
            ("--timeout", "120.0"),
        ):
            self.assertEqual(argv[argv.index(option) + 1], expected)
        for field in ("log_file", "lock_file", "startup_file"):
            self.assertEqual(argv[argv.index("--" + field.replace("_", "-")) + 1], wait[field])
        self.assertEqual(argv[argv.index("--") + 1:], ["true"])

    def test_wait_program_rolls_back_when_submission_fails(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        with patch("waitctl.request", return_value={"ok": False, "error": "boom"}), \
                self.assertRaisesRegex(wait_goal.GoalError, "boom.*rolled back"):
            wait_goal.command_wait(self.wait_namespace(program=["true"]))
        node = self.load()["nodes"]["deploy"]
        self.assertEqual(node["status"], "running")
        self.assertIsNone(node["wait"])

    def test_wait_program_rolls_back_when_receipt_never_appears(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        with (
            patch("waitctl.request", return_value={"ok": True, "watch_id": "w"}),
            patch.object(wait_goal, "RECEIPT_WAIT_SECONDS", 0.05),
            self.assertRaisesRegex(wait_goal.GoalError, "receipt did not appear"),
        ):
            wait_goal.command_wait(self.wait_namespace(program=["true"]))
        node = self.load()["nodes"]["deploy"]
        self.assertEqual(node["status"], "running")
        self.assertIsNone(node["wait"])

    def test_failed_dependency_makes_goal_blocked(self) -> None:
        self.add("build", kind="external")
        self.add("release", ["build"])
        self.start("build")
        watch_id = self.wait("build")
        self.invoke(
            wait_goal.command_wake,
            state=self.state,
            id="build",
            event_id=watch_id,
            event="terminal",
            external_status="Failed",
        )
        self.invoke(
            wait_goal.command_fail,
            state=self.state,
            id="build",
            summary="verified terminal state",
        )
        self.assertEqual(self.graph().activity(), "blocked")
        self.assertEqual(self.ready_ids(), [])

        with self.assertRaises(wait_goal.GoalError):
            self.invoke(
                wait_goal.command_fail,
                state=self.state,
                id="build",
                summary="overwrite watcher result",
            )

    def test_failed_node_can_retry_with_attempt_history(self) -> None:
        self.add("build")
        self.start("build")
        self.invoke(
            wait_goal.command_fail,
            state=self.state,
            id="build",
            summary="first attempt failed",
        )
        self.invoke(wait_goal.command_retry, state=self.state, id="build")

        node = self.load()["nodes"]["build"]
        self.assertEqual(node["status"], "pending")
        self.assertIsNone(node["result"])
        self.assertNotIn("started_at", node)
        self.assertEqual(node["attempts"][0]["result"], "first attempt failed")
        self.assertTrue(node["attempts"][0]["failed_at"])
        self.assertEqual(self.ready_ids(), ["build"])

    def test_terminal_wake_requires_root_to_decide_failure(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        watch_id = self.wait("deploy")
        self.invoke(
            wait_goal.command_wake,
            state=self.state,
            id="deploy",
            event_id=watch_id,
            event="terminal",
            external_status="Failed",
        )

        node = self.load()["nodes"]["deploy"]
        self.assertEqual(node["status"], "running")
        self.assertIsNone(node["result"])

    def test_insert_before_pending_node(self) -> None:
        self.add("release")
        self.add("review", before="release")
        self.assertEqual(self.load()["nodes"]["release"]["depends_on"], ["review"])
        self.assertEqual(self.ready_ids(), ["review"])

    def test_events_are_contiguous_and_failed_edits_are_not_recorded(self) -> None:
        self.add("inspect", reason="initial decomposition")
        before = self.load()["events"]

        with self.assertRaises(wait_goal.GoalError):
            self.add("invalid", inputs=[("source", "inspect")])

        events = self.load()["events"]
        self.assertEqual(events, before)
        self.assertEqual([event["seq"] for event in events], [1, 2])
        self.assertEqual(events[-1]["operation"], "add_node")
        self.assertEqual(events[-1]["details"]["reason"], "initial decomposition")

    def test_node_contract_requires_direct_inputs_and_expected_artifacts(self) -> None:
        self.add("inspect")
        self.add(
            "implement",
            ["inspect"],
            inputs=[("requirements", "inspect")],
            expected_artifacts=["patch"],
        )
        self.start("inspect")
        self.complete("inspect")
        self.start("implement")

        with self.assertRaises(wait_goal.GoalError):
            self.complete("implement")

        self.complete("implement", ["patch"])
        node = self.load()["nodes"]["implement"]
        self.assertEqual(node["inputs"], {"requirements": "inspect"})
        self.assertEqual(node["artifacts"], ["patch"])

    def test_ready_frontier_serializes_overlapping_write_paths(self) -> None:
        self.add("first", write_paths=["src"])
        self.add("second", write_paths=["src/module.py"])

        self.assertEqual(self.ready_ids(), ["first"])
        issues = self.graph().lint()
        self.assertEqual(issues[0]["code"], "write_path_conflict")
        self.assertEqual(issues[0]["node_id"], "second")

        self.start("first")
        self.assertEqual(self.graph().activity(), "running")
        with self.assertRaises(wait_goal.GoalError):
            self.start("second")
        self.complete("first")
        self.assertEqual(self.ready_ids(), ["second"])

    def test_non_overlapping_ready_nodes_remain_parallel(self) -> None:
        self.add("first", write_paths=["src/one.py"])
        self.add("second", write_paths=["src/two.py"])

        self.assertEqual(self.ready_ids(), ["first", "second"])

    def test_read_only_ready_nodes_remain_parallel(self) -> None:
        self.add("first")
        self.add("second")

        self.assertEqual(self.ready_ids(), ["first", "second"])

    def test_unknown_and_case_aliased_write_paths_are_serialized(self) -> None:
        self.add("a_unknown", read_only=False)
        self.add("b_known", write_paths=["src/one.py"])
        self.assertEqual(self.ready_ids(), ["a_unknown"])
        issue_codes = {issue["code"] for issue in self.graph().lint()}
        self.assertEqual(issue_codes, {"write_path_conflict", "unknown_write_scope"})

        self.start("a_unknown")
        with self.assertRaises(wait_goal.GoalError):
            self.start("b_known")

        self.complete("a_unknown")
        self.start("b_known")
        self.complete("b_known")
        self.add("upper", write_paths=["src/A.py"])
        self.add("lower", write_paths=["src/a.py"])
        self.assertEqual(len(self.ready_ids()), 1)

    def test_ready_node_id_comes_from_graph_key(self) -> None:
        self.add("work")
        graph = self.graph()
        graph.nodes["work"]["id"] = "spoofed"

        self.assertEqual(graph.ready_nodes()[0]["id"], "work")

    def test_rejects_missing_dependency_and_duplicate_node(self) -> None:
        with self.assertRaises(wait_goal.GoalError):
            self.add("later", ["missing"])
        self.add("one")
        with self.assertRaises(wait_goal.GoalError):
            self.add("one")

    def test_rejects_incomplete_state_shape(self) -> None:
        state = self.load()
        del state["created_at"]

        with self.assertRaises(wait_goal.GoalError):
            wait_goal.GoalGraph(state).validate()

        self.add("work")
        graph = self.graph()
        del graph.nodes["work"]["result"]

        with self.assertRaises(wait_goal.GoalError):
            graph.validate()

    def test_invalid_json_shapes_raise_goal_error(self) -> None:
        state = self.load()
        state["status"] = []
        with self.assertRaises(wait_goal.GoalError):
            wait_goal.GoalGraph(state).validate()

        state = self.load()
        state["events"] = [None]
        with self.assertRaises(wait_goal.GoalError):
            wait_goal.GoalGraph(state).validate()

        self.add("work")
        state = self.load()
        state["nodes"]["work"]["kind"] = {}
        with self.assertRaises(wait_goal.GoalError):
            wait_goal.GoalGraph(state).validate()

        state = self.load()
        state["nodes"]["work"]["status"] = "dispatching"
        with self.assertRaises(wait_goal.GoalError):
            wait_goal.GoalGraph(state).validate()

    def test_deep_acyclic_graph_does_not_recurse(self) -> None:
        graph = wait_goal.GoalGraph.create("deep graph", None)
        template = {
            "title": "node",
            "kind": "local",
            "depends_on": [],
            "status": "pending",
            "acceptance": [],
            "read_only": True,
            "write_paths": [],
            "inputs": {},
            "expected_artifacts": [],
            "artifacts": [],
            "result": None,
            "wait": None,
            "last_event": None,
            "assignment": None,
            "attempts": [],
        }
        for index in range(1_100):
            node = copy.deepcopy(template)
            node["depends_on"] = [str(index + 1)] if index < 1_099 else []
            graph.nodes[str(index)] = node
        graph.validate()

    def test_rejects_version_field(self) -> None:
        state = self.load()
        state["version"] = 2

        with self.assertRaises(wait_goal.GoalError):
            wait_goal.GoalGraph(state)

    def test_stale_event_cannot_wake_a_later_wait_cycle(self) -> None:
        self.add("deploy", kind="external")
        self.start("deploy")
        first_watch = self.wait("deploy")
        self.invoke(
            wait_goal.command_wake,
            state=self.state,
            id="deploy",
            event_id=first_watch,
            event="ready",
            external_status="Ready",
        )
        second_watch = self.wait("deploy")
        self.invoke(
            wait_goal.command_wake,
            state=self.state,
            id="deploy",
            event_id=second_watch,
            event="ready",
            external_status="Ready",
        )
        third_watch = self.wait("deploy")

        with self.assertRaises(wait_goal.GoalError):
            self.invoke(
                wait_goal.command_wake,
                state=self.state,
                id="deploy",
                event_id=first_watch,
                event="terminal",
                external_status="Failed",
            )

        node = self.load()["nodes"]["deploy"]
        self.assertEqual(node["status"], "waiting")
        self.assertEqual(node["wait"]["watch_id"], third_watch)

    def test_agent_start_requires_and_persists_assignment(self) -> None:
        self.add("review", kind="agent")
        with self.assertRaises(wait_goal.GoalError):
            self.start("review")

        dispatch_token = self.prepare_agent("review")
        self.assertEqual(self.load()["nodes"]["review"]["status"], "dispatching")
        with self.assertRaises(wait_goal.GoalError):
            self.start("review", "agent-42", "wrong-token")
        self.start("review", "agent-42", dispatch_token)
        assignment = self.load()["nodes"]["review"]["assignment"]
        self.assertEqual(assignment["agent_id"], "agent-42")
        self.assertEqual(assignment["dispatch_token"], dispatch_token)
        self.assertEqual(assignment["phase"], "active")
        self.assertTrue(assignment["assigned_at"])
        self.complete("review")
        state = self.load()
        state["nodes"]["review"]["assignment"] = None
        with self.assertRaises(wait_goal.GoalError):
            wait_goal.GoalGraph(state).validate()

    def test_prepared_agent_dispatch_can_be_reconciled_or_aborted(self) -> None:
        self.add("review", kind="agent")
        dispatch_token = self.prepare_agent("review")
        self.assertEqual(self.graph().activity(), "dispatching")
        self.assertEqual(self.ready_ids(), [])

        with self.assertRaises(wait_goal.GoalError):
            self.invoke(
                wait_goal.command_abort_agent,
                state=self.state,
                id="review",
                dispatch_token="wrong-token",
            )
        self.invoke(
            wait_goal.command_abort_agent,
            state=self.state,
            id="review",
            dispatch_token=dispatch_token,
        )
        self.assertEqual(self.load()["nodes"]["review"]["status"], "pending")
        self.assertEqual(self.ready_ids(), ["review"])

    def test_prepared_dispatch_blocks_other_ready_nodes(self) -> None:
        self.add("first", kind="agent")
        self.add("second", kind="agent")

        self.prepare_agent("first")

        self.assertEqual(self.graph().activity(), "dispatching")
        self.assertEqual(self.ready_ids(), [])
        with self.assertRaises(wait_goal.GoalError):
            self.prepare_agent("second")

    def test_cancelled_prepared_dispatch_retains_reconciliation_token(self) -> None:
        self.add("review", kind="agent")
        dispatch_token = self.prepare_agent("review")

        self.invoke(wait_goal.command_cancel, state=self.state)

        node = self.load()["nodes"]["review"]
        self.assertEqual(node["status"], "cancelled")
        self.assertEqual(node["assignment"]["phase"], "prepared")
        self.assertEqual(node["assignment"]["dispatch_token"], dispatch_token)

    def test_completed_states_require_results_and_timestamps(self) -> None:
        self.add("work")
        self.start("work")
        self.complete("work")
        state = self.load()
        state["nodes"]["work"]["result"] = None
        with self.assertRaises(wait_goal.GoalError):
            wait_goal.GoalGraph(state).validate()

        state = self.load()
        self.invoke(
            wait_goal.command_verify,
            state=self.state,
            summary="verified",
            check=["tests pass"],
        )
        state = self.load()
        state["status"] = "completed"
        with self.assertRaises(wait_goal.GoalError):
            wait_goal.GoalGraph(state).validate()

    def test_locked_state_does_not_commit_after_an_exception(self) -> None:
        with self.assertRaises(RuntimeError):
            with wait_goal.GoalStore(self.state).edit() as graph:
                graph.state["status"] = "cancelled"
                raise RuntimeError("abort transaction")
        self.assertEqual(self.load()["status"], "open")

    def test_concurrent_initialization_has_one_winner(self) -> None:
        state = Path(self.directory.name) / "concurrent.json"

        def create() -> bool:
            try:
                wait_goal.GoalStore(state).create(wait_goal.GoalGraph.create("goal", None))
            except wait_goal.GoalError:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(create) for _ in range(2)]
            results = [future.result() for future in futures]

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(wait_goal.GoalStore(state).load().state["objective"], "goal")

    def graph(self) -> wait_goal.GoalGraph:
        return wait_goal.GoalStore(self.state).load()

    def load(self) -> dict[str, object]:
        return self.graph().state

    def ready_ids(self) -> list[str]:
        return [node["id"] for node in self.graph().ready_nodes()]


if __name__ == "__main__":
    unittest.main()
