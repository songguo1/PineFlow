"""LLM-backed triage for natural-language turns before ReAct tool use."""

from __future__ import annotations

import json
from typing import Any, Protocol

from pineflow_agent.core.json_safety import make_json_safe

from pineflow_api.persistence.session_state import SessionState
from pineflow_api.routing.turn_intent import TurnIntent


class JsonCompletionClient(Protocol):
    def complete(self, *, system_prompt: str, user_prompt: str) -> str:
        """Return a JSON object string."""


TRIAGE_SYSTEM_PROMPT = """You route one user message before a GIS ReAct runtime.

Return only one JSON object. Do not call tools. Do not write Markdown.

Choose intent:
- chat: conversational message that does not need GIS state or GIS execution.
- gis_answer: read-only question about the current GIS session state.
- gis_execute: request that should enter the GIS tool-use runtime.

For gis_answer choose answer_type:
fields, layers, crs, outputs, last_step, summary, or none.

Use gis_execute when unsure. Do not block possible GIS work. Session reset/cancel is handled by slash commands, not natural-language triage.
"""

VALID_INTENTS: set[str] = {"chat", "gis_answer", "gis_execute"}
VALID_ANSWER_TYPES: set[str] = {"fields", "layers", "crs", "outputs", "last_step", "summary", "none"}


class TriageRouter:
    """Classify natural-language turns with a structured, fail-closed LLM call."""

    def __init__(self, *, confidence_threshold: float = 0.55) -> None:
        self.confidence_threshold = float(confidence_threshold)

    def classify(
        self,
        message: str,
        session_state: SessionState,
        *,
        llm: JsonCompletionClient | None = None,
    ) -> TurnIntent:
        if session_state.status in {"awaiting_user", "awaiting_confirmation"}:
            return TurnIntent("gis_execute", reason="pending_session")
        if llm is None:
            return TurnIntent("gis_execute", reason="triage_unavailable")
        try:
            payload = json.loads(
                llm.complete(
                    system_prompt=TRIAGE_SYSTEM_PROMPT,
                    user_prompt=json.dumps(
                        _triage_payload(message=message, session_state=session_state),
                        ensure_ascii=False,
                    ),
                )
            )
        except Exception as exc:
            return TurnIntent("gis_execute", reason=f"triage_failed:{exc.__class__.__name__}")
        intent = _intent_from_payload(payload)
        if intent.kind != "gis_execute" and intent.confidence < self.confidence_threshold:
            return TurnIntent(
                "gis_execute",
                reason=f"low_triage_confidence:{intent.kind}",
                confidence=intent.confidence,
            )
        return intent


def _intent_from_payload(payload: dict[str, Any]) -> TurnIntent:
    intent = str(payload.get("intent") or "gis_execute").strip()
    if intent not in VALID_INTENTS:
        intent = "gis_execute"
    answer_type = str(payload.get("answer_type") or "none").strip()
    if answer_type not in VALID_ANSWER_TYPES:
        answer_type = "none"
    confidence = _confidence(payload.get("confidence"))
    if intent == "gis_answer" and answer_type == "none":
        answer_type = "summary"
    return TurnIntent(
        kind=intent,  # type: ignore[arg-type]
        reason=str(payload.get("reason") or "llm_triage"),
        answer_type=answer_type,  # type: ignore[arg-type]
        control_action="",
        confidence=confidence,
        message=str(payload.get("message") or "").strip(),
    )


def _confidence(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(max(score, 0.0), 1.0)


def _triage_payload(*, message: str, session_state: SessionState) -> dict[str, Any]:
    layers = _layer_summaries(session_state)
    outputs = _output_summaries(session_state)
    return {
        "message": str(message or ""),
        "session": {
            "status": session_state.status or "new",
            "has_layers": bool(layers),
            "has_outputs": bool(outputs),
            "has_prior_steps": bool(session_state.react_trace),
        },
        "layers": make_json_safe(layers),
        "outputs": make_json_safe(outputs),
        "response_schema": {
            "intent": "chat | gis_answer | gis_execute",
            "answer_type": "fields | layers | crs | outputs | last_step | summary | none",
            "confidence": "number from 0 to 1",
            "reason": "short reason",
            "message": "short user-facing message only for chat",
        },
    }


def _layer_summaries(session_state: SessionState) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for layer in list(session_state.state_tree.get("layers") or []):
        if not isinstance(layer, dict):
            continue
        metadata = dict(layer.get("metadata") or {})
        fields = metadata.get("fields") or []
        summaries.append(
            {
                "name": layer.get("name"),
                "layer_id": layer.get("layer_id"),
                "kind": layer.get("kind"),
                "crs": metadata.get("crs"),
                "geometry_type": metadata.get("geometry_type"),
                "feature_count": metadata.get("feature_count"),
                "field_count": len(fields) if isinstance(fields, list) else None,
                "fields_preview": _fields_preview(fields),
            }
        )
    return summaries[:12]


def _output_summaries(session_state: SessionState) -> list[dict[str, Any]]:
    artifact_summaries: list[dict[str, Any]] = []
    for artifact in session_state.artifacts:
        if str(artifact.get("role") or "") == "input":
            continue
        artifact_summaries.append(
            {
                "name": artifact.get("name"),
                "layer_id": artifact.get("layer_id"),
                "kind": artifact.get("kind"),
                "path": artifact.get("path"),
                "role": artifact.get("role"),
            }
        )
    if artifact_summaries:
        return artifact_summaries[:12]
    summaries: list[dict[str, Any]] = []
    for output in session_state.outputs:
        if not isinstance(output, dict):
            continue
        summaries.append(
            {
                "name": output.get("name"),
                "layer_id": output.get("layer_id"),
                "kind": output.get("kind"),
                "path": output.get("path") or output.get("output_path"),
            }
        )
    return summaries[:12]


def _fields_preview(fields: Any) -> list[str]:
    preview: list[str] = []
    for field in list(fields or [])[:12]:
        if isinstance(field, dict):
            value = field.get("name") or field.get("field_name") or field.get("id")
        else:
            value = field
        text = str(value or "").strip()
        if text:
            preview.append(text)
    return preview
