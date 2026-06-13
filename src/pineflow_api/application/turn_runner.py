"""In-process execution for routed PineFlow turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.core.messages import set_locale
from pineflow_agent.core.state_tree import GISStateTree
from pineflow_agent.core.workspace import WorkspaceContext
from pineflow_agent.core.file_state import workspace_file_state

from pineflow_api.contracts.models import QGISAgentRequest
from pineflow_api.persistence.session_state import TurnContext, turn_context_from_run_snapshot
from pineflow_api.routing.turn_routing import TurnRoute

EventSink = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class TurnExecutionResult:
    """Result payload plus the message text that should be stored for the turn."""

    result_payload: dict[str, Any]
    message_content: str


class TurnRunner:
    """Execute one routed turn without owning session storage or API transport."""

    def __init__(
        self,
        *,
        runtime_factory: Callable[..., Any],
        toolbox_factory: Callable[..., Any],
        agent_factory: Callable[[QGISAgentRequest, Any], Any],
        apply_qgis_environment: Callable[[QGISAgentRequest], None],
        preload_sources: Callable[..., list[str]],
        run_snapshot_loader: Callable[[str], dict[str, Any]],
        workspace: WorkspaceContext | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._toolbox_factory = toolbox_factory
        self._agent_factory = agent_factory
        self._apply_qgis_environment = apply_qgis_environment
        self._preload_sources = preload_sources
        self._run_snapshot_loader = run_snapshot_loader
        self._workspace = workspace or WorkspaceContext()

    def run(self, route: TurnRoute, request: QGISAgentRequest, emit: EventSink) -> TurnExecutionResult:
        try:
            set_locale(request.options.locale)
            self._apply_qgis_environment(request)
            if route.kind == "new_session":
                result_payload = self._run_new_session(route, request, emit)
            elif route.kind == "continue_session":
                result_payload = self._run_continuation(route, request, emit)
            elif route.kind == "pending_reply":
                result_payload = self._run_pending_reply(route, request, emit)
            elif route.kind == "structured_resume":
                result_payload = self._run_structured_resume(route, request, emit)
            else:  # pragma: no cover - guarded by TurnKind
                raise ValueError(f"Unsupported turn route: {route.kind}")
        except Exception as exc:
            result_payload = self._failed_result(route, exc)
            emit(
                {
                    "event": "failed",
                    "message": str(exc),
                    "session_id": route.session_id,
                    "result": result_payload,
                }
            )
        return TurnExecutionResult(
            result_payload=make_json_safe(dict(result_payload)),
            message_content=route.message_content or request.message,
        )

    def _run_new_session(
        self,
        route: TurnRoute,
        request: QGISAgentRequest,
        emit: EventSink,
    ) -> dict[str, Any]:
        emit(
            {
                "event": "session",
                "message": "PineFlow session started.",
                "session_id": route.session_id,
                "sources": [source.model_dump() for source in request.sources],
            }
        )
        toolbox = self._new_toolbox(request, route.session_id)
        try:
            preload_logs = self._preload_sources(toolbox, request, emit, context={"phase": "new_session"})
            agent = self._agent_factory(request, toolbox)
            result = agent.run(
                request.message,
                session_id=route.session_id,
                on_event=emit,
                goal_contract=dict(request.options.goal_contract or {}),
                run_context=dict(request.options.run_context or {}),
            )
            return self._with_preload_logs(self._with_toolbox_artifacts(result.to_dict(), toolbox), preload_logs)
        finally:
            self._shutdown_toolbox_runtime(toolbox)

    def _run_continuation(
        self,
        route: TurnRoute,
        request: QGISAgentRequest,
        emit: EventSink,
    ) -> dict[str, Any]:
        emit(
            {
                "event": "session",
                "message": "PineFlow session continued.",
                "session_id": route.session_id,
                "sources": [source.model_dump() for source in request.sources],
            }
        )
        turn = self._restore_turn_context(route)
        toolbox = self._restored_toolbox(request, route.session_id, turn)
        try:
            preload_logs = self._preload_sources(toolbox, request, emit, skip_existing=True, context={"phase": "continue_session"})
            agent = self._agent_factory(request, toolbox)
            result = agent.run(
                request.message,
                session_id=route.session_id,
                on_event=emit,
                prior_steps=list(turn.prior_steps),
                goal_contract=dict(request.options.goal_contract or {}),
                run_context=dict(request.options.run_context or {}),
            )
            return self._with_preload_logs(self._with_toolbox_artifacts(result.to_dict(), toolbox), preload_logs)
        finally:
            self._shutdown_toolbox_runtime(toolbox)

    def _run_pending_reply(
        self,
        route: TurnRoute,
        request: QGISAgentRequest,
        emit: EventSink,
    ) -> dict[str, Any]:
        turn = self._restore_turn_context(route)
        toolbox = self._restored_toolbox(request, route.session_id, turn)
        try:
            preload_logs = self._preload_sources(
                toolbox,
                request,
                emit,
                skip_existing=True,
                context=self._resume_preload_context(turn.pending_task, action="answer"),
            )
            agent = self._agent_factory(request, toolbox)
            result = self._resume_pending_task(
                agent,
                action="reply",
                user_reply=str(route.payload.get("user_reply") or request.message),
                pending_task=dict(turn.pending_task),
                session_id=route.session_id,
                on_event=emit,
                prior_steps=list(turn.prior_steps),
            )
            return self._with_preload_logs(self._with_toolbox_artifacts(result.to_dict(), toolbox), preload_logs)
        finally:
            self._shutdown_toolbox_runtime(toolbox)

    def _run_structured_resume(
        self,
        route: TurnRoute,
        request: QGISAgentRequest,
        emit: EventSink,
    ) -> dict[str, Any]:
        turn = self._restore_turn_context(route)
        toolbox = self._restored_toolbox(request, route.session_id, turn)
        try:
            agent = self._agent_factory(request, toolbox)
            action = str(route.payload.get("action") or "")
            pending_task = dict(turn.pending_task)
            repair = dict(turn.repair)
            preload_logs: list[str] = []
            if action not in {"reject", "cancel"}:
                preload_logs = self._preload_sources(
                    toolbox,
                    request,
                    emit,
                    skip_existing=True,
                    context=self._resume_preload_context(pending_task, action=action),
                )
            result = self._resume_pending_task(
                agent,
                action=action,
                pending_task=pending_task,
                repair=repair,
                slot_patch=dict(route.payload.get("slot_patch") or {}),
                user_reply=str(route.payload.get("user_reply") or request.message),
                user_request=str(route.payload.get("message") or request.message or ""),
                session_id=route.session_id,
                on_event=emit,
                prior_steps=list(turn.prior_steps),
            )
            return self._with_preload_logs(self._with_toolbox_artifacts(result.to_dict(), toolbox), preload_logs)
        finally:
            self._shutdown_toolbox_runtime(toolbox)

    def _new_toolbox(self, request: QGISAgentRequest, session_id: str) -> Any:
        return self._toolbox_factory(
            runtime=self._runtime_factory(prefix_path=request.qgis.prefix_path),
            session_id=session_id,
            workspace=self._workspace,
        )

    def _restored_toolbox(self, request: QGISAgentRequest, session_id: str, turn: TurnContext) -> Any:
        state = GISStateTree.from_dict(dict(turn.state_tree or {}))
        return self._toolbox_factory(
            runtime=self._runtime_factory(prefix_path=request.qgis.prefix_path),
            state=state,
            session_id=session_id,
            workspace=self._workspace,
        )

    def _restore_turn_context(self, route: TurnRoute) -> TurnContext:
        run_id = str(route.restore_run_id or "").strip()
        if run_id:
            snapshot = self._run_snapshot_loader(run_id)
            restored = turn_context_from_run_snapshot(snapshot)
            if restored.state_tree or restored.prior_steps or restored.pending_task or restored.repair:
                return restored
        return route.session_state.turn_context(message=route.message_content, user_reply=str(route.payload.get("user_reply") or ""))

    @staticmethod
    def _resume_pending_task(agent: Any, **kwargs: Any) -> Any:
        resume_pending = getattr(agent, "resume_pending_task", None)
        if callable(resume_pending):
            return resume_pending(**kwargs)
        action = str(kwargs.get("action") or "").strip()
        if action == "reply":
            return agent.resume_with_user_reply(
                user_reply=str(kwargs.get("user_reply") or ""),
                pending_task=dict(kwargs.get("pending_task") or {}),
                session_id=str(kwargs.get("session_id") or ""),
                on_event=kwargs.get("on_event"),
                prior_steps=list(kwargs.get("prior_steps") or []),
            )
        if action == "patch":
            return agent.resume_with_slot_patch(
                pending_task=dict(kwargs.get("pending_task") or {}),
                slot_patch=dict(kwargs.get("slot_patch") or {}),
                session_id=str(kwargs.get("session_id") or ""),
                on_event=kwargs.get("on_event"),
                prior_steps=list(kwargs.get("prior_steps") or []),
            )
        if action == "confirm":
            return agent.run_confirmed_repair(
                pending_task=dict(kwargs.get("pending_task") or {}),
                repair=dict(kwargs.get("repair") or {}),
                session_id=str(kwargs.get("session_id") or ""),
                on_event=kwargs.get("on_event"),
                prior_steps=list(kwargs.get("prior_steps") or []),
            )
        if action == "reject":
            return agent.reject_pending_repair(
                pending_task=dict(kwargs.get("pending_task") or {}),
                repair=dict(kwargs.get("repair") or {}),
                session_id=str(kwargs.get("session_id") or ""),
                on_event=kwargs.get("on_event"),
            )
        if action == "cancel":
            return agent.cancel_pending_task(
                pending_task=dict(kwargs.get("pending_task") or {}),
                session_id=str(kwargs.get("session_id") or ""),
                on_event=kwargs.get("on_event"),
            )
        if action == "replan":
            return agent.resume_with_replanned_request(
                user_request=str(kwargs.get("user_request") or ""),
                pending_task=dict(kwargs.get("pending_task") or {}),
                session_id=str(kwargs.get("session_id") or ""),
                on_event=kwargs.get("on_event"),
                prior_steps=list(kwargs.get("prior_steps") or []),
            )
        raise ValueError(f"Unsupported pending action: {action}")

    @staticmethod
    def _resume_preload_context(pending_task: dict[str, Any], *, action: str) -> dict[str, Any]:
        payload = dict(pending_task or {})
        return {
            "phase": "resume",
            "resume_action": str(action or "").strip(),
            "active_intent": str(payload.get("active_intent") or "").strip(),
            "source_requests": [dict(item) for item in list(payload.get("source_requests") or []) if isinstance(item, dict)],
            "missing_slots": [str(item) for item in list(payload.get("missing_slots") or []) if str(item or "").strip()],
            "pending_kind": str(payload.get("pending_kind") or "").strip(),
            "awaiting_state": str(payload.get("awaiting_state") or "").strip(),
        }

    @staticmethod
    def _with_preload_logs(result_payload: dict[str, Any], preload_logs: list[str]) -> dict[str, Any]:
        payload = dict(result_payload)
        payload["logs"] = list(payload.get("logs") or []) + list(preload_logs)
        return payload

    @staticmethod
    def _with_toolbox_artifacts(result_payload: dict[str, Any], toolbox: Any) -> dict[str, Any]:
        payload = dict(result_payload)
        artifact_index = getattr(toolbox, "artifacts", None)
        workspace = getattr(toolbox, "workspace", None)
        if workspace is not None and artifact_index is not None:
            payload["file_state"] = workspace_file_state(workspace, artifacts=artifact_index)
        if artifact_index is None or not hasattr(artifact_index, "outputs"):
            return payload
        try:
            outputs = [dict(item) for item in list(artifact_index.outputs() or []) if isinstance(item, dict)]
        except Exception:
            return payload
        if outputs:
            payload["outputs"] = outputs
        return payload

    @staticmethod
    def _shutdown_toolbox_runtime(toolbox: Any) -> None:
        runtime = getattr(toolbox, "runtime", None)
        shutdown = getattr(runtime, "shutdown", None)
        if callable(shutdown):
            try:
                shutdown()
            except Exception:
                pass

    @staticmethod
    def _failed_result(route: TurnRoute, exc: Exception) -> dict[str, Any]:
        preserve_context = route.kind == "continue_session"
        turn = route.session_state.turn_context()
        return {
            "session_id": route.session_id,
            "status": "failed",
            "react_trace": list(turn.prior_steps) if preserve_context else [],
            "state_tree": dict(turn.state_tree) if preserve_context else {},
            "outputs": [],
            "logs": [],
            "errors": [str(exc)],
            "next_question": "",
        }
