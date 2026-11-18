"""Deliver saved wake events to their owning sessions."""

from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import Callable
from contextlib import nullcontext
from weakref import WeakValueDictionary

from .. import runtime
from . import process

DELIVERED_NOTIFICATIONS = frozenset({"started", "queued", "completed", "native_pending"})


class WakeDelivery:
    """Own session serialization, delivery attempts and their receipts."""

    def __init__(self) -> None:
        self._slots = WeakValueDictionary[str, asyncio.Lock]()

    def _delivery_slot(self, args: argparse.Namespace) -> asyncio.Lock | nullcontext[None]:
        if args.client != "codex":
            return nullcontext()
        key = args.thread
        if (slot := self._slots.get(key)) is None:
            slot = self._slots[key] = asyncio.Lock()
        return slot

    @staticmethod
    def _notification_command(args: argparse.Namespace, message: str) -> list[str]:
        remote = runtime.resolve_codex_remote(args.remote) if args.client == "codex" else args.remote
        return runtime.notification_command(args.client, args.thread, message, remote, args.resume_args)

    async def deliver(
        self,
        args: argparse.Namespace,
        result: dict[str, object],
        cwd: str,
        cancel_event: asyncio.Event,
        persist: Callable[[], None],
    ) -> int | None:
        notification = result.get("notification")
        if notification in DELIVERED_NOTIFICATIONS:
            return None
        if notification == "attempting":
            # A restarted waiter may have sent turn/start before losing the receipt.
            result["notification"] = "unconfirmed"
            persist()
            return runtime.EXIT_NOTIFY_FAILED
        if notification in {"unconfirmed", "failed"}:
            return runtime.EXIT_NOTIFY_FAILED
        if notification == "cancelled":
            return runtime.EXIT_ACTIVATION_CANCELLED
        if args.client == "claude":
            result.update(notification="native_pending", notification_attempts=0, available_at=time.time())
            persist()
            return None

        max_failures, timeout = runtime.notification_limits(args)
        attempts = int(result.get("notification_attempts", 0))
        # Older persisted rejections used the total attempt count.
        failures = int(result.get("notification_failures", attempts if result.get("notification") == "failed_retrying" else 0))
        delivery_slot = self._delivery_slot(args)
        while True:
            retry_at = result.get("next_notification_at")
            if retry_at is not None:
                await process.wait_for_cancel(cancel_event, max(0.0, float(retry_at) - time.time()))

            async with delivery_slot:
                if cancel_event.is_set() or not runtime.watch_is_current(args):
                    result.update(notification="cancelled")
                    result.pop("next_notification_at", None)
                    return runtime.EXIT_ACTIVATION_CANCELLED

                attempts += 1
                result.pop("next_notification_at", None)
                result.update(notification="attempting", notification_attempts=attempts)
                persist()

                status = await self._attempt(args, result, cwd, timeout)
                if status in {"started", "completed"}:
                    result.update(notification=status, delivered_at=time.time())
                    persist()
                    return None

                if status == "failed":
                    failures += 1
                    result["notification_failures"] = failures
                retry = status in {"busy", "notification_unavailable"} or (status == "failed" and failures < max_failures)
                if retry:
                    delay = min(args.notification_retry_interval * 2 ** min(attempts - 1, 10), 300.0)
                    result.update(notification=f"{status}_retrying", next_notification_at=time.time() + delay)
                else:
                    result["notification"] = "failed" if status == "launch_failed" else status
                persist()
                if not retry:
                    return runtime.EXIT_NOTIFY_FAILED

    async def _attempt(self, args: argparse.Namespace, result: dict[str, object], cwd: str, timeout: float) -> str:
        message = runtime.resume_instruction(args)
        if result.get("event") == "watcher_failed":
            message += "; event=watcher_failed"
        try:
            command = self._notification_command(args, message)
        except runtime.NotificationUnavailable as exc:
            result["notification_stderr"] = str(exc)
            return "launch_failed"
        outcome = await process.run_program(command, timeout, cwd)
        result.update(
            notification_exit_code=outcome["exit_code"],
            notification_stdout=outcome["stdout"],
            notification_stderr=outcome["stderr"] or outcome.get("error", ""),
        )
        if outcome["event"] == "start_failed":
            return "launch_failed"
        if outcome.get("output_truncated"):
            result["notification_output_truncated"] = True
        if outcome["event"] == "timeout":
            return "unconfirmed"
        code = outcome["exit_code"]
        if code == 0:
            return runtime.notification_success(args.client)
        if args.client != "codex" or code == runtime.EXIT_NOTIFY_REJECTED:
            return "failed"
        if code == runtime.EXIT_NOTIFY_FAILED:
            return "notification_unavailable"
        if code == runtime.EXIT_NOTIFY_BUSY:
            return "busy"
        # A killed helper or an unknown exit may follow an accepted turn/start.
        return "unconfirmed"
