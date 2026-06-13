"""Runtime-aware skill activation helpers.

Skills are cognitive guidance, not execution authority. This module only ranks
which skill metadata should be shown to the model for the current run context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pineflow_agent.core.json_safety import make_json_safe


@dataclass(frozen=True)
class SkillActivationContext:
    user_request: str = ""
    active_intent: str = ""
    visible_tools: tuple[str, ...] = ()
    active_toolkits: tuple[str, ...] = ()
    workflow_stage: str = "initial"
    workspace_layers: tuple[dict[str, Any], ...] = ()
    artifact_refs: tuple[dict[str, Any], ...] = ()
    risk_codes: tuple[str, ...] = ()
    attention_signals: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return make_json_safe(
            {
                "user_request": self.user_request,
                "active_intent": self.active_intent,
                "visible_tools": list(self.visible_tools),
                "active_toolkits": list(self.active_toolkits),
                "workflow_stage": self.workflow_stage,
                "workspace_layers": [dict(item) for item in self.workspace_layers],
                "artifact_refs": [dict(item) for item in self.artifact_refs],
                "risk_codes": list(self.risk_codes),
                "attention_signals": list(self.attention_signals),
            }
        )


def build_skill_activation_context(
    *,
    user_request: str,
    state: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    visible_tools: list[str] | tuple[str, ...] | None = None,
    tool_disclosure: dict[str, Any] | None = None,
    previous_steps: list[Any] | None = None,
) -> SkillActivationContext:
    state_payload = dict(state or {})
    layers = tuple(_layer_records(state_payload))
    artifact_refs = tuple(_artifact_records(artifacts or state_payload.get("artifacts") or []))
    risk_codes = tuple(_risk_codes(state_payload, previous_steps or []))
    active_toolkits = tuple(
        str(item)
        for item in list(dict(tool_disclosure or {}).get("active_toolkits") or [])
        if str(item or "").strip()
    )
    steps = list(previous_steps or [])
    active_intent = _last_action(steps)
    attention = tuple(_attention_signals(layers=layers, artifacts=artifact_refs, risk_codes=risk_codes))
    return SkillActivationContext(
        user_request=str(user_request or ""),
        active_intent=active_intent,
        visible_tools=tuple(str(item) for item in list(visible_tools or []) if str(item or "").strip()),
        active_toolkits=active_toolkits,
        workflow_stage="initial" if not steps else "running",
        workspace_layers=layers,
        artifact_refs=artifact_refs,
        risk_codes=risk_codes,
        attention_signals=attention,
    )


def activation_for_skill(meta: Any, context: SkillActivationContext) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []

    required_toolkits = tuple(getattr(meta, "requires_toolkits", ()) or ())
    toolkit_hits = [item for item in required_toolkits if item in set(context.active_toolkits)]
    if toolkit_hits:
        score += 2 * len(toolkit_hits)
        reasons.append("active toolkit: " + ", ".join(toolkit_hits))

    attention_hits = [item for item in tuple(getattr(meta, "workspace_attention", ()) or ()) if item in set(context.attention_signals)]
    if attention_hits:
        score += len(attention_hits)
        reasons.append("workspace attention: " + ", ".join(attention_hits[:4]))

    risk_hits = [item for item in tuple(getattr(meta, "risk_awareness", ()) or ()) if item in set(context.risk_codes)]
    if risk_hits:
        score += 5 * len(risk_hits)
        reasons.append("current risk: " + ", ".join(risk_hits[:3]))

    query_hits = _workspace_query_hits(getattr(meta, "workspace_queries", ()) or (), context)
    if query_hits:
        score += len(query_hits)
        reasons.append("workspace queries available: " + ", ".join(query_hits[:4]))

    return make_json_safe(
        {
            "score": score,
            "reasons": reasons,
            "matched_risks": risk_hits,
            "workspace_hits": list(dict.fromkeys(attention_hits + query_hits)),
            "workflow_stage": context.workflow_stage,
        }
    )


def should_activate_skill(activation: dict[str, Any]) -> bool:
    if int(activation.get("score") or 0) <= 0:
        return False
    return bool(
        list(activation.get("matched_risks") or [])
        or list(activation.get("workspace_hits") or [])
    )


def _layer_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for layer in list(state.get("layers") or []):
        if not isinstance(layer, dict):
            continue
        metadata = dict(layer.get("metadata") or {})
        fields = list(metadata.get("fields") or layer.get("fields") or [])
        result.append(
            {
                "layer_id": str(layer.get("layer_id") or ""),
                "name": str(layer.get("name") or layer.get("layer_id") or ""),
                "kind": str(layer.get("kind") or ""),
                "crs": str(metadata.get("crs") or ""),
                "geometry_type": str(metadata.get("geometry_type") or ""),
                "feature_count": metadata.get("feature_count", metadata.get("row_count")),
                "fields": [str(item) for item in fields if str(item or "").strip()],
            }
        )
    return result


def _artifact_records(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in list(value or []):
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "artifact_id": str(item.get("artifact_id") or item.get("id") or ""),
                "role": str(item.get("role") or item.get("artifact_role") or ""),
                "source_action": str(item.get("source_action") or item.get("action") or ""),
                "path": str(item.get("path") or item.get("output_path") or ""),
            }
        )
    return result


def _risk_codes(state: dict[str, Any], previous_steps: list[Any]) -> list[str]:
    codes: list[str] = []
    for risk in list(state.get("risks") or []):
        if isinstance(risk, dict) and str(risk.get("code") or "").strip():
            codes.append(str(risk.get("code")))
    for step in previous_steps:
        payload = step if isinstance(step, dict) else {}
        observation = dict(payload.get("observation") or {}) if isinstance(payload.get("observation"), dict) else {}
        data = dict(observation.get("data") or {}) if isinstance(observation.get("data"), dict) else {}
        for warning in list(data.get("postflight_warnings") or []):
            if isinstance(warning, dict) and str(warning.get("code") or "").strip():
                codes.append(str(warning.get("code")))
            risk = dict(warning.get("risk") or {}) if isinstance(warning, dict) and isinstance(warning.get("risk"), dict) else {}
            if str(risk.get("code") or "").strip():
                codes.append(str(risk.get("code")))
    return list(dict.fromkeys(codes))


def _attention_signals(*, layers: tuple[dict[str, Any], ...], artifacts: tuple[dict[str, Any], ...], risk_codes: tuple[str, ...]) -> list[str]:
    signals: list[str] = []
    if layers:
        signals.extend(["layers", "layer_summary"])
    if any(str(layer.get("crs") or "").strip() for layer in layers):
        signals.extend(["crs", "input_crs"])
    if any(str(layer.get("geometry_type") or "").strip() for layer in layers):
        signals.append("geometry_type")
    if any(list(layer.get("fields") or []) for layer in layers):
        signals.extend(["fields", "field_summaries", "sample_values"])
    if any(str(layer.get("kind") or "") == "table" for layer in layers):
        signals.extend(["csv_fields", "coordinate_field_candidates", "row_count"])
    if artifacts:
        signals.extend(["artifacts", "outputs"])
    if risk_codes:
        signals.append("risks")
    return list(dict.fromkeys(signals))


def _workspace_query_hits(queries: tuple[dict[str, Any], ...], context: SkillActivationContext) -> list[str]:
    hits: list[str] = []
    for query in queries:
        target = str(dict(query).get("target") or "").strip()
        if not target:
            continue
        if target in {"fields", "csv_fields"} and any(list(layer.get("fields") or []) for layer in context.workspace_layers):
            hits.append(target)
        elif target in {"crs", "input_crs"} and any(str(layer.get("crs") or "").strip() for layer in context.workspace_layers):
            hits.append(target)
        elif target in {"layers", "layer_summary"} and context.workspace_layers:
            hits.append(target)
        elif target in {"outputs", "artifacts"} and context.artifact_refs:
            hits.append(target)
        elif target == "risks" and context.risk_codes:
            hits.append(target)
    return list(dict.fromkeys(hits))


def _last_action(steps: list[Any]) -> str:
    for step in reversed(steps):
        if isinstance(step, dict):
            action = str(step.get("action") or "").strip()
            if action:
                return action
    return ""
