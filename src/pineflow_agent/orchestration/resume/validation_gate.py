"""Validation issue branching for the ReAct loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from pineflow_agent.core.field_metadata import field_records
from pineflow_agent.core.messages import format_slots, get_locale, render_message
from pineflow_agent.core.models import ActionPlan, AgentResult, ReActStep
from pineflow_agent.orchestration.agent.goal_contract import attach_goal_contract
from pineflow_agent.orchestration.agent.result_builder import awaiting_result
from pineflow_agent.policies.autonomy_policy import AutonomyDecision, AutonomyPolicy
from pineflow_agent.policies.crs_recommendation import DEFAULT_PROJECTED_CRS, recommend_projected_crs
from pineflow_agent.risks.converters import risks_from_issues
from pineflow_agent.risks.models import GISRisk, RiskDecision
from pineflow_agent.risks.policy import RiskPolicy
from pineflow_agent.rules.validation import (
    PendingTask,
    ValidationIssue,
    allowed_resume_actions,
    normalize_pending_choices,
    normalize_slot_patch_schema,
)
from pineflow_agent.orchestration.resume.export_result_contract import (
    export_result_missing_slots,
    export_result_question,
    export_result_slot_patch_schema,
)
from pineflow_agent.tools.contracts.tool_definitions import display_title_for_action, tool_definition_for_action
from pineflow_agent.tools.semantic.semantic_tools import normalize_semantic_input


@dataclass(frozen=True)
class ValidationPause:
    event: str
    message: str
    result: AgentResult
    payload: dict[str, Any]


@dataclass(frozen=True)
class ValidationGate:
    state_tree: dict[str, Any]
    steps: list[ReActStep]
    session_id: str
    user_request: str
    ux_explanation: str = ""
    ux_explainer: Callable[..., str] | None = None

    def pause_for_issues(
        self,
        *,
        plan: ActionPlan,
        issues: list[ValidationIssue],
        step_index: int,
        step_total: int,
    ) -> ValidationPause | None:
        if not issues:
            return None
        risks = risks_from_issues(issues, tool_name=plan.action, state_tree=self.state_tree)
        decision = RiskPolicy().evaluate(risks)
        primary_issue = _primary_issue_for_decision(issues, risks, decision)
        autonomy_decision = AutonomyPolicy().decide_validation_issue(
            plan=plan,
            issue=primary_issue,
            risk=decision.primary_risk,
        )
        _attach_autonomy_decision(risks, autonomy_decision)
        ux_explanation = self._explain_pause(
            plan=plan,
            issue=primary_issue,
            risk=decision.primary_risk,
            awaiting_state="awaiting_confirmation"
            if decision.kind == "ask_confirmation"
            else "awaiting_user",
        )
        if primary_issue.stage == "preflight":
            return self._preflight_pause(
                plan=plan,
                issues=issues,
                risks=risks,
                decision=decision,
                primary_issue=primary_issue,
                step_index=step_index,
                step_total=step_total,
                ux_explanation=ux_explanation,
                autonomy_decision=autonomy_decision,
            )
        return self._semantic_pause(
            plan=plan,
            issues=issues,
            risks=risks,
            decision=decision,
            primary_issue=primary_issue,
            step_index=step_index,
            step_total=step_total,
            ux_explanation=ux_explanation,
            autonomy_decision=autonomy_decision,
        )

    def _preflight_pause(
        self,
        *,
        plan: ActionPlan,
        issues: list[ValidationIssue],
        risks: list[GISRisk],
        decision: RiskDecision,
        primary_issue: ValidationIssue,
        step_index: int,
        step_total: int,
        ux_explanation: str,
        autonomy_decision: AutonomyDecision,
    ) -> ValidationPause:
        prompt_message = primary_issue.repair.message if primary_issue.repair else primary_issue.message
        status = "awaiting_confirmation" if decision.kind == "ask_confirmation" else "awaiting_user"
        primary_risk = decision.primary_risk
        missing_slots = _preflight_missing_slots(plan, primary_issue)
        missing_slots = _pending_missing_slots(plan.action, missing_slots)
        normalized = normalize_semantic_input(plan.action, plan.action_input)
        filled_slots = {
            key: value
            for key, value in normalized.items()
            if key not in missing_slots and value is not None and str(value).strip() != ""
        }
        choices = pending_choices_from_context(
            plan,
            primary_issue,
            missing_slots=missing_slots,
            risk=primary_risk,
            state_tree=self.state_tree,
        )
        source_requests = pending_source_requests_from_context(
            plan,
            primary_issue,
            missing_slots=missing_slots,
            state_tree=self.state_tree,
        )
        slot_patch_schema = pending_slot_patch_schema(
            plan.action,
            missing_slots,
            source_requests=source_requests,
        )
        prompt_message = pending_question_from_context(
            plan,
            primary_issue,
            missing_slots=missing_slots,
            choices=choices,
            source_requests=source_requests,
            default_question=prompt_message,
        )
        pending_task = PendingTask(
            active_intent=plan.action,
            continue_with=_continue_with(plan),
            source=_pending_source(primary_issue, awaiting_state=status),
            pending_kind=_pending_kind(missing_slots, choices),
            filled_slots=filled_slots,
            missing_slots=missing_slots,
            original_request=self.user_request,
            last_question_key=primary_issue.repair.message_key if primary_issue.repair else primary_issue.message_key,
            last_question_params=dict(primary_issue.repair.params if primary_issue.repair else primary_issue.params),
            awaiting_state=status,
            allowed_actions=allowed_resume_actions(status),
            risk=primary_risk.to_dict() if primary_risk else {},
            risk_code=primary_risk.code if primary_risk else "",
            confirmation_type=primary_risk.category if primary_risk else "",
            choices=choices,
            slot_patch_schema=slot_patch_schema,
            source_requests=source_requests,
            ux_explanation=ux_explanation,
            question=prompt_message,
        )
        display_message = ux_explanation or prompt_message
        result = awaiting_result(
            display_message,
            steps=self.steps,
            state_tree=self.state_tree,
            session_id=self.session_id,
            status=status,
            issues=issues,
            risks=risks,
            pending_task=pending_task,
            repair=primary_issue.repair,
        )
        attach_goal_contract(result, self.user_request)
        return ValidationPause(
            event="repair",
            message=display_message,
            result=result,
            payload={
                "session_id": self.session_id,
                "step_index": step_index,
                "step_total": step_total,
                "action": plan.action,
                "issues": [issue.to_dict() for issue in issues],
                "risks": [risk.to_dict() for risk in risks],
                "risk": primary_risk.to_dict() if primary_risk else {},
                "risk_decision": decision.to_dict(),
                "autonomy_decision": autonomy_decision.to_dict(),
                "repair": primary_issue.repair.to_dict() if primary_issue.repair else {},
                "pending_task": pending_task.to_dict(),
                "ux_explanation": ux_explanation,
                "result": result.to_dict(),
            },
        )

    def _semantic_pause(
        self,
        *,
        plan: ActionPlan,
        issues: list[ValidationIssue],
        risks: list[GISRisk],
        decision: RiskDecision,
        primary_issue: ValidationIssue,
        step_index: int,
        step_total: int,
        ux_explanation: str,
        autonomy_decision: AutonomyDecision,
    ) -> ValidationPause:
        question = primary_issue.repair.message if primary_issue.repair else primary_issue.message
        primary_risk = decision.primary_risk
        pending_task = pending_task_from_issue(
            plan,
            primary_issue,
            original_request=self.user_request,
            state_tree=self.state_tree,
            risk=primary_risk,
        )
        pending_task.ux_explanation = ux_explanation
        pending_task.question = pending_task.question or question
        display_message = ux_explanation or pending_task.question
        result = awaiting_result(
            display_message,
            steps=self.steps,
            state_tree=self.state_tree,
            session_id=self.session_id,
            issues=issues,
            risks=risks,
            pending_task=pending_task,
            repair=primary_issue.repair,
        )
        attach_goal_contract(result, self.user_request)
        return ValidationPause(
            event="question",
            message=display_message,
            result=result,
            payload={
                "session_id": self.session_id,
                "step_index": step_index,
                "step_total": step_total,
                "action": plan.action,
                "issues": [issue.to_dict() for issue in issues],
                "risks": [risk.to_dict() for risk in risks],
                "risk": primary_risk.to_dict() if primary_risk else {},
                "risk_decision": decision.to_dict(),
                "autonomy_decision": autonomy_decision.to_dict(),
                "repair": primary_issue.repair.to_dict() if primary_issue.repair else {},
                "pending_task": pending_task.to_dict(),
                "ux_explanation": ux_explanation,
                "missing_slots": list(primary_issue.params.get("missing_slots") or []),
                "result": result.to_dict(),
            },
        )

    def _explain_pause(
        self,
        *,
        plan: ActionPlan,
        issue: ValidationIssue,
        risk: GISRisk | None,
        awaiting_state: str,
    ) -> str:
        if self.ux_explanation:
            return self.ux_explanation
        if self.ux_explainer is None:
            return ""
        try:
            return str(
                self.ux_explainer(
                    user_request=self.user_request,
                    plan=plan,
                    issue=issue,
                    risk=risk.to_dict() if risk else {},
                    awaiting_state=awaiting_state,
                )
                or ""
            ).strip()
        except Exception:
            return ""


def _attach_autonomy_decision(risks: list[GISRisk], autonomy_decision: AutonomyDecision) -> None:
    payload = autonomy_decision.to_dict()
    for risk in list(risks or []):
        diagnosis = dict(risk.diagnosis or {})
        diagnosis["autonomy_policy"] = payload
        risk.diagnosis = diagnosis


def _primary_issue_for_decision(
    issues: list[ValidationIssue],
    risks: list[GISRisk],
    decision: RiskDecision,
) -> ValidationIssue:
    for issue, risk in zip(issues, risks):
        if risk is decision.primary_risk:
            return issue
    return issues[0]


def pending_task_from_issue(
    plan: ActionPlan,
    issue: ValidationIssue,
    *,
    original_request: str,
    state_tree: Any = None,
    awaiting_state: str = "awaiting_user",
    risk: GISRisk | None = None,
) -> PendingTask:
    normalized = normalize_semantic_input(plan.action, plan.action_input)
    missing_slots = list(issue.params.get("missing_slots") or [])
    missing_slots = _pending_missing_slots(plan.action, missing_slots)
    filled_slots = {
        key: value
        for key, value in normalized.items()
        if key not in missing_slots and value is not None and str(value).strip() != ""
    }
    question = issue.repair.message if issue.repair else issue.message
    choices = pending_choices_from_context(
        plan,
        issue,
        missing_slots=missing_slots,
        risk=risk,
        state_tree=state_tree,
    )
    source_requests = pending_source_requests_from_context(
        plan,
        issue,
        missing_slots=missing_slots,
        state_tree=state_tree,
    )
    slot_patch_schema = pending_slot_patch_schema(
        plan.action,
        missing_slots,
        source_requests=source_requests,
    )
    question = pending_question_from_context(
        plan,
        issue,
        missing_slots=missing_slots,
        choices=choices,
        source_requests=source_requests,
        default_question=question,
    )
    return PendingTask(
        active_intent=plan.action,
        continue_with=_continue_with(plan),
        source=_pending_source(issue, awaiting_state=awaiting_state),
        pending_kind=_pending_kind(missing_slots, choices),
        filled_slots=filled_slots,
        missing_slots=missing_slots,
        original_request=original_request,
        last_question_key=issue.repair.message_key if issue.repair else issue.message_key,
        last_question_params=dict(issue.repair.params if issue.repair else issue.params),
        awaiting_state=awaiting_state,
        allowed_actions=allowed_resume_actions(awaiting_state),
        risk=risk.to_dict() if risk else {},
        risk_code=risk.code if risk else "",
        confirmation_type=risk.category if risk else "",
        choices=choices,
        slot_patch_schema=slot_patch_schema,
        source_requests=source_requests,
        question=question,
    )


def choices_for_missing_slots(choices: list[dict[str, Any]] | tuple[dict[str, Any], ...], missing_slots: list[str]) -> list[dict[str, Any]]:
    return normalize_pending_choices(choices, missing_slots)


def pending_choices_from_context(
    plan: ActionPlan,
    issue: ValidationIssue,
    *,
    missing_slots: list[str],
    risk: GISRisk | None = None,
    state_tree: Any = None,
) -> list[dict[str, Any]]:
    if risk and risk.suggested_choices:
        return choices_for_missing_slots(risk.suggested_choices, missing_slots)
    generated = _generated_choices(plan, issue, missing_slots=missing_slots, state_tree=state_tree)
    return choices_for_missing_slots(generated, missing_slots)


def pending_source_requests_from_context(
    plan: ActionPlan,
    issue: ValidationIssue,
    *,
    missing_slots: list[str],
    state_tree: Any = None,
) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for slot in list(missing_slots or []):
        item = _source_request_for_slot(str(slot or "").strip(), plan, issue, state_tree)
        if item:
            requests.append(item)
    return requests


def pending_slot_patch_schema(
    action: str,
    missing_slots: list[str],
    *,
    source_requests: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if str(action or "").strip() == "export_result":
        schema = export_result_slot_patch_schema()
        return schema
    source_by_slot = {
        str(item.get("slot") or "").strip(): dict(item)
        for item in list(source_requests or [])
        if isinstance(item, dict) and str(item.get("slot") or "").strip()
    }
    schema = normalize_slot_patch_schema({}, missing_slots)
    for slot, item in source_by_slot.items():
        current = dict(schema.get(slot) or {})
        current["source_required"] = True
        current["source_kind"] = str(item.get("kind") or "data_source")
        current["accepted_source_types"] = list(item.get("accepted_source_types") or [])
        current["reason"] = str(item.get("reason") or "")
        schema[slot] = current
    return schema


def pending_question_from_context(
    plan: ActionPlan,
    issue: ValidationIssue,
    *,
    missing_slots: list[str],
    choices: list[dict[str, Any]],
    source_requests: list[dict[str, Any]],
    default_question: str,
) -> str:
    del issue
    if str(plan.action or "").strip() == "export_result":
        return export_result_question(default_question)
    if choices:
        return default_question
    if not missing_slots:
        return default_question
    primary_slot = str(missing_slots[0] or "").strip()
    for item in list(source_requests or []):
        if str(item.get("slot") or "").strip() != primary_slot:
            continue
        return str(item.get("question") or item.get("reason") or default_question)
    return default_question


def _pending_missing_slots(action: str, missing_slots: list[str]) -> list[str]:
    if str(action or "").strip() == "export_result" and "output_path" in {str(item) for item in list(missing_slots or [])}:
        return export_result_missing_slots()
    return list(missing_slots or [])


def _preflight_missing_slots(plan: ActionPlan, issue: ValidationIssue) -> list[str]:
    if issue.code == "unknown_field":
        missing_fields = {str(item) for item in list(issue.params.get("fields") or [])}
        slots = [
            key
            for key, value in dict(plan.action_input or {}).items()
            if str(value) in missing_fields or (isinstance(value, list) and missing_fields.intersection(str(item) for item in value))
        ]
        return slots or ["field"]
    if issue.code == "unknown_layer":
        missing_layer = str(issue.params.get("layer") or "")
        slots = [
            key
            for key, value in dict(plan.action_input or {}).items()
            if str(value) == missing_layer and (key.endswith("_ref") or key.endswith("_refs") or key == "layer_ref")
        ]
        return slots or ["input_ref"]
    return list(issue.params.get("missing_slots") or [])


def _pending_source(issue: ValidationIssue, *, awaiting_state: str) -> str:
    if awaiting_state != "awaiting_user":
        return ""
    missing_slots = list(issue.params.get("missing_slots") or [])
    if issue.code in {"missing_slot", "unknown_field", "unknown_layer"} or missing_slots:
        return "missing_slot_validation"
    return ""


def _pending_kind(missing_slots: list[str], choices: list[dict[str, Any]]) -> str:
    if choices:
        return "choice"
    return "form"


def _continue_with(plan: ActionPlan) -> str:
    return display_title_for_action(plan.action)


def _generated_choices(
    plan: ActionPlan,
    issue: ValidationIssue,
    *,
    missing_slots: list[str],
    state_tree: Any = None,
) -> list[dict[str, Any]]:
    slot = str(missing_slots[0] or "").strip() if missing_slots else ""
    if not slot:
        return []
    if slot == "distance":
        return [
            {"slot": "distance", "value": 100, "label": "100m"},
            {"slot": "distance", "value": 500, "label": "500m"},
            {"slot": "distance", "value": 1000, "label": "1000m"},
        ]
    if slot == "target_crs":
        return _target_crs_choices(plan, state_tree)
    if slot == "predicate":
        return _predicate_choices(plan)
    if slot == "operator":
        return _attribute_operator_choices()
    if slot in {"field", "x_field", "y_field"}:
        return _field_slot_choices(slot, plan, issue, state_tree)
    if slot.endswith("_ref") or slot.endswith("_refs") or slot == "layer_ref":
        return _layer_slot_choices(slot, plan, state_tree)
    return []


def _source_request_for_slot(slot: str, plan: ActionPlan, issue: ValidationIssue, state_tree: Any) -> dict[str, Any]:
    if not slot or not _is_layer_slot(slot):
        return {}
    if _layer_slot_choices(slot, plan, state_tree):
        return {}
    accepted = _accepted_source_types(plan.action, slot)
    if not accepted:
        return {}
    slot_label = format_slots([slot])
    source_label = _source_label(accepted)
    question = render_message(
        "semantic.source_required",
        {"slot_label": slot_label, "source_label": source_label},
    )
    return {
        "slot": slot,
        "kind": "data_source",
        "accepted_source_types": accepted,
        "expected_layer_kind": _expected_layer_kind(plan.action, slot),
        "slot_label": slot_label,
        "source_label": source_label,
        "reason": question,
        "question": question,
        "issue_code": str(issue.code or ""),
    }


def _target_crs_choices(plan: ActionPlan, state_tree: Any) -> list[dict[str, Any]]:
    state = _state_tree_dict(state_tree)
    layer = _resolve_layer_record(str(plan.action_input.get("input_ref") or ""), state)
    if not layer:
        return [{"slot": "target_crs", "value": DEFAULT_PROJECTED_CRS, "label": DEFAULT_PROJECTED_CRS}]
    metadata = dict(layer.get("metadata") or {})
    current_crs = str(metadata.get("crs") or "").strip()
    if current_crs and not _is_geographic_crs(current_crs):
        return [{"slot": "target_crs", "value": current_crs, "label": f"{current_crs} (current projected CRS)"}]
    recommendation = recommend_projected_crs({"metadata": metadata}, task_type="reprojection")
    choices = [{"slot": "target_crs", "value": recommendation.target_crs, "label": recommendation.target_crs}]
    for item in list(recommendation.alternatives or []):
        target = str(item.get("target_crs") or item.get("recommended_crs") or "").strip()
        if target and target != recommendation.target_crs:
            choices.append({"slot": "target_crs", "value": target, "label": target})
    return choices


def _field_slot_choices(slot: str, plan: ActionPlan, issue: ValidationIssue, state_tree: Any) -> list[dict[str, Any]]:
    state = _state_tree_dict(state_tree)
    layer = _resolve_layer_record(str(plan.action_input.get("input_ref") or ""), state)
    if not layer:
        layer_name = str(issue.params.get("layer") or "")
        layer = _resolve_layer_by_name(layer_name, state)
    if not layer:
        return []
    records = field_records(dict(layer.get("metadata") or {}))
    fields = [str(item.get("name") or "") for item in records if str(item.get("name") or "").strip()]
    if slot in {"x_field", "y_field"}:
        ranked = _rank_coordinate_fields(fields, axis="x" if slot == "x_field" else "y")
    else:
        ranked = fields[:8]
    by_name = {str(item.get("name") or ""): item for item in records}
    choices: list[dict[str, Any]] = []
    for name in ranked[:8]:
        record = by_name.get(name, {})
        choice = {"slot": slot, "value": name, "label": name}
        if record.get("type"):
            choice["type"] = record.get("type")
        if "null_count" in record:
            choice["null_count"] = record.get("null_count")
        if record.get("sample_values"):
            choice["sample"] = list(record.get("sample_values") or [])
        choices.append(choice)
    return choices


def _layer_slot_choices(slot: str, plan: ActionPlan, state_tree: Any) -> list[dict[str, Any]]:
    state = _state_tree_dict(state_tree)
    layers = [dict(item) for item in list(state.get("layers") or []) if isinstance(item, dict)]
    expected_kind = _expected_layer_kind(plan.action, slot)
    exclude = {str(plan.action_input.get("input_ref") or "").strip()}
    if slot == "input_ref":
        exclude = set()
    choices: list[dict[str, Any]] = []
    for layer in layers:
        layer_id = str(layer.get("layer_id") or "").strip()
        name = str(layer.get("name") or layer_id).strip()
        if not layer_id or not name or layer_id in exclude or name in exclude:
            continue
        kind = str(layer.get("kind") or "")
        if expected_kind and kind != expected_kind:
            continue
        metadata = dict(layer.get("metadata") or {})
        choices.append(
            {
                "slot": slot,
                "value": layer_id,
                "label": name,
                "layer_id": layer_id,
                "kind": kind,
                "crs": str(metadata.get("crs") or ""),
                "geometry_type": str(metadata.get("geometry_type") or ""),
                "feature_count": metadata.get("feature_count"),
            }
        )
    return choices[:8]


def _predicate_choices(plan: ActionPlan) -> list[dict[str, Any]]:
    options = {
        "extract_by_location": ["intersects", "within", "contains", "touches", "overlaps", "disjoint"],
        "join_by_location": ["intersects", "within", "contains", "touches", "overlaps", "disjoint"],
    }.get(str(plan.action or "").strip(), ["intersects", "within", "contains"])
    return [{"slot": "predicate", "value": item, "label": item} for item in options]


def _attribute_operator_choices() -> list[dict[str, Any]]:
    options = ["=", "!=", ">", ">=", "<", "<=", "contains", "does_not_contain", "is_null", "is_not_null"]
    return [{"slot": "operator", "value": item, "label": item} for item in options]


def _expected_layer_kind(action: str, slot: str) -> str:
    definition = tool_definition_for_action(action)
    if definition is None:
        return ""
    for candidate_slot, expected_kind in definition.layer_requirements:
        if candidate_slot == slot:
            return str(expected_kind or "").strip()
    return ""


def _is_layer_slot(slot: str) -> bool:
    return slot.endswith("_ref") or slot.endswith("_refs") or slot in {"layer_ref", "input_ref", "overlay_ref"}


def _accepted_source_types(action: str, slot: str) -> list[str]:
    expected_kind = _expected_layer_kind(action, slot)
    if expected_kind == "vector":
        return ["vector"]
    if expected_kind == "raster":
        return ["raster"]
    if expected_kind == "table":
        return ["csv"]
    return []


def _source_label(source_types: list[str]) -> str:
    labels_by_locale = {
        "zh-CN": {
            "vector": "矢量图层",
            "raster": "栅格图层",
            "csv": "CSV 表格",
        },
        "en-US": {
            "vector": "vector layer",
            "raster": "raster layer",
            "csv": "CSV table",
        },
    }
    labels = labels_by_locale.get(get_locale(), labels_by_locale["zh-CN"])
    first = str((source_types or ["data"])[0] or "").strip().lower()
    return labels.get(first, first or "数据")


def _state_tree_dict(state_tree: Any) -> dict[str, Any]:
    if hasattr(state_tree, "to_dict"):
        return dict(state_tree.to_dict() or {})
    return dict(state_tree or {})


def _resolve_layer_record(layer_ref: str, state_tree: dict[str, Any]) -> dict[str, Any]:
    ref = str(layer_ref or "").strip()
    if not ref:
        return {}
    aliases = dict(state_tree.get("aliases") or {})
    layer_id = str(aliases.get(ref) or ref)
    for layer in list(state_tree.get("layers") or []):
        if not isinstance(layer, dict):
            continue
        if str(layer.get("layer_id") or "") == layer_id:
            return dict(layer)
    return {}


def _resolve_layer_by_name(name: str, state_tree: dict[str, Any]) -> dict[str, Any]:
    target = str(name or "").strip().lower()
    if not target:
        return {}
    for layer in list(state_tree.get("layers") or []):
        if not isinstance(layer, dict):
            continue
        if str(layer.get("name") or "").strip().lower() == target:
            return dict(layer)
    return {}


def _rank_coordinate_fields(fields: list[str], *, axis: str) -> list[str]:
    preferred = ("x", "lon", "lng", "longitude", "经度") if axis == "x" else ("y", "lat", "latitude", "纬度")
    ranked = sorted(
        fields,
        key=lambda name: (
            0 if any(token in name.lower() for token in preferred if token.isascii()) or any(token in name for token in preferred if not token.isascii()) else 1,
            fields.index(name),
        ),
    )
    return ranked


def _is_geographic_crs(crs: str) -> bool:
    text = str(crs or "").strip().upper()
    return text in {"EPSG:4326", "EPSG:4490", "CRS:84"} or "WGS 84" in text or "GEOGRAPHIC" in text
