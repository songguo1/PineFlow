"""Final Report audit facts collected from runtime events and legacy steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pineflow_agent.core.artifacts import ArtifactRecord
from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.core.models import AgentResult, ReActStep, react_steps_from_payload


@dataclass
class ReportAudit:
    executed_tools: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    empty_results: list[dict[str, Any]] = field(default_factory=list)
    repairs: list[dict[str, Any]] = field(default_factory=list)
    exports: list[dict[str, Any]] = field(default_factory=list)
    user_confirmations: list[dict[str, Any]] = field(default_factory=list)
    clarification_decisions: list[dict[str, Any]] = field(default_factory=list)
    source_loads: list[dict[str, Any]] = field(default_factory=list)
    quality_findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return normalize_report_audit(
            {
                "executed_tools": self.executed_tools,
                "artifacts": self.artifacts,
                "warnings": self.warnings,
                "empty_results": self.empty_results,
                "repairs": self.repairs,
                "exports": self.exports,
                "user_confirmations": self.user_confirmations,
                "clarification_decisions": self.clarification_decisions,
                "source_loads": self.source_loads,
                "quality_findings": self.quality_findings,
            }
        )


class ReportAuditCollector:
    """Collect report audit facts without parsing human-facing messages."""

    @staticmethod
    def collect(
        result: AgentResult,
        *,
        runtime_events: list[dict[str, Any]] | None = None,
        artifact_outputs: list[dict[str, Any]] | None = None,
    ) -> ReportAudit:
        audit = ReportAudit(
            artifacts=_artifact_records(list(artifact_outputs or [])),
            quality_findings=_dict_items(list(result.quality_findings or [])),
        )
        events = _dict_items(list(runtime_events or []))
        if events:
            _collect_events(audit, events)
        _fill_legacy_fallbacks(audit, list(result.steps or []), result)
        audit.executed_tools = _dedupe_by(audit.executed_tools, ("step_index", "action", "status", "output_path"))
        audit.artifacts = _dedupe_by(audit.artifacts, ("role", "path", "layer_id", "name"))
        audit.warnings = _dedupe_by(audit.warnings, ("code", "message", "step_index", "source"))
        audit.empty_results = _dedupe_by(audit.empty_results, ("code", "message", "step_index", "action"))
        audit.repairs = _dedupe_by(audit.repairs, ("event_type", "action", "repair_step_index", "message"))
        audit.exports = _dedupe_by(audit.exports, ("output_path", "layer_id", "step_index"))
        audit.user_confirmations = _dedupe_by(audit.user_confirmations, ("decision", "risk_code", "message", "step_index"))
        audit.clarification_decisions = _dedupe_by(
            audit.clarification_decisions,
            ("decision", "question", "active_intent", "step_index"),
        )
        audit.source_loads = _dedupe_by(
            audit.source_loads,
            ("path", "slot", "phase", "active_intent"),
        )
        return audit


def build_report_audit_dict(
    result: AgentResult,
    *,
    runtime_events: list[dict[str, Any]] | None = None,
    artifact_outputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return ReportAuditCollector.collect(
        result,
        runtime_events=runtime_events,
        artifact_outputs=artifact_outputs,
    ).to_dict()


def build_report_audit_from_payload(
    result_payload: dict[str, Any],
    *,
    runtime_events: list[dict[str, Any]] | None = None,
    artifact_outputs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = make_json_safe(dict(result_payload or {}))
    result = AgentResult(
        success=bool(payload.get("success", str(payload.get("status") or "") == "completed")),
        final_message=str(payload.get("final_message") or ""),
        steps=react_steps_from_payload(list(payload.get("react_trace") or [])),
        state=make_json_safe(dict(payload.get("state_tree") or {})),
        session_id=str(payload.get("session_id") or ""),
        status=str(payload.get("status") or ""),
        logs=make_json_safe(list(payload.get("logs") or [])),
        errors=make_json_safe(list(payload.get("errors") or [])),
        next_question=str(payload.get("next_question") or ""),
        goal_contract=make_json_safe(dict(payload.get("goal_contract") or {})),
        quality_findings=_dict_items(list(payload.get("quality_findings") or [])),
    )
    return build_report_audit_dict(
        result,
        runtime_events=runtime_events,
        artifact_outputs=artifact_outputs,
    )


def normalize_report_audit(value: Any) -> dict[str, Any]:
    payload = make_json_safe(dict(value or {})) if isinstance(value, dict) else {}
    return {
        "executed_tools": _dict_items(list(payload.get("executed_tools") or [])),
        "artifacts": _dict_items(list(payload.get("artifacts") or [])),
        "warnings": _dict_items(list(payload.get("warnings") or [])),
        "empty_results": _dict_items(list(payload.get("empty_results") or [])),
        "repairs": _dict_items(list(payload.get("repairs") or [])),
        "exports": _dict_items(list(payload.get("exports") or [])),
        "user_confirmations": _dict_items(list(payload.get("user_confirmations") or [])),
        "clarification_decisions": _dict_items(list(payload.get("clarification_decisions") or [])),
        "source_loads": _dict_items(list(payload.get("source_loads") or [])),
        "quality_findings": _dict_items(list(payload.get("quality_findings") or [])),
    }


def _collect_events(audit: ReportAudit, events: list[dict[str, Any]]) -> None:
    for event in events:
        event_type = str(event.get("event_type") or "").strip()
        event_name = str(event.get("event") or "").strip()
        fact = _event_fact(event)
        if event_type in {"tool.completed", "tool.failed"}:
            observation = dict(fact.get("observation") or {})
            output_artifact = _artifact_record(dict(fact.get("output_artifact") or {}))
            audit.executed_tools.append(
                {
                    "event_type": event_type,
                    "status": "success" if event_type == "tool.completed" else "failed",
                    "action": str(fact.get("action") or ""),
                    "action_input": make_json_safe(dict(fact.get("action_input") or {})),
                    "step_index": _int(fact.get("step_index")),
                    "attempt_no": _int(fact.get("attempt_no")),
                    "observation": make_json_safe(observation),
                    "output_layer_id": str(fact.get("output_layer_id") or observation.get("output_layer_id") or ""),
                    "output_path": str(fact.get("output_path") or observation.get("output_path") or ""),
                    "output_artifact": output_artifact,
                    "message": str(event.get("display_summary") or event.get("message") or observation.get("message") or ""),
                }
            )
            export = _export_record_from_tool_fact(fact, observation=observation, output_artifact=output_artifact)
            if export:
                _upsert_export_record(audit.exports, export)
            continue
        if event_type == "source.loaded":
            source_load = _source_load_record(event, fact)
            if source_load:
                audit.source_loads.append(source_load)
                artifact = _artifact_record(dict(source_load.get("artifact") or {}), role="input")
                if artifact:
                    artifact["source_action"] = str(source_load.get("action") or "")
                    artifact["step_index"] = _int(source_load.get("step_index"))
                    audit.artifacts.append(artifact)
            continue
        if event_type == "artifact.created":
            artifact = _artifact_record(dict(fact.get("artifact") or {}), role=str(fact.get("role") or ""))
            if artifact:
                artifact["source_action"] = str(fact.get("source_action") or fact.get("action") or "")
                artifact["step_index"] = _int(fact.get("step_index"))
                audit.artifacts.append(artifact)
            continue
        if event_type == "warning.emitted":
            warning = _warning_record(event, fact)
            if warning:
                audit.warnings.append(warning)
            continue
        if event_type == "result.empty":
            warning = _warning_record(event, fact)
            if warning:
                audit.empty_results.append(warning)
                audit.warnings.append(warning)
            continue
        if event_type == "export.before":
            export = make_json_safe(dict(fact.get("export") or {}))
            if export:
                export["action"] = str(fact.get("action") or "")
                export["step_index"] = _int(fact.get("step_index"))
                _upsert_export_record(audit.exports, export)
            continue
        if event_type in {"repair.started", "repair.completed", "repair.failed"}:
            repair = {
                "event_type": event_type,
                "action": str(fact.get("action") or ""),
                "message": str(event.get("display_summary") or event.get("message") or ""),
                "repair_session": make_json_safe(dict(fact.get("repair_session") or {})),
                "repair_audit": make_json_safe(dict(fact.get("repair_audit") or {})),
                "repair_step_index": _int(fact.get("repair_step_index")),
                "repair_goal": str(fact.get("repair_goal") or ""),
                "step_index": _int(fact.get("step_index")),
            }
            audit.repairs.append(repair)
            continue
        if event_type in {"repair.confirmation_requested", "user_input.requested"} or event_name in {"confirmation", "question"}:
            confirmation = _confirmation_record(event, fact)
            if confirmation:
                audit.user_confirmations.append(confirmation)
            continue
        if event_name == "resume" and isinstance(fact.get("clarification_decision"), dict):
            clarification = _clarification_record(event, fact)
            if clarification:
                audit.clarification_decisions.append(clarification)


def _fill_legacy_fallbacks(audit: ReportAudit, steps: list[ReActStep], result: AgentResult) -> None:
    if not audit.executed_tools:
        for step in steps:
            audit.executed_tools.append(
                {
                    "event_type": "legacy.step",
                    "status": "success" if step.observation.is_success else "failed",
                    "action": step.action,
                    "action_input": make_json_safe(dict(step.action_input or {})),
                    "step_index": step.index,
                    "attempt_no": step.attempt_no,
                    "observation": step.observation.to_dict(),
                    "output_layer_id": step.observation.output_layer_id,
                    "output_path": step.observation.output_path,
                    "message": step.observation.message,
                }
            )
    if not audit.artifacts:
        audit.artifacts.extend(_artifact_records(list(result.to_dict().get("outputs") or [])))
    if not audit.warnings:
        for step in steps:
            data = dict(step.observation.data or {})
            for key in ("preflight_warnings", "postflight_warnings"):
                for item in _dict_items(list(data.get(key) or [])):
                    item.setdefault("source", key.replace("_warnings", ""))
                    item.setdefault("step_index", step.index)
                    item.setdefault("action", step.action)
                    audit.warnings.append(item)
                    if str(item.get("code") or "").startswith("empty_"):
                        audit.empty_results.append(item)
    if not audit.repairs:
        for step in steps:
            data = dict(step.observation.data or {})
            for item in _dict_items(list(data.get("audit_repairs") or [])):
                audit.repairs.append(
                    {
                        "event_type": "legacy.repair",
                        "action": str(item.get("action") or step.action),
                        "message": "",
                        "repair_audit": make_json_safe(dict(item)),
                        "repair_step_index": _int(item.get("step_index") or step.index),
                        "repair_goal": str(item.get("reason") or ""),
                        "step_index": step.index,
                    }
                )
    if not audit.user_confirmations:
        for step in steps:
            data = dict(step.observation.data or {})
            for item in _dict_items(list(data.get("audit_decisions") or [])):
                item.setdefault("step_index", step.index)
                item.setdefault("step_action", step.action)
                audit.user_confirmations.append(item)


def _event_fact(event: dict[str, Any]) -> dict[str, Any]:
    fact = dict(event.get("payload") or {}) if isinstance(event.get("payload"), dict) else {}
    for key, value in event.items():
        if key in {"payload", "debug_payload"}:
            continue
        fact.setdefault(key, value)
    return make_json_safe(fact)


def _warning_record(event: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
    warning = make_json_safe(dict(fact.get("warning") or {}))
    risk = make_json_safe(dict(fact.get("risk") or warning.get("risk") or {}))
    if not warning and not risk:
        return {}
    warning.setdefault("message", str(event.get("display_summary") or event.get("message") or risk.get("message") or ""))
    warning.setdefault("code", str(risk.get("code") or ""))
    warning["risk"] = risk
    warning["source"] = str(fact.get("source") or "")
    warning["action"] = str(fact.get("action") or "")
    warning["step_index"] = _int(fact.get("step_index"))
    affected = _affected_artifacts_for_fact(fact)
    if affected and not warning.get("affected_artifacts"):
        warning["affected_artifacts"] = affected
    return warning


def _confirmation_record(event: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
    pending_task = dict(fact.get("pending_task") or {})
    risk = dict(fact.get("risk") or pending_task.get("risk") or {})
    decision = dict(fact.get("risk_decision") or {})
    diagnosis = dict(risk.get("diagnosis") or {})
    recommendation = diagnosis.get("crs_recommendation")
    if not (pending_task or risk or decision):
        return {}
    return {
        "decision": str(decision.get("decision") or decision.get("kind") or "user_confirmation_requested"),
        "risk_code": str(risk.get("code") or pending_task.get("risk_code") or ""),
        "risk_category": str(risk.get("category") or pending_task.get("confirmation_type") or ""),
        "message": str(event.get("display_summary") or event.get("message") or risk.get("message") or ""),
        "pending_task": make_json_safe(pending_task),
        "risk": make_json_safe(risk),
        "crs_recommendation": make_json_safe(dict(recommendation) if isinstance(recommendation, dict) else {}),
        "selected_crs": str(fact.get("selected_crs") or ""),
        "step_index": _int(fact.get("step_index")),
    }


def _clarification_record(event: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
    decision = make_json_safe(dict(fact.get("clarification_decision") or {}))
    if not decision:
        return {}
    pending_task = make_json_safe(dict(fact.get("pending_task") or {}))
    source = str(decision.get("source") or pending_task.get("source") or "proactive_clarification")
    decision.setdefault(
        "decision",
        "missing_slot_answered" if source == "missing_slot_validation" else "proactive_clarification_answered",
    )
    decision.setdefault("source", source)
    decision.setdefault("question", str(pending_task.get("question") or pending_task.get("last_question") or ""))
    decision.setdefault("active_intent", str(pending_task.get("active_intent") or ""))
    decision.setdefault("selected_crs", str(decision.get("slot_patch", {}).get("target_crs") or ""))
    decision["pending_task"] = pending_task
    decision["step_index"] = _int(fact.get("step_index"))
    decision["message"] = str(event.get("display_summary") or event.get("message") or "")
    return make_json_safe(decision)


def _source_load_record(event: dict[str, Any], fact: dict[str, Any]) -> dict[str, Any]:
    source = make_json_safe(dict(fact.get("source") or {}))
    preload = make_json_safe(dict(fact.get("preload_context") or {}))
    observation = make_json_safe(dict(fact.get("observation") or {}))
    layer = _source_loaded_layer(observation)
    artifact = _source_loaded_artifact(layer)
    source_type = str(source.get("type") or layer.get("kind") or "").strip().lower()
    request = _matching_source_request(preload, source_type)
    alias = str(source.get("alias") or layer.get("name") or Path(str(source.get("path") or "")).stem or "").strip()
    path = str(source.get("path") or layer.get("source") or "").strip()
    if not (alias or path or artifact):
        return {}
    return make_json_safe(
        {
            "event_type": "source.loaded",
            "action": str(fact.get("action") or ""),
            "message": str(event.get("display_summary") or event.get("message") or ""),
            "phase": str(preload.get("phase") or ""),
            "active_intent": str(preload.get("active_intent") or ""),
            "source": source,
            "source_type": source_type,
            "alias": alias,
            "path": path,
            "slot": str(request.get("slot") or ""),
            "slot_label": str(request.get("slot_label") or ""),
            "accepted_source_types": [
                str(item)
                for item in list(request.get("accepted_source_types") or [])
                if str(item or "").strip()
            ],
            "layer_id": str(layer.get("layer_id") or ""),
            "artifact": artifact,
            "step_index": _int(fact.get("step_index")),
        }
    )


def _source_loaded_layer(observation: dict[str, Any]) -> dict[str, Any]:
    data = dict(observation.get("data") or {})
    layer = data.get("layer")
    return make_json_safe(dict(layer or {})) if isinstance(layer, dict) else {}


def _source_loaded_artifact(layer: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(layer.get("metadata") or {})
    artifact = metadata.get("artifact")
    if isinstance(artifact, dict) and artifact:
        return _artifact_record(dict(artifact), role="input")
    if not layer:
        return {}
    payload = dict(layer)
    payload.setdefault("artifact_id", str(metadata.get("artifact_id") or layer.get("layer_id") or layer.get("name") or ""))
    return _artifact_record(payload, role="input")


def _matching_source_request(preload: dict[str, Any], source_type: str) -> dict[str, Any]:
    requests = [dict(item) for item in list(preload.get("source_requests") or []) if isinstance(item, dict)]
    for item in requests:
        accepted = [str(value or "").strip().lower() for value in list(item.get("accepted_source_types") or [])]
        if source_type and source_type in accepted:
            return item
    return requests[0] if requests else {}


def _artifact_records(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _dict_items(items):
        record = _artifact_record(item, role=str(item.get("role") or ""))
        if record:
            records.append(record)
    return records


def _artifact_record(item: dict[str, Any], *, role: str = "") -> dict[str, Any]:
    if not item:
        return {}
    payload = dict(item)
    if role and not payload.get("role"):
        payload["role"] = role
    try:
        return ArtifactRecord.from_dict(payload).output_dict()
    except Exception:
        return make_json_safe(payload)


def _export_record_from_tool_fact(
    fact: dict[str, Any],
    *,
    observation: dict[str, Any],
    output_artifact: dict[str, Any],
) -> dict[str, Any]:
    action = str(fact.get("action") or "").strip()
    if action != "export_result":
        return {}
    action_input = dict(fact.get("action_input") or {})
    artifact = dict(output_artifact or {})
    output_path = str(
        artifact.get("path")
        or fact.get("output_path")
        or observation.get("output_path")
        or action_input.get("output_path")
        or ""
    ).strip()
    if not output_path and not artifact:
        return {}
    input_artifacts = [
        dict(item)
        for item in list(artifact.get("input_artifacts") or [])
        if isinstance(item, dict)
    ]
    primary_input = input_artifacts[0] if input_artifacts else {}
    return make_json_safe(
        {
            "action": action,
            "step_index": _int(fact.get("step_index")),
            "layer_id": str(fact.get("output_layer_id") or observation.get("output_layer_id") or artifact.get("layer_id") or ""),
            "layer_name": str(
                artifact.get("name")
                or primary_input.get("name")
                or action_input.get("layer_ref")
                or fact.get("output_layer_id")
                or ""
            ),
            "output_name": Path(output_path).name if output_path else "",
            "output_path": output_path,
            "feature_count": artifact.get("feature_count"),
            "crs": artifact.get("crs"),
            "geometry_type": artifact.get("geometry_type"),
            "artifact": artifact,
            "input_artifacts": input_artifacts,
            "source_artifact": make_json_safe(primary_input),
        }
    )


def _affected_artifacts_for_fact(fact: dict[str, Any]) -> list[dict[str, Any]]:
    output_artifact = _artifact_record(dict(fact.get("output_artifact") or {}))
    if output_artifact:
        return [_compact_artifact_ref(output_artifact)]
    artifact = _artifact_record(dict(fact.get("artifact") or {}))
    if artifact:
        return [_compact_artifact_ref(artifact)]
    return []


def _compact_artifact_ref(artifact: dict[str, Any]) -> dict[str, Any]:
    return make_json_safe(
        {
            "artifact_id": artifact.get("artifact_id"),
            "name": artifact.get("name") or artifact.get("layer_id"),
            "role": artifact.get("role"),
            "path": artifact.get("path"),
        }
    )


def _upsert_export_record(records: list[dict[str, Any]], export: dict[str, Any]) -> None:
    step_index = _int(export.get("step_index"))
    output_path = str(export.get("output_path") or "").strip()
    layer_id = str(export.get("layer_id") or "").strip()
    for index, existing in enumerate(records):
        existing_step = _int(existing.get("step_index"))
        existing_path = str(existing.get("output_path") or "").strip()
        existing_layer_id = str(existing.get("layer_id") or "").strip()
        same_path = output_path and existing_path == output_path
        same_layer = layer_id and existing_layer_id == layer_id
        if existing_step == step_index and (same_path or same_layer):
            merged = dict(existing)
            for key, value in export.items():
                if value in (None, "", [], {}):
                    continue
                merged[key] = value
            records[index] = make_json_safe(merged)
            return
    records.append(make_json_safe(dict(export)))


def _dict_items(items: list[Any]) -> list[dict[str, Any]]:
    return [make_json_safe(dict(item)) for item in items if isinstance(item, dict)]


def _dedupe_by(items: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = tuple(item.get(field) for field in keys)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
