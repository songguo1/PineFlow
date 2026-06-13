"""Application commands for creating and controlling execution runs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from pineflow_api.application.run_runtime import RunManager
from pineflow_api.application.run_service import RunContext, RunService
from pineflow_api.contracts.models import QGISAgentRequest
from pineflow_api.contracts.run_control import (
    PENDING_CONTROL_ACTIONS,
    RUN_CONTROL_ACTIONS,
    RunControlAction,
    normalize_run_control_result,
)
from pineflow_api.routing.turn_intent import TurnIntent
from pineflow_api.routing.turn_routing import SessionRouter, TurnRoute


PreparedRunExecutor = Callable[[TurnRoute, QGISAgentRequest, RunContext, TurnIntent | None], dict]
IntentResolver = Callable[[TurnRoute, QGISAgentRequest], TurnIntent | None]
RunErrorHandler = Callable[[RunContext, TurnRoute, Exception], None]
RunBootstrapper = Callable[[TurnRoute, QGISAgentRequest, RunContext, TurnIntent | None], None]


@dataclass(frozen=True)
class PreparedRun:
    route: TurnRoute
    request: QGISAgentRequest
    run: RunContext
    intent: TurnIntent | None


class RunCommandError(RuntimeError):
    """Raised when a run command cannot be applied."""


class RunNotFoundError(RunCommandError):
    """Raised when a run id is not known to the runtime."""


class RunCommandService:
    """Owns application-level run commands, independent from HTTP routing."""

    def __init__(
        self,
        *,
        router: SessionRouter,
        runs: RunService,
        run_manager: RunManager,
        resolve_intent: IntentResolver,
        execute_prepared: PreparedRunExecutor,
        handle_error: RunErrorHandler,
        bootstrap_prepared: RunBootstrapper | None = None,
    ) -> None:
        self.router = router
        self.runs = runs
        self.run_manager = run_manager
        self.resolve_intent = resolve_intent
        self.execute_prepared = execute_prepared
        self.handle_error = handle_error
        self.bootstrap_prepared = bootstrap_prepared

    def prepare(self, request: QGISAgentRequest) -> PreparedRun:
        route = self.router.route(request, self.runs.sessions.get)
        intent = self.resolve_intent(route, request)
        run = self.runs.begin(
            route.session_id,
            route_kind=route.kind,
            message=route.message_content or request.message,
        )
        request = self._attach_run_context(request, run, route, intent)
        if self.bootstrap_prepared is not None:
            self.bootstrap_prepared(route, request, run, intent)
        return PreparedRun(route=route, request=request, run=run, intent=intent)

    def create_run(self, request: QGISAgentRequest) -> dict:
        prepared = self.prepare(request)
        return self._start(prepared)

    def resume_run(self, run_id: str, request: QGISAgentRequest) -> dict:
        existing_run = self.runs.get(run_id)
        if not existing_run:
            raise RunNotFoundError("Run does not exist.")
        session_id = str(existing_run.get("session_id") or request.session_id or "")
        if not session_id:
            raise RunCommandError("Run has no session_id.")
        self.run_manager.mark_resumed(run_id)
        return self.create_run(request.model_copy(update={"session_id": session_id}))

    def apply_control_action(self, run_id: str, action: RunControlAction) -> dict:
        normalized_run_id = action.validate_run_id(run_id)
        action_type = str(action.action_type or "").strip()
        if action_type in RUN_CONTROL_ACTIONS:
            if action_type == "run.pause":
                run = self.request_pause(normalized_run_id)
            else:
                run = self.request_cancel(normalized_run_id)
            return normalize_run_control_result(
                {
                    "run_id": normalized_run_id,
                    "session_id": run.get("session_id") or "",
                    "ok": True,
                    "action_type": action_type,
                    "run": run,
                    "next_run_id": normalized_run_id,
                },
                action_type=action_type,
                fallback_run_id=normalized_run_id,
            )
        if action_type in PENDING_CONTROL_ACTIONS:
            self._validate_pending_id(normalized_run_id, action)
        request = action.to_resume_request(run_id=normalized_run_id)
        summary = self.resume_run(normalized_run_id, request)
        summary["ok"] = True
        summary["action_type"] = action_type
        summary["next_run_id"] = summary.get("run_id") or normalized_run_id
        return normalize_run_control_result(summary, action_type=action_type, fallback_run_id=normalized_run_id)

    def request_pause(self, run_id: str) -> dict:
        run = self.run_manager.request_pause(run_id)
        if not run:
            raise RunNotFoundError("No active run found.")
        return run

    def request_cancel(self, run_id: str) -> dict:
        run = self.run_manager.request_cancel(run_id)
        if not run:
            raise RunNotFoundError("No active run found.")
        return run

    def _start(self, prepared: PreparedRun) -> dict:
        def execute() -> dict:
            return self.execute_prepared(prepared.route, prepared.request, prepared.run, prepared.intent)

        def on_error(exc: Exception) -> None:
            self.handle_error(prepared.run, prepared.route, exc)

        summary = self.run_manager.start(prepared.run, execute=execute, on_error=on_error)
        summary["route_kind"] = prepared.route.kind
        return summary

    @staticmethod
    def _attach_run_context(
        request: QGISAgentRequest,
        run: RunContext,
        route: TurnRoute,
        intent: TurnIntent | None,
    ) -> QGISAgentRequest:
        del route, intent
        context = {"source_run_id": run.run_id, "status": "executed"}
        options = request.options.model_copy(update={"run_context": context})
        return request.model_copy(update={"options": options})

    def _validate_pending_id(self, run_id: str, action: RunControlAction) -> None:
        pending_id = str(action.pending_id or "").strip()
        if not pending_id:
            return
        snapshot = self.runs.sessions.get_run_snapshot(run_id)
        current_pending = _current_pending_task(snapshot)
        current_pending_id = str(current_pending.get("pending_id") or "").strip()
        if current_pending_id != pending_id:
            raise RunCommandError("Pending task is no longer current.")


def _current_pending_task(snapshot: dict) -> dict:
    pending = snapshot.get("pending_task") if isinstance(snapshot, dict) else {}
    if isinstance(pending, dict) and pending:
        return pending
    result = snapshot.get("result") if isinstance(snapshot, dict) else {}
    if isinstance(result, dict):
        result_pending = result.get("pending_task")
        if isinstance(result_pending, dict) and result_pending:
            return result_pending
    return {}
