"""Session continuation helpers for PineFlow API orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pineflow_agent.core.json_safety import make_json_safe

from pineflow_api.contracts.transcript_projection import (
    append_transcript_item,
    build_transcript_projection,
    merge_transcript_projection,
)


@dataclass(frozen=True)
class TurnContext:
    """Runtime input restored from a saved session for one user turn."""

    state_tree: dict[str, Any] = field(default_factory=dict)
    prior_steps: list[dict[str, Any]] = field(default_factory=list)
    pending_task: dict[str, Any] = field(default_factory=dict)
    repair: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    user_reply: str = ""
    action: str = ""
    slot_patch: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "state_tree": make_json_safe(dict(self.state_tree)),
            "prior_steps": make_json_safe(list(self.prior_steps)),
            "pending_task": make_json_safe(dict(self.pending_task)),
            "repair": make_json_safe(dict(self.repair)),
        }
        if self.message:
            payload["message"] = self.message
        if self.user_reply:
            payload["user_reply"] = self.user_reply
        if self.action:
            payload["action"] = self.action
        if self.slot_patch:
            payload["slot_patch"] = make_json_safe(dict(self.slot_patch))
        return payload


@dataclass(frozen=True)
class SessionState:
    """Small typed wrapper around the persisted session dict."""

    session_id: str
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_session(cls, session_id: str, session: dict[str, Any] | None) -> "SessionState":
        return cls(session_id=str(session_id or ""), raw=make_json_safe(dict(session or {})))

    @property
    def exists(self) -> bool:
        return bool(self.raw)

    @property
    def status(self) -> str:
        return str(self.raw.get("status") or "")

    @property
    def session_status(self) -> str:
        return str(self.raw.get("session_status") or "active")

    @property
    def last_run_status(self) -> str:
        return str(self.raw.get("last_run_status") or self.status)

    @property
    def latest_run(self) -> dict[str, Any]:
        return dict(self.raw.get("latest_run") or {})

    @property
    def latest_run_id(self) -> str:
        return str(self.latest_run.get("run_id") or "")

    @property
    def messages(self) -> list[dict[str, Any]]:
        return [dict(item) for item in list(self.raw.get("messages") or []) if isinstance(item, dict)]

    @property
    def events(self) -> list[dict[str, Any]]:
        return [dict(item) for item in list(self.raw.get("events") or []) if isinstance(item, dict)]

    @property
    def state_tree(self) -> dict[str, Any]:
        return dict(self.raw.get("state_tree") or {})

    @property
    def react_trace(self) -> list[dict[str, Any]]:
        # Deprecated: retained for old session backward compatibility only.
        # New sessions use transcript.timeline as single truth source.
        # Internal agent continuation should use continuation_trace() instead.
        trace = self.raw.get("react_trace") or []
        return [dict(item) for item in list(trace) if isinstance(item, dict)]

    def continuation_trace(self) -> list[dict[str, Any]]:
        """Return prior steps for agent TurnContext continuation.

        Prefers react_trace from legacy session data; new sessions
        should have prior_steps materialized directly in the run
        snapshot rather than reading from the API-facing result dict.
        """
        return self.react_trace

    @property
    def artifacts(self) -> list[dict[str, Any]]:
        file_state = dict(self.raw.get("file_state") or {})
        artifacts = file_state.get("artifacts") or []
        return [dict(item) for item in list(artifacts) if isinstance(item, dict)]

    @property
    def file_state(self) -> dict[str, Any]:
        return dict(self.raw.get("file_state") or {})

    @property
    def outputs(self) -> list[dict[str, Any]]:
        return [dict(item) for item in list(self.raw.get("outputs") or []) if isinstance(item, dict)]

    @property
    def pending_task(self) -> dict[str, Any]:
        return dict(self.raw.get("pending_task") or {})

    @property
    def repair(self) -> dict[str, Any]:
        return dict(self.raw.get("repair") or {})

    @property
    def transcript(self) -> dict[str, Any]:
        return dict(self.raw.get("transcript") or {})

    def turn_context(self, *, message: str = "", user_reply: str = "") -> TurnContext:
        return TurnContext(
            state_tree=self.state_tree,
            prior_steps=self.react_trace,
            pending_task=self.pending_task,
            repair=self.repair,
            message=str(message or ""),
            user_reply=str(user_reply or ""),
        )

    def execution_result(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status or "running",
            "success": bool(self.raw.get("success", self.status == "completed")),
            "final_message": str(self.raw.get("final_message") or ""),
            "state_tree": self.state_tree,
            "outputs": self.outputs,
            "logs": make_json_safe(list(self.raw.get("logs") or [])),
            "errors": make_json_safe(list(self.raw.get("errors") or [])),
            "next_question": str(self.raw.get("next_question") or ""),
            "issues": make_json_safe(list(self.raw.get("issues") or [])),
            "risks": make_json_safe(list(self.raw.get("risks") or [])),
            "pending_task": self.pending_task,
            "repair": self.repair,
            "transcript": self.transcript,
            "file_state": self.file_state,
        }

    def saved_session(
        self,
        *,
        request_payload: dict[str, Any],
        result_payload: dict[str, Any],
        events: list[dict[str, Any]],
        message_content: str,
        run_id: str = "",
    ) -> dict[str, Any]:
        run_status = str(result_payload.get("status") or "failed")
        messages = _append_turn_messages(self.messages, message_content=message_content, result_payload=result_payload)
        transcript = _saved_session_transcript(
            self.transcript,
            result_payload=result_payload,
            messages=messages,
            events=events,
            message_content=message_content,
            session_id=self.session_id,
            run_id=run_id,
        )
        saved = {
            "session_id": self.session_id,
            "status": run_status,
            "session_status": self.session_status,
            "last_run_status": run_status,
            "messages": messages,
            "request": make_json_safe(dict(request_payload)),
            "events": self.events + [make_json_safe(dict(event)) for event in events],
            "success": bool(result_payload.get("success", run_status == "completed")),
            "final_message": str(result_payload.get("final_message") or ""),
            "transcript": transcript,
        }
        return saved


def turn_context_from_run_snapshot(snapshot: dict[str, Any] | None) -> TurnContext:
    payload = dict(snapshot or {})
    result = dict(payload.get("result") or {})
    return TurnContext(
        state_tree=make_json_safe(dict(result.get("state_tree") or {})),
        prior_steps=make_json_safe(
            list(payload.get("prior_steps") or result.get("react_trace") or [])
        ),
        pending_task=make_json_safe(dict(result.get("pending_task") or {})),
        repair=make_json_safe(dict(result.get("repair") or {})),
    )


def _turn_messages(*, message_content: str, result_payload: dict[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    user_content = str(message_content or "").strip()
    if user_content:
        messages.append({"role": "user", "content": user_content})
    assistant_content = _assistant_message_from_result(result_payload)
    if assistant_content:
        messages.append({"role": "assistant", "content": assistant_content})
    return messages


def append_user_message_transcript(
    transcript: dict[str, Any] | None,
    message_content: str,
    *,
    message_id: str = "",
    session_id: str = "",
    run_id: str = "",
) -> dict[str, Any]:
    text = str(message_content or "").strip()
    if not text:
        return make_json_safe(dict(transcript or {}))
    item = {"type": "user_message", "text": text}
    key = str(message_id or "").strip()
    if key:
        item["message_id"] = key
        item["event_key"] = f"message:{key}"
        item["id"] = f"message:{key}"
    normalized_session_id = str(session_id or "").strip()
    normalized_run_id = str(run_id or _run_id_from_message_id(key)).strip()
    if normalized_session_id:
        item["session_id"] = normalized_session_id
    if normalized_run_id:
        item["run_id"] = normalized_run_id
    item["seq"] = 0
    return append_transcript_item(transcript, item)


def _saved_session_transcript(
    current: dict[str, Any] | None,
    *,
    result_payload: dict[str, Any],
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    message_content: str,
    session_id: str,
    run_id: str,
) -> dict[str, Any]:
    current = _without_workflow_steps(current)
    current_timeline = list(dict(current or {}).get("timeline") or [])
    seed = current
    if not current_timeline:
        seed = build_transcript_projection(
            messages=_messages_before_current_turn(messages, message_content),
            result={},
        )
    base = append_user_message_transcript(
        seed,
        message_content,
        message_id=f"run:{run_id}:user" if str(run_id or "").strip() else "",
        session_id=session_id,
        run_id=run_id,
    )
    incoming = _without_workflow_steps(result_payload.get("transcript"))
    if base.get("timeline") or incoming.get("timeline"):
        return merge_transcript_projection(base, incoming)

    # Legacy fallback only: old sessions may not have transcript fields yet.
    session_result = dict(result_payload)
    session_result.pop("transcript", None)
    return build_transcript_projection(messages=messages, events=events, result=session_result)


def _without_workflow_steps(transcript: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(transcript or {})
    timeline = [
        dict(item)
        for item in list(payload.get("timeline") or [])
        if isinstance(item, dict) and item.get("type") != "workflow_step"
    ]
    if timeline:
        payload["timeline"] = timeline
    else:
        payload.pop("timeline", None)
    return make_json_safe(payload)


def _messages_before_current_turn(messages: list[dict[str, Any]], message_content: str) -> list[dict[str, Any]]:
    text = str(message_content or "").strip()
    if not text:
        return [dict(item) for item in list(messages or []) if isinstance(item, dict)]
    normalized = [
        dict(item)
        for item in list(messages or [])
        if isinstance(item, dict)
    ]
    for index in range(len(normalized) - 1, -1, -1):
        item = normalized[index]
        if str(item.get("role") or "").strip() == "user" and str(item.get("content") or "").strip() == text:
            return normalized[:index]
    return normalized


def _append_turn_messages(
    messages: list[dict[str, Any]],
    *,
    message_content: str,
    result_payload: dict[str, Any],
) -> list[dict[str, str]]:
    next_messages = [dict(item) for item in list(messages or []) if isinstance(item, dict)]
    for message in _turn_messages(message_content=message_content, result_payload=result_payload):
        if _same_message(next_messages[-1] if next_messages else None, message):
            continue
        next_messages.append(message)
    return make_json_safe(next_messages)


def _same_message(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> bool:
    if not isinstance(existing, dict):
        return False
    return (
        str(existing.get("role") or "").strip() == str(incoming.get("role") or "").strip()
        and str(existing.get("content") or "").strip() == str(incoming.get("content") or "").strip()
    )


def _assistant_message_from_result(result_payload: dict[str, Any]) -> str:
    status = str(result_payload.get("status") or "").strip().lower()
    if status in {"awaiting_user", "awaiting_confirmation"}:
        return ""
    final_message = str(result_payload.get("final_message") or "").strip()
    if status == "paused" and final_message.lower() == "paused by user request.":
        return ""
    if final_message:
        return final_message
    next_question = str(result_payload.get("next_question") or "").strip()
    if next_question:
        return next_question
    errors = result_payload.get("errors") or []
    for error in list(errors):
        text = str(error or "").strip()
        if text:
            return text
    return ""


def _run_id_from_message_id(message_id: str) -> str:
    parts = str(message_id or "").split(":")
    if len(parts) >= 3 and parts[0] == "run" and parts[-1] == "user":
        return parts[1]
    return ""


