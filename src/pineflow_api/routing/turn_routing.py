"""Session-aware turn routing for PineFlow API requests."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.rules.resume_rules import validate_resume_action

from pineflow_api.contracts.models import QGISAgentRequest
from pineflow_api.persistence.session_state import SessionState

TurnKind = Literal[
    "new_session",
    "structured_resume",
    "pending_reply",
    "continue_session",
]

SessionLookup = Callable[[str], dict[str, Any] | None]
BUTTON_RESUME_ACTIONS = {"confirm", "reject", "cancel"}


@dataclass(frozen=True)
class TurnRoute:
    """A normalized route for one incoming user turn."""

    kind: TurnKind
    session_id: str
    session_state: SessionState
    restore_run_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    message_content: str = ""
    subprocess_key: str = ""

    def extra_payload(self) -> dict[str, Any] | None:
        if not self.subprocess_key:
            return None
        payload = make_json_safe(dict(self.payload))
        if self.restore_run_id:
            payload["restore_run_id"] = self.restore_run_id
        return {self.subprocess_key: payload}


class SessionRouter:
    """Classify a request as new work, pending resume, or session continuation."""

    def route(self, request: QGISAgentRequest, session_lookup: SessionLookup) -> TurnRoute:
        requested_session_id = str(request.session_id or "").strip()
        session_id = requested_session_id or uuid.uuid4().hex
        reset_session = bool(request.options.reset_session)
        existing_session = None
        if requested_session_id and not reset_session:
            existing_session = session_lookup(session_id)
        session_state = SessionState.from_session(session_id, existing_session)

        explicit_resume = self._resume_payload(request, session_state)
        if explicit_resume:
            return TurnRoute(
                kind="structured_resume",
                session_id=session_id,
                session_state=session_state,
                restore_run_id=self._restore_run_id(session_state),
                payload=explicit_resume,
                message_content=self.resume_message_content(request, explicit_resume),
                subprocess_key="_resume",
            )

        pending_reply = self._pending_reply_payload(request, session_state)
        if pending_reply:
            return TurnRoute(
                kind="pending_reply",
                session_id=session_id,
                session_state=session_state,
                restore_run_id=self._restore_run_id(session_state),
                payload=pending_reply,
                message_content=request.message,
                subprocess_key="_pending_reply",
            )

        continuation = self._continuation_payload(request, session_state)
        if continuation:
            return TurnRoute(
                kind="continue_session",
                session_id=session_id,
                session_state=session_state,
                restore_run_id=self._restore_run_id(session_state),
                payload=continuation,
                message_content=request.message,
                subprocess_key="_continue",
            )

        return TurnRoute(
            kind="new_session",
            session_id=session_id,
            session_state=SessionState.from_session(session_id, None),
            message_content=request.message,
        )

    @staticmethod
    def _pending_reply_payload(
        request: QGISAgentRequest,
        session_state: SessionState,
    ) -> dict[str, Any] | None:
        if not session_state.exists:
            return None
        if session_state.status != "awaiting_user":
            return None
        pending_task = session_state.pending_task
        if not pending_task:
            return None
        return {"user_reply": request.message}

    @staticmethod
    def _resume_payload(
        request: QGISAgentRequest,
        session_state: SessionState,
    ) -> dict[str, Any] | None:
        resume = request.resume
        if resume is None:
            return None
        status = session_state.status
        pending_task = session_state.pending_task
        action = str(resume.action or "").strip()
        message = str(resume.message or "").strip()
        if not message and action == "replan":
            message = str(request.message or "").strip()
        payload = {
            "action": action,
            "slot_patch": make_json_safe(dict(resume.slot_patch or {})),
            "message": message,
        }
        payload.setdefault("action", action)
        payload.setdefault("slot_patch", {})
        payload.setdefault("message", message)
        issues = validate_resume_action(
            action,
            status=status,
            pending_task=pending_task,
            repair=session_state.repair,
            slot_patch=payload["slot_patch"],
            message=payload["message"],
            has_session=session_state.exists,
        )
        if issues:
            raise ValueError("; ".join(issue.message for issue in issues))
        return payload

    @staticmethod
    def _continuation_payload(
        request: QGISAgentRequest,
        session_state: SessionState,
    ) -> dict[str, Any] | None:
        if not session_state.exists:
            return None
        if not request.session_id.strip():
            return None
        return session_state.turn_context(message=request.message).to_payload()

    @staticmethod
    def _restore_run_id(session_state: SessionState) -> str:
        return session_state.latest_run_id

    @staticmethod
    def resume_message_content(request: QGISAgentRequest, resume_payload: dict[str, Any]) -> str:
        action = _resume_action(resume_payload)
        if action in BUTTON_RESUME_ACTIONS:
            return ""
        if action == "patch":
            message = str(resume_payload.get("message") or "").strip()
            if message:
                return message
            return _patch_message(resume_payload)
        if action == "replan":
            return str(resume_payload.get("message") or request.message or "[replan]")
        message = str(resume_payload.get("message") or request.message or "").strip()
        if message and message != "resume":
            return message
        return str(request.message or f"[{action}]")


def _resume_action(resume_payload: dict[str, Any]) -> str:
    return str(resume_payload.get("action") or "").strip()


def _patch_message(resume_payload: dict[str, Any]) -> str:
    patch = dict(resume_payload.get("slot_patch") or {})
    return f"[patch] {json.dumps(make_json_safe(patch), ensure_ascii=False)}"
