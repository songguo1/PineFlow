"""Pure session projection helpers for SQLite-backed session persistence."""

from __future__ import annotations

from typing import Any

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_api.contracts.transcript_projection import append_transcript_item, merge_transcript_projection


class SessionProjector:
    """Build API-facing session views from events, snapshots, and run results."""

    COPY_LIST_FIELDS = (
        "messages",
        "react_trace",  # deprecated — retained for pre-consolidation session backward compat
        "outputs",
        "logs",
        "errors",
        "issues",
        "risks",
        "quality_findings",
        "decision_rows",
    )
    COPY_DICT_FIELDS = (
        "request",
        "state_tree",
        "pending_task",
        "repair",
        "transcript",
        "file_state",
        "goal_contract",
        "report_audit",
    )
    COPY_TEXT_FIELDS = ("next_question",)
    EXECUTION_RESULT_KEYS = (
        "session_status",
        "messages",
        "request",
        "state_tree",
        "react_trace",  # deprecated — retained for pre-consolidation session backward compat
        "outputs",
        "logs",
        "errors",
        "next_question",
        "issues",
        "risks",
        "pending_task",
        "repair",
        "transcript",
        "file_state",
        "goal_contract",
        "quality_findings",
        "report_audit",
        "decision_rows",
    )

    @staticmethod
    def session_summary(session_id: str, session: dict[str, Any]) -> dict[str, Any]:
        messages = list(session.get("messages") or [])
        first_message = ""
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "user":
                first_message = str(msg.get("content") or "")
                break
        summary = {
            "session_id": str(session.get("session_id") or session_id),
            "status": str(session.get("status") or "unknown"),
            "first_message": str(first_message)[:120] if first_message else "",
            "updated_at": str(session.get("updated_at") or ""),
            "event_count": int(session.get("event_count") or 0),
            "message_count": len(messages),
        }
        if isinstance(session.get("latest_run"), dict):
            summary["latest_run"] = make_json_safe(dict(session.get("latest_run") or {}))
        return summary

    @staticmethod
    def project_from_events(session_id: str, events: list[dict[str, Any]], base_session: dict[str, Any]) -> dict[str, Any]:
        payload = make_json_safe(dict(base_session or {}))
        payload["session_id"] = session_id
        for event in list(events or []):
            payload = SessionProjector.apply_event_to_session(payload, event)
        payload["events"] = [dict(item) for item in list(events or []) if isinstance(item, dict)]
        return payload

    @staticmethod
    def execution_state_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for event in list(events or []):
            event_name = str(event.get("event") or "")
            result = event.get("result")
            if isinstance(result, dict):
                payload = SessionProjector.merge_execution_result(payload, result)
            elif event_name in {"completed", "failed", "cancelled"}:
                payload["status"] = _status_from_event_name(event_name)
                payload["last_run_status"] = payload["status"]
        return payload

    @staticmethod
    def apply_event_to_session(session: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(session)
        event_name = str(event.get("event") or "")
        if isinstance(event.get("result"), dict):
            payload = SessionProjector.merge_execution_result(payload, event["result"], fallback_status=event_name)
        transcript_item = dict(event.get("transcript_item") or {})
        if transcript_item and transcript_item.get("type") != "workflow_step":
            payload["transcript"] = append_transcript_item(payload.get("transcript"), transcript_item)
        if event_name in {"completed", "failed", "cancelled"}:
            run_status = _status_from_event_name(event_name)
            payload["status"] = run_status
            payload["session_status"] = str(payload.get("session_status") or "active")
            payload["last_run_status"] = run_status
        return payload

    @staticmethod
    def base_session(session_id: str = "") -> dict[str, Any]:
        return {
            "session_id": str(session_id or ""),
            "status": "running",
            "messages": [],
            "request": {},
            "events": [],
            "state_tree": {},
            "react_trace": [],  # deprecated — retained for pre-consolidation session backward compat
            "outputs": [],
            "logs": [],
            "errors": [],
            "next_question": "",
            "issues": [],
            "risks": [],
            "pending_task": {},
            "repair": {},
            "transcript": {},
        }

    @staticmethod
    def copy_execution_fields(snapshot: dict[str, Any] | None, *, session_id: str = "") -> dict[str, Any]:
        base = SessionProjector.base_session(session_id)
        payload = make_json_safe(dict(snapshot or {}))
        _copy_execution_contract(base, payload)
        if payload.get("status"):
            base["status"] = str(payload.get("status") or "running")
        if payload.get("success") is not None:
            base["success"] = bool(payload.get("success"))
        if payload.get("final_message"):
            base["final_message"] = str(payload.get("final_message") or "")
        if payload.get("last_run_status"):
            base["last_run_status"] = str(payload.get("last_run_status") or "")
        return base

    @staticmethod
    def merge_execution_result(
        session: dict[str, Any],
        result_payload: dict[str, Any],
        *,
        fallback_status: str = "",
    ) -> dict[str, Any]:
        payload = make_json_safe(dict(session or {}))
        result = make_json_safe(dict(result_payload or {}))
        run_status = str(result.get("status") or fallback_status or payload.get("status") or "running")
        payload["status"] = run_status
        payload["session_status"] = str(payload.get("session_status") or "active")
        payload["last_run_status"] = run_status
        payload["success"] = bool(result.get("success", run_status == "completed"))
        payload["final_message"] = str(result.get("final_message") or payload.get("final_message") or "")
        _merge_execution_contract(payload, result)
        return payload

    @staticmethod
    def session_snapshot_payload(session: dict[str, Any], *, run_id: str = "") -> dict[str, Any]:
        payload = make_json_safe(dict(session or {}))
        if not run_id:
            return payload
        return {
            "session_id": str(payload.get("session_id") or ""),
            "status": str(payload.get("status") or "running"),
            "session_status": str(payload.get("session_status") or "active"),
            "last_run_status": str(payload.get("last_run_status") or payload.get("status") or "running"),
            "success": bool(payload.get("success", str(payload.get("status") or "") == "completed")),
            "final_message": str(payload.get("final_message") or ""),
            "messages": make_json_safe(list(payload.get("messages") or [])),
            "request": make_json_safe(dict(payload.get("request") or {})),
            "events": make_json_safe(list(payload.get("events") or [])),
            "react_trace": make_json_safe(list(payload.get("react_trace") or [])),  # deprecated — backward compat
            "transcript": make_json_safe(dict(payload.get("transcript") or {})),
            "state_version": int(payload.get("state_version") or 2),
            "updated_at": str(payload.get("updated_at") or ""),
            "event_count": int(payload.get("event_count") or len(list(payload.get("events") or []))),
        }

    @staticmethod
    def hydrate_session_execution(
        *,
        session_id: str,
        session: dict[str, Any],
        events: list[dict[str, Any]],
        latest_run_result: dict[str, Any],
    ) -> dict[str, Any]:
        payload = make_json_safe(dict(session or {}))
        payload["session_id"] = session_id
        payload["events"] = [dict(item) for item in list(events or []) if isinstance(item, dict)]
        if latest_run_result:
            return SessionProjector.merge_latest_run_result(payload, latest_run_result)
        if _has_execution_state(payload):
            return payload
        projected = SessionProjector.execution_state_from_events(events)
        if projected:
            return SessionProjector.merge_execution_result(payload, projected)
        return payload

    @staticmethod
    def latest_run_result_from_snapshot(snapshot: dict[str, Any], latest_run: dict[str, Any]) -> dict[str, Any]:
        result = snapshot.get("result") if isinstance(snapshot, dict) else {}
        if not isinstance(result, dict):
            return {}
        merged = make_json_safe(dict(result))
        if latest_run.get("status"):
            merged["status"] = str(latest_run.get("status") or merged.get("status") or "")
        return merged

    @staticmethod
    def merge_latest_run_result(session: dict[str, Any], latest_run_result: dict[str, Any]) -> dict[str, Any]:
        payload = make_json_safe(dict(session or {}))
        conversation_messages = make_json_safe(list(payload.get("messages") or []))
        conversation_transcript = make_json_safe(dict(payload.get("transcript") or {}))
        merged = SessionProjector.merge_execution_result(payload, latest_run_result)
        merged["messages"] = conversation_messages
        merged["transcript"] = conversation_transcript
        merged["active_run_result"] = make_json_safe(dict(latest_run_result or {}))
        return merged

    @staticmethod
    def decorate_session(
        *,
        session_id: str,
        session: dict[str, Any],
        latest_run: dict[str, Any],
        file_state: dict[str, Any],
        updated_at: str,
    ) -> dict[str, Any]:
        payload = make_json_safe(dict(session or {}))
        payload["session_id"] = session_id
        payload.setdefault("session_status", "active")
        payload.setdefault("status", str(latest_run.get("status") or payload.get("status") or "running"))
        payload.setdefault("last_run_status", str(payload.get("status") or ""))
        if latest_run.get("status"):
            payload["status"] = str(latest_run.get("status") or "")
            payload["last_run_status"] = str(latest_run.get("status") or "")
        if not isinstance(payload.get("messages"), list):
            payload["messages"] = []
        if not isinstance(payload.get("request"), dict):
            payload["request"] = {}
        if not isinstance(payload.get("events"), list):
            payload["events"] = []
        payload["file_state"] = make_json_safe(dict(file_state or {}))
        payload["state_version"] = 2
        payload["updated_at"] = str(updated_at or "")
        payload["event_count"] = int(payload.get("event_count") or len(list(payload.get("events") or [])))
        payload["latest_run"] = make_json_safe(dict(latest_run or {}))
        return payload


def _status_from_event_name(event_name: str) -> str:
    if event_name == "failed":
        return "failed"
    if event_name == "cancelled":
        return "cancelled"
    return "completed"


def _state_tree_has_content(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(value.get("layers") or value.get("aliases"))


def _has_execution_state(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if _state_tree_has_content(value.get("state_tree")):
        return True
    for key in ("react_trace", "outputs", "issues", "risks", "logs", "errors"):
        if isinstance(value.get(key), list) and value.get(key):
            return True
    for key in ("pending_task", "repair"):
        if isinstance(value.get(key), dict) and value.get(key):
            return True
    return bool(str(value.get("next_question") or "").strip())


def _copy_execution_contract(target: dict[str, Any], payload: dict[str, Any]) -> None:
    for key in SessionProjector.COPY_LIST_FIELDS:
        if key in payload:
            target[key] = _normalize_json_list(payload.get(key))
    for key in SessionProjector.COPY_DICT_FIELDS:
        if key in payload:
            target[key] = _normalize_json_dict(payload.get(key))
    for key in SessionProjector.COPY_TEXT_FIELDS:
        if key in payload:
            target[key] = str(payload.get(key) or "")
    if "session_status" in payload:
        target["session_status"] = str(payload.get("session_status") or "active")


def _merge_execution_contract(target: dict[str, Any], result: dict[str, Any]) -> None:
    for key in ("react_trace", "outputs", "logs", "errors", "issues", "risks", "quality_findings", "decision_rows"):
        if key in result:
            target[key] = _normalize_json_list(result.get(key))
    if "state_tree" in result and _state_tree_has_content(result.get("state_tree")):
        target["state_tree"] = _normalize_state_tree(result.get("state_tree"))
    for key in ("pending_task", "repair", "goal_contract", "report_audit", "file_state", "active_run_result"):
        if key in result:
            target[key] = _normalize_json_dict(result.get(key))
    if "transcript" in result:
        target["transcript"] = merge_transcript_projection(target.get("transcript"), result.get("transcript"))
    for key in ("next_question",):
        if key in result:
            target[key] = str(result.get(key) or "")


def _normalize_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return make_json_safe(list(value))
    return []


def _normalize_json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return make_json_safe(dict(value))
    return {}


def _normalize_state_tree(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return make_json_safe(dict(value))
    return {}


# ── display_transcript projection ────────────────────────────────────────


def build_display_transcript(
    *,
    session_transcript: dict[str, Any],
    active_run_transcript: dict[str, Any],
    active_run_id: str = "",
) -> dict[str, Any]:
    """Build a single display transcript by splicing the active run into session history.

    This is a read-model projection only — it does not modify any stored facts.
    The frontend chat area renders this and nothing else.
    """
    session_normalized = _normalize_transcript_input(session_transcript)
    run_normalized = _normalize_transcript_input(active_run_transcript)
    session_timeline = session_normalized["timeline"]
    run_timeline = run_normalized["timeline"]

    if not run_timeline:
        return session_normalized

    version = max(session_normalized["version"], run_normalized["version"])
    run_id = str(active_run_id or "").strip()
    start_index = _find_run_start_index(session_timeline, run_id, run_timeline)

    if start_index >= 0:
        display_timeline = list(session_timeline[:start_index]) + list(run_timeline)
    else:
        display_timeline = list(session_timeline) + list(run_timeline)

    from pineflow_api.contracts.transcript_projection import _dedupe_timeline

    return {"timeline": _dedupe_timeline(display_timeline), "version": version}


def _normalize_transcript_input(value: Any) -> dict[str, Any]:
    """Normalize a transcript input to stable shape: {version, timeline}."""
    transcript = value if isinstance(value, dict) else {}
    timeline = [
        dict(item) for item in list(transcript.get("timeline") or []) if isinstance(item, dict)
    ]
    return {
        "version": max(int(transcript.get("version") or 0), 2),
        "timeline": timeline,
    }


def _find_run_start_index(
    session_timeline: list[dict[str, Any]],
    run_id: str,
    run_timeline: list[dict[str, Any]],
) -> int:
    """Find the index in session_timeline where the active run segment begins.

    Priority anchors:
    1. message:run:{run_id}:user — the user message that started this run
    2. First item in run_timeline with a stable event_key
    3. Return -1 if no anchor found (caller falls back to append)
    """
    user_anchor = f"message:run:{run_id}:user"
    for idx, item in enumerate(session_timeline):
        if not isinstance(item, dict):
            continue
        if item.get("event_key") == user_anchor or item.get("id") == user_anchor:
            return idx
        if item.get("message_id") == f"run:{run_id}:user":
            return idx

    if run_id:
        for idx, item in enumerate(session_timeline):
            if _item_belongs_to_run(item, run_id):
                return idx

    if run_timeline and run_id:
        first = run_timeline[0]
        first_key = str(first.get("event_key") or first.get("id") or "").strip()
        if first_key:
            for idx, item in enumerate(session_timeline):
                if not isinstance(item, dict):
                    continue
                if item.get("event_key") == first_key or item.get("id") == first_key:
                    return idx

    return -1


def _item_belongs_to_run(item: dict[str, Any], run_id: str) -> bool:
    if not isinstance(item, dict):
        return False
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id:
        return False
    if str(item.get("run_id") or item.get("runId") or "").strip() == normalized_run_id:
        return True
    run_prefix = f"run:{normalized_run_id}:"
    message_prefix = f"message:run:{normalized_run_id}:"
    for key in ("event_key", "id", "message_id"):
        value = str(item.get(key) or "").strip()
        if value.startswith(run_prefix) or value.startswith(message_prefix):
            return True
    return False
