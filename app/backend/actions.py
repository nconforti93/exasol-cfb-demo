"""In-process action status for refreshes whose database run may outlive an HTTP request."""

from __future__ import annotations

import secrets
import threading
from datetime import UTC, datetime
from typing import Any

MAX_ACTIONS = 100


class RefreshBusy(RuntimeError):
    """One refresh already owns the process's single flight."""


class ActionStore:
    """Short-lived status only; the database timeline survives process restarts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._actions: dict[str, dict[str, Any]] = {}
        self._active: str | None = None

    def active(self) -> str | None:
        with self._lock:
            return self._active

    def begin(self, point_before: dict[str, Any] | None) -> str:
        with self._lock:
            if self._active is not None:
                raise RefreshBusy(f"refresh action {self._active} is already running")
            action_id = secrets.token_hex(12)
            self._actions[action_id] = {
                "action_id": action_id,
                "kind": "refresh",
                "state": "RUNNING",
                "run_id": None,
                "started_at": datetime.now(UTC).isoformat(),
                "ended_at": None,
                "error": None,
                "calls_spent": None,
                "point_before": point_before,
                "point_after": None,
                "point_message": None,
            }
            self._active = action_id
            while len(self._actions) > MAX_ACTIONS:
                oldest = next(iter(self._actions))
                if oldest == self._active:
                    break
                del self._actions[oldest]
            return action_id

    def update(self, action_id: str, **values: Any) -> None:
        with self._lock:
            self._actions[action_id].update(values)

    def finish(self, action_id: str, *, error: str | None, **values: Any) -> None:
        with self._lock:
            self._actions[action_id].update(
                state="FAILED" if error else "DONE",
                ended_at=datetime.now(UTC).isoformat(),
                error=error,
                **values,
            )
            if self._active == action_id:
                self._active = None

    def get(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._actions.get(action_id)
            return None if found is None else dict(found)
