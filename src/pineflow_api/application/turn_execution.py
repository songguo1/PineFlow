"""Application service for executing routed PineFlow turns."""

from __future__ import annotations

from typing import Any, Callable

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.core.messages import get_locale
from pineflow_agent.orchestration.agent.goal_contract import infer_goal_contract
from pineflow_agent.orchestration.agent.report_audit import build_report_audit_from_payload
from pineflow_agent.tools.contracts.tool_definitions import display_title_for_action
from pineflow_api.application.completion_summary import apply_completion_summary
from pineflow_api.application.execution import preload_sources
from pineflow_api.application.run_service import RunContext, RunService
from pineflow_api.application.state_answers import TurnResponseBuilder
from pineflow_api.application.turn_runner import TurnRunner
from pineflow_api.contracts.models import QGISAgentRequest
from pineflow_api.contracts.decision_rows import build_decision_rows
from pineflow_api.contracts.pending_tasks import normalize_pending_task
from pineflow_api.contracts.run_lifecycle import RunStatus
from pineflow_api.contracts.run_snapshots import normalize_run_snapshot
from pineflow_api.contracts.transcript_projection import (
    append_report_artifact_summary,
    append_transcript_item,
    build_transcript_projection,
    merge_transcript_projection,
    transcript_item_from_event,
)
from pineflow_api.persistence.session_state import SessionState, append_user_message_transcript
from pineflow_api.persistence.sessions import SessionStore
from pineflow_api.routing.turn_intent import TurnIntent
from pineflow_api.routing.turn_routing import TurnRoute

EventSink = Callable[[dict[str, Any]], None]


class TurnExecutionService:
    """Executes prepared turns and persists their API-facing session result."""

    def __init__(
        self,
        *,
        sessions: SessionStore,
        runs: RunService,
        runtime_factory_for_request: Callable[[QGISAgentRequest], Callable[..., Any]],
        toolbox_factory: Callable[..., Any],
        agent_factory: Callable[[QGISAgentRequest, Any], Any],
        apply_qgis_environment: Callable[[QGISAgentRequest], None],
        run_snapshot_loader: Callable[[str], dict[str, Any]],
        intent_responses: TurnResponseBuilder | None = None,
    ) -> None:
        self.sessions = sessions
        self.runs = runs
        self.runtime_factory_for_request = runtime_factory_for_request
        self.toolbox_factory = toolbox_factory
        self.agent_factory = agent_factory
        self.apply_qgis_environment = apply_qgis_environment
        self.run_snapshot_loader = run_snapshot_loader
        self.intent_responses = intent_responses or TurnResponseBuilder()

    def run_prepared(
        self,
        route: TurnRoute,
        request: QGISAgentRequest,
        run: RunContext,
        intent: TurnIntent | None,
        *,
        on_event: EventSink | None = None,
    ) -> dict[str, Any]:
        if intent is not None:
            return self._run_intent_response(route, request, intent, run=run, on_event=on_event)
        return self._run_route_in_process(route, request, run=run, on_event=on_event)

    def bootstrap_prepared_run(
        self,
        route: TurnRoute,
        request: QGISAgentRequest,
        run: RunContext,
        intent: TurnIntent | None,
    ) -> None:
        session_state = self._bootstrap_session_state(route, intent)
        message_content = route.message_content or request.message
        request_payload = _redacted_request_payload(request)
        run.request_payload = make_json_safe(dict(request_payload))
        run.initial_user_message = str(message_content or "")
        self.sessions.save(
            self._build_staged_session_payload(
                session_state,
                run_id=run.run_id,
                route=route,
                request_payload=request_payload,
                message_content=message_content,
            ),
            run_id=run.run_id,
        )
        initial_result = self.decorate_result_payload(
            route.session_id,
            self._build_initial_run_result(
                session_state,
                run_id=run.run_id,
                route=route,
                message_content=message_content,
                request=request,
            ),
            event_count=0,
        )
        run.current_result = make_json_safe(dict(initial_result))
        self._save_run_snapshot(
            run.run_id,
            route.session_id,
            initial_result,
            request_payload=run.request_payload,
            event_count=0,
        )

    def handle_background_error(self, run: RunContext, route: TurnRoute, exc: Exception) -> None:
        self._emit_run_event(
            run,
            {
                "event": "failed",
                "message": str(exc),
                "session_id": route.session_id,
                "errors": [str(exc)],
            },
        )
        self.runs.finish(run, status=RunStatus.FAILED, error=str(exc))

    def _run_intent_response(
        self,
        route: TurnRoute,
        request: QGISAgentRequest,
        intent: TurnIntent,
        *,
        run: RunContext,
        on_event: EventSink | None = None,
    ) -> dict[str, Any]:
        def emit(event: dict[str, Any]) -> None:
            self._emit_run_event(run, event, on_event=on_event)

        try:
            result_payload = self.intent_responses.build(
                intent,
                session_id=route.session_id,
                session_state=route.session_state,
            )
            emit(
                {
                    "event": "summary",
                    "message": result_payload.get("final_message") or "",
                    "session_id": route.session_id,
                    "stream": "stdout",
                }
            )
            emit(
                {
                    "event": "completed",
                    "message": result_payload.get("final_message") or "",
                    "session_id": route.session_id,
                    "result": result_payload,
                }
            )
            save_state = (
                SessionState.from_session(route.session_id, None)
                if intent.kind == "session_control" and intent.control_action == "reset"
                else route.session_state
            )
            self._save_session_result(
                save_state,
                request=request,
                result_payload=self._with_request_metadata(result_payload, request),
                events=run.events,
                message_content=route.message_content or request.message,
                run_id=run.run_id,
            )
            result_payload = self._with_request_metadata(result_payload, request)
            self._save_run_snapshot(run.run_id, route.session_id, result_payload, request=request, event_count=len(run.events), events=run.events)
            result_payload = self.decorate_result_payload(route.session_id, result_payload, event_count=len(run.events), events=run.events)
            self.runs.finish(run, status=str(result_payload.get("status") or RunStatus.COMPLETED))
            return make_json_safe(result_payload)
        except Exception:
            raise

    def _run_route_in_process(
        self,
        route: TurnRoute,
        request: QGISAgentRequest,
        *,
        run: RunContext,
        on_event: EventSink | None = None,
    ) -> dict[str, Any]:
        def emit(event: dict[str, Any]) -> None:
            self._emit_run_event(run, event, on_event=on_event)

        try:
            execution = TurnRunner(
                runtime_factory=self.runtime_factory_for_request(request),
                toolbox_factory=self.toolbox_factory,
                agent_factory=self.agent_factory,
                apply_qgis_environment=self.apply_qgis_environment,
                preload_sources=preload_sources,
                run_snapshot_loader=self.run_snapshot_loader,
            ).run(route, request, emit)

            self._save_session_result(
                route.session_state,
                request=request,
                result_payload=self._with_request_metadata(execution.result_payload, request),
                events=run.events,
                message_content=execution.message_content,
                run_id=run.run_id,
            )
            execution_payload = self._with_request_metadata(execution.result_payload, request)
            self._save_run_snapshot(
                run.run_id,
                route.session_id,
                execution_payload,
                request=request,
                event_count=len(run.events),
                events=run.events,
            )
            result_payload = self.decorate_result_payload(
                route.session_id,
                execution_payload,
                event_count=len(run.events),
            )
            self.runs.finish(run, status=str(result_payload.get("status") or RunStatus.COMPLETED))
            return make_json_safe(result_payload)
        except Exception:
            raise

    def _emit_run_event(
        self,
        run: RunContext,
        event: dict[str, Any],
        *,
        on_event: EventSink | None = None,
    ) -> dict[str, Any]:
        emitted = self.runs.emit(
            run,
            event,
            on_event=on_event,
            decorate_result=self.decorate_result_payload,
            attach_transcript_item=self.attach_run_transcript_item,
        )
        self._refresh_run_snapshot_from_event(run, emitted)
        return emitted

    def _save_session_result(
        self,
        session_state: SessionState,
        *,
        request: QGISAgentRequest,
        result_payload: dict[str, Any],
        events: list[dict[str, Any]],
        message_content: str,
        run_id: str = "",
    ) -> None:
        self.sessions.save(
            session_state.saved_session(
                request_payload=_redacted_request_payload(request),
                result_payload=self.decorate_result_payload(
                    session_state.session_id,
                    result_payload,
                    event_count=len(events),
                    events=events,
                ),
                events=events,
                message_content=message_content,
                run_id=run_id,
            ),
            run_id=run_id,
        )

    def _save_run_snapshot(
        self,
        run_id: str,
        session_id: str,
        result_payload: dict[str, Any],
        *,
        request: QGISAgentRequest | None = None,
        request_payload: dict[str, Any] | None = None,
        event_count: int = 0,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        decorated_result = self.decorate_result_payload(
            session_id, result_payload, event_count=event_count, events=events
        )
        existing_events = self.sessions.list_run_events(run_id, limit=2000)
        decorated_result["report_audit"] = build_report_audit_from_payload(
            decorated_result,
            runtime_events=existing_events,
            artifact_outputs=list(decorated_result.get("outputs") or []),
        )
        decorated_result["decision_rows"] = build_decision_rows(decorated_result, existing_events)
        payload_request = (
            make_json_safe(dict(request_payload))
            if isinstance(request_payload, dict)
            else _redacted_request_payload(request) if request is not None else {}
        )
        pending_task = normalize_pending_task(decorated_result.get("pending_task"))
        payload = {
            "run_id": run_id,
            "session_id": session_id,
            "request": payload_request,
            "result": decorated_result,
            "status": str(decorated_result.get("status") or RunStatus.COMPLETED),
            "transcript": make_json_safe(dict(decorated_result.get("transcript") or {})),
            "workflow": make_json_safe({"steps": _compact_workflow_steps(decorated_result)}),
            "prior_steps": make_json_safe(
                list(decorated_result.get("prior_steps") or [])
                or _existing_prior_steps(self.sessions, run_id)
            ),
            "tool_state": make_json_safe(
                {
                    "state_tree": dict(decorated_result.get("state_tree") or {}),
                    "file_state": dict(decorated_result.get("file_state") or {}),
                    "outputs": list(decorated_result.get("outputs") or []),
                    "risks": list(decorated_result.get("risks") or []),
                }
            ),
            "quality_findings": make_json_safe(list(decorated_result.get("quality_findings") or [])),
            "report_audit": make_json_safe(dict(decorated_result.get("report_audit") or {})),
            "decision_rows": make_json_safe(list(decorated_result.get("decision_rows") or [])),
            "updated_at": str(decorated_result.get("updated_at") or ""),
        }
        if pending_task:
            payload["pending_task"] = pending_task
        self.sessions.save_run_snapshot(run_id, session_id, normalize_run_snapshot(payload, run_id=run_id, session_id=session_id))

    def _refresh_run_snapshot_from_event(
        self,
        run: RunContext,
        event: dict[str, Any],
    ) -> None:
        current = make_json_safe(dict(run.current_result or {}))
        next_result = dict(event.get("result") or {})
        if next_result:
            merged = make_json_safe({**current, **next_result})
            merged["transcript"] = merge_transcript_projection(current.get("transcript"), next_result.get("transcript"))
            current = merged
        else:
            if isinstance(event.get("pending_task"), dict) and event.get("pending_task"):
                current["pending_task"] = normalize_pending_task(event.get("pending_task"))
            if isinstance(event.get("repair"), dict) and event.get("repair"):
                current["repair"] = make_json_safe(dict(event.get("repair") or {}))
            if isinstance(event.get("risk"), dict) and event.get("risk"):
                risks = [dict(item) for item in list(current.get("risks") or []) if isinstance(item, dict)]
                risks.append(dict(event.get("risk") or {}))
                current["risks"] = make_json_safe(risks)
        transcript_item = dict(event.get("transcript_item") or {})
        if transcript_item:
            current["transcript"] = append_transcript_item(current.get("transcript"), transcript_item)
        current["transcript"] = append_user_message_transcript(
            current.get("transcript"),
            run.initial_user_message,
            message_id=_run_user_message_id(run.run_id),
            session_id=run.session_id,
            run_id=run.run_id,
        )
        event_name = str(event.get("event") or "").strip()
        if event_name == "failed":
            current["status"] = RunStatus.FAILED
            if event.get("message"):
                current["errors"] = make_json_safe(list(current.get("errors") or []) + [str(event.get("message") or "")])
        elif event_name == "cancelled":
            current["status"] = "cancelled"
        elif event_name == "paused":
            current["status"] = "paused"
        elif event_name == "question" and not next_result:
            current["status"] = "awaiting_user"
        elif event_name == "confirmation" and not next_result:
            current["status"] = "awaiting_confirmation"
        elif not next_result and not current.get("status"):
            current["status"] = RunStatus.RUNNING
        current = self.decorate_result_payload(run.session_id, current, event_count=len(run.events))
        current["workflow"] = {"steps": _compact_workflow_steps(current)}
        current["report_audit"] = build_report_audit_from_payload(
            current,
            runtime_events=run.events,
            artifact_outputs=list(current.get("outputs") or []),
        )
        current["decision_rows"] = build_decision_rows(current, run.events)
        run.current_result = make_json_safe(dict(current))
        self._save_run_snapshot(
            run.run_id,
            run.session_id,
            current,
            request_payload=run.request_payload,
            event_count=len(run.events),
            events=run.events,
        )

    def decorate_result_payload(
        self,
        session_id: str,
        result_payload: dict[str, Any],
        *,
        event_count: int = 0,
        events: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        payload = make_json_safe(dict(result_payload or {}))
        payload.pop("steps", None)
        payload.pop("state", None)
        payload["file_state"] = make_json_safe(
            dict(payload.get("file_state") or self.sessions.file_state(session_id, event_count=event_count))
        )
        if not payload.get("outputs"):
            payload["outputs"] = self.sessions.artifact_outputs(session_id)
        if not isinstance(payload.get("report_audit"), dict) or not payload.get("report_audit"):
            payload["report_audit"] = build_report_audit_from_payload(
                payload,
                artifact_outputs=list(payload.get("outputs") or []),
            )
        payload = apply_completion_summary(payload)
        if not isinstance(payload.get("transcript"), dict) or not payload.get("transcript"):
            payload["transcript"] = build_transcript_projection(events=events, result=payload)
        payload["transcript"] = append_report_artifact_summary(
            payload.get("transcript"),
            list(payload.get("outputs") or []),
        )
        payload["decision_rows"] = build_decision_rows(payload)
        return payload

    @staticmethod
    def attach_run_transcript_item(event: dict[str, Any], step_contexts: dict[int, dict[str, Any]]) -> None:
        step_index = int(event.get("step_index") or 0)
        event_name = str(event.get("event") or "")
        if step_index > 0 and event_name in {"action", "command"} and event.get("action"):
            step_contexts[step_index] = {
                "action": str(event.get("action") or ""),
                "action_input": dict(event.get("action_input") or {}),
            }
        item = transcript_item_from_event(event, step_context=step_contexts.get(step_index) or {})
        if item:
            event["transcript_item"] = item

    @staticmethod
    def _bootstrap_session_state(route: TurnRoute, intent: TurnIntent | None) -> SessionState:
        if intent is not None and intent.kind == "session_control" and intent.control_action == "reset":
            return SessionState.from_session(route.session_id, None)
        return route.session_state

    def _build_staged_session_payload(
        self,
        session_state: SessionState,
        *,
        run_id: str,
        route: TurnRoute,
        request_payload: dict[str, Any],
        message_content: str,
    ) -> dict[str, Any]:
        messages = self._messages_with_user_turn(session_state.messages, message_content)
        transcript = append_user_message_transcript(
            session_state.transcript,
            message_content,
            message_id=_run_user_message_id(run_id),
            session_id=session_state.session_id,
            run_id=run_id,
        )
        transcript = self._append_resume_transition(transcript, route)
        return {
            "session_id": session_state.session_id,
            "status": RunStatus.RUNNING,
            "session_status": session_state.session_status,
            "last_run_status": RunStatus.RUNNING,
            "messages": messages,
            "request": request_payload,
            "events": session_state.events,
            "success": False,
            "final_message": "",
            "transcript": transcript,
        }

    def _build_initial_run_result(
        self,
        session_state: SessionState,
        *,
        run_id: str,
        route: TurnRoute,
        message_content: str,
        request: QGISAgentRequest | None = None,
    ) -> dict[str, Any]:
        result = make_json_safe(dict(session_state.execution_result()))
        result["prior_steps"] = make_json_safe(list(session_state.continuation_trace()))
        result["status"] = RunStatus.RUNNING
        result["final_message"] = ""
        result["next_question"] = ""
        result["pending_task"] = {}
        result["repair"] = {}
        result["issues"] = []
        result["risks"] = []
        result["errors"] = []
        result["goal_contract"] = self._request_goal_contract(request) or infer_goal_contract(message_content).to_dict()
        result["transcript"] = append_user_message_transcript(
            result.get("transcript"),
            message_content,
            message_id=_run_user_message_id(run_id),
            session_id=session_state.session_id,
            run_id=run_id,
        )
        result["transcript"] = self._append_resume_transition(result.get("transcript"), route)
        return result

    def _with_request_metadata(self, result_payload: dict[str, Any], request: QGISAgentRequest) -> dict[str, Any]:
        payload = make_json_safe(dict(result_payload or {}))
        goal_contract = self._request_goal_contract(request)
        if goal_contract:
            payload["goal_contract"] = goal_contract
        return payload

    @staticmethod
    def _request_goal_contract(request: QGISAgentRequest | None) -> dict[str, Any]:
        if request is None:
            return {}
        value = request.options.goal_contract
        return make_json_safe(dict(value or {})) if isinstance(value, dict) and value else {}

    @staticmethod
    def _append_resume_transition(transcript: dict[str, Any] | None, route: TurnRoute) -> dict[str, Any]:
        item = _resume_transition_item(route)
        if not item:
            return make_json_safe(dict(transcript or {}))
        return append_transcript_item(transcript, item)

    @staticmethod
    def _messages_with_user_turn(messages: list[dict[str, Any]], message_content: str) -> list[dict[str, Any]]:
        text = str(message_content or "").strip()
        next_messages = [dict(item) for item in list(messages or []) if isinstance(item, dict)]
        if not text:
            return make_json_safe(next_messages)
        candidate = {"role": "user", "content": text}
        if next_messages:
            last = next_messages[-1]
            if str(last.get("role") or "") == "user" and str(last.get("content") or "") == text:
                return make_json_safe(next_messages)
        next_messages.append(candidate)
        return make_json_safe(next_messages)


def _resume_transition_item(route: TurnRoute) -> dict[str, Any]:
    if route.kind != "structured_resume":
        return {}
    pending_task = dict(route.session_state.pending_task or {})
    action = str(route.payload.get("action") or "").strip()
    if action == "cancel":
        return {}
    continue_with = _continue_with_label(pending_task)
    locale = get_locale()
    zh = locale == "zh-CN"
    title_by_action = {
        "confirm": "继续处理" if zh else "Continue",
        "patch": "参数已补齐" if zh else "Parameters updated",
        "replan": "任务已调整" if zh else "Task updated",
        "reject": "先跳过这一步" if zh else "Skip for now",
    }
    title = title_by_action.get(action, "继续处理" if zh else "Continue")
    if action == "reject":
        message = "先跳过这一步，等你下一步安排。" if zh else "Skipped for now. Waiting for the next direction."
    elif continue_with:
        message = f"接着处理{continue_with}。" if zh else f"Continuing with {continue_with}."
    else:
        message = "接着处理当前任务。" if zh else "Continuing the current task."
    return make_json_safe(
        {
            "type": "resume_transition",
            "event_key": _resume_transition_key(route, pending_task, action),
            "title": title,
            "text": message,
            "resume_action": action,
            "continue_with": continue_with,
        }
    )


def _resume_transition_key(route: TurnRoute, pending_task: dict[str, Any], action: str) -> str:
    pending_id = str(pending_task.get("pending_id") or "").strip()
    if pending_id:
        return f"resume:{route.kind}:{action}:{pending_id}"
    active_intent = str(pending_task.get("active_intent") or "").strip()
    return f"resume:{route.kind}:{action}:{active_intent or 'task'}"


def _continue_with_label(pending_task: dict[str, Any]) -> str:
    explicit = str(pending_task.get("continue_with") or pending_task.get("continueWith") or "").strip()
    if explicit:
        return explicit
    active_intent = str(pending_task.get("active_intent") or "").strip()
    return display_title_for_action(active_intent) if active_intent else ""


def _redacted_request_payload(request: QGISAgentRequest) -> dict[str, Any]:
    payload = request.model_dump()
    llm = payload.get("llm")
    if isinstance(llm, dict) and llm.get("api_key"):
        llm["api_key"] = "[REDACTED]"
    return payload


def _existing_prior_steps(sessions: Any, run_id: str) -> list[dict[str, Any]]:
    """Read prior_steps from an existing run snapshot so they survive overwrites."""
    try:
        existing = sessions.get_run_snapshot(run_id)
        return list(existing.get("prior_steps") or [])
    except Exception:
        return []


def _compact_workflow_steps(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract compact step metadata from transcript.timeline workflow_step items.

    Each step has: index, action, status, display_title.
    This replaces the legacy react_trace copy in run snapshots.
    """
    transcript = dict(result.get("transcript") or {})
    timeline = list(transcript.get("timeline") or [])
    steps: list[dict[str, Any]] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "workflow_step":
            continue
        steps.append(
            {
                "index": int(item.get("index") or 0),
                "action": str(item.get("tool") or ""),
                "status": str(item.get("status") or ""),
                "display_title": str(item.get("display_title") or ""),
            }
        )
    return steps


def _run_user_message_id(run_id: str) -> str:
    value = str(run_id or "").strip()
    return f"run:{value}:user" if value else ""
