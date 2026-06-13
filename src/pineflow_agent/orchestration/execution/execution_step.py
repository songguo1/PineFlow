"""Execution and event streaming for one concrete tool action."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Callable

from pineflow_agent.orchestration.event_stream import EventHandler, emit_event
from pineflow_agent.orchestration.execution.tool_runtime_events import ToolRuntimeEventEmitter
from pineflow_agent.orchestration.hooks.contexts import HookPoint, ObservationContext
from pineflow_agent.orchestration.hooks.pipeline import get_pipeline
from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.core.models import ActionPlan, Observation, ReActStep
from pineflow_agent.core.state_tree import GISStateTree
from pineflow_agent.core.artifacts import ArtifactIndex, ArtifactRecord
from pineflow_agent.tools.contracts.tool_definitions import tool_definition_for_action

ExecuteAction = Callable[[ActionPlan], Observation]
ObservationNarrator = Callable[[ActionPlan, Observation, dict[str, Any]], str]
KeyEventNarrator = Callable[[str, dict[str, Any]], str]


def execute_action_step(
    plan: ActionPlan,
    *,
    index: int,
    step_total: int,
    steps: list[ReActStep],
    on_event: EventHandler | None,
    session_id: str,
    state: GISStateTree,
    execute_action: ExecuteAction,
    artifact_index: ArtifactIndex | None = None,
    source_run_id: str = "",
    attempt_no: int = 0,
    preflight_warnings: list[Any] | None = None,
    observation_narrator: ObservationNarrator | None = None,
    key_event_narrator: KeyEventNarrator | None = None,
) -> Observation:
    """Execute one action and emit the workflow events around it."""
    emit_event(
        on_event,
        "step_start",
        f"{step_label(index, step_total)}: {step_title(plan)}",
        session_id=session_id,
        step_index=index,
        step_total=step_total,
        action=plan.action,
        attempt_no=attempt_no,
    )
    command = command_text(plan)
    if plan.action == "export_result":
        export_payload = _before_export_payload(plan, state)
        _runtime_events(on_event, session_id=session_id, step_index=index, step_total=step_total, attempt_no=attempt_no).before_export(
            plan,
            message=_key_event_message(
                key_event_narrator,
                "before_export",
                {"export": export_payload, "action": plan.action, "action_input": plan.action_input},
                fallback=_before_export_message(export_payload),
            ),
            export=export_payload,
        )
    _runtime_events(
        on_event,
        session_id=session_id,
        step_index=index,
        step_total=step_total,
        attempt_no=attempt_no,
    ).tool_started(plan, message=step_title(plan), command=command)

    started_at = perf_counter()
    observation = execute_action(plan)
    execution_ms = round((perf_counter() - started_at) * 1000, 2)
    _attach_timing(observation, qgis_runtime_ms=execution_ms)
    if observation_narrator is not None:
        _attach_ux_summary(plan, observation, state=state, observation_narrator=observation_narrator)
    record_observation_step(
        plan,
        observation,
        index=index,
        step_total=step_total,
        steps=steps,
        on_event=on_event,
        session_id=session_id,
        state=state,
        artifact_index=artifact_index,
        source_run_id=source_run_id,
        attempt_no=attempt_no,
        preflight_warnings=preflight_warnings,
        key_event_narrator=key_event_narrator,
    )
    return observation


def _runtime_events(
    on_event: EventHandler | None,
    *,
    session_id: str,
    step_index: int,
    step_total: int = 0,
    attempt_no: int = 0,
) -> ToolRuntimeEventEmitter:
    return ToolRuntimeEventEmitter(
        on_event,
        session_id=session_id,
        step_index=step_index,
        step_total=step_total,
        attempt_no=attempt_no,
    )


def record_observation_step(
    plan: ActionPlan,
    observation: Observation,
    *,
    index: int,
    step_total: int,
    steps: list[ReActStep],
    on_event: EventHandler | None,
    session_id: str,
    state: GISStateTree,
    artifact_index: ArtifactIndex | None = None,
    source_run_id: str = "",
    attempt_no: int = 0,
    preflight_warnings: list[Any] | None = None,
    key_event_narrator: KeyEventNarrator | None = None,
) -> None:
    normalized_preflight_warnings = _risk_enriched_preflight_warnings(
        list(preflight_warnings or []),
        tool_name=plan.action,
    )
    if normalized_preflight_warnings:
        data = dict(observation.data or {})
        data["preflight_warnings"] = normalized_preflight_warnings
        observation.data = data

    hooks = get_pipeline()
    obs_ctx = ObservationContext(
        plan=plan,
        observation=observation,
        step_index=index,
        state=state,
        artifact_index=artifact_index,
        source_run_id=source_run_id,
    )
    obs_ctx = hooks.emit(HookPoint.AFTER_TOOL_CALL, obs_ctx)
    output_artifact = resolve_observation_artifact(observation, artifact_index=artifact_index)
    if output_artifact is not None:
        _attach_output_artifact(observation, output_artifact)
    events = _runtime_events(
        on_event,
        session_id=session_id,
        step_index=index,
        step_total=step_total,
        attempt_no=attempt_no,
    )
    for warning in normalized_preflight_warnings:
        events.warning(
            plan,
            warning,
            source="preflight",
            message=str(warning.get("message") or warning.get("code") or "Preflight warning."),
        )
    for warning in list(obs_ctx.data.get("postflight_warnings") or []):
        if not isinstance(warning, dict):
            continue
        if str(warning.get("code") or "").startswith("empty_"):
            empty_payload = {
                "warning": warning,
                "risk": warning.get("risk") if isinstance(warning.get("risk"), dict) else {},
                "diagnosis": dict(warning.get("risk", {}).get("diagnosis") or warning.get("diagnosis") or {}),
            }
            events.empty_result(
                plan,
                warning=warning,
                message=_key_event_message(
                    key_event_narrator,
                    "empty_result",
                    empty_payload,
                    fallback=_empty_result_message(warning),
                ),
                diagnosis=dict(warning.get("risk", {}).get("diagnosis") or warning.get("diagnosis") or {}),
            )
        events.warning(
            plan,
            warning,
            source="postflight",
            message=str(warning.get("message") or warning.get("code") or "Postflight warning."),
        )

    _attach_observation_summary(plan, observation)

    steps.append(
        ReActStep(
            index=index,
            thought=plan.thought,
            action=plan.action,
            action_input=dict(plan.action_input),
            observation=observation,
            attempt_no=attempt_no,
        )
    )
    artifact_payload = output_artifact.output_dict() if output_artifact is not None else {}
    events.tool_finished(
        plan,
        observation,
        state_tree=state.to_dict(),
        output_artifact=artifact_payload,
        timing=_observation_timing(observation),
    )
    events.artifact_created(plan, observation, artifact=artifact_payload)
    if observation.is_success:
        emit_event(
            on_event,
            "step_complete",
            f"{step_label(index, step_total)} completed.",
            session_id=session_id,
            step_index=index,
            step_total=step_total,
            action=plan.action,
            attempt_no=attempt_no,
        )


def step_title(plan: ActionPlan) -> str:
    definition = tool_definition_for_action(plan.action)
    if definition is None:
        return f"Run {plan.action or 'tool action'}"
    return definition.step_title(plan.action_input)


def _attach_timing(observation: Observation, **timing: float) -> None:
    data = dict(observation.data or {})
    current = dict(data.get("timing") or {})
    for key, value in timing.items():
        current[str(key)] = value
    data["timing"] = current
    observation.data = data


def _observation_timing(observation: Observation) -> dict[str, Any]:
    data = dict(observation.data or {})
    timing = data.get("timing")
    return dict(timing) if isinstance(timing, dict) else {}


def step_label(index: int, step_total: int) -> str:
    total = int(step_total or 0)
    if total > 0:
        return f"Step {index}/{total}"
    return f"Step {index}"


def command_text(plan: ActionPlan) -> str:
    definition = tool_definition_for_action(plan.action)
    if definition is None:
        return plan.action or "unknown_action"
    return definition.command_text(plan.action_input)


def _before_export_payload(plan: ActionPlan, state: GISStateTree) -> dict[str, Any]:
    action_input = dict(plan.action_input or {})
    layer_ref = str(action_input.get("layer_ref") or action_input.get("input_ref") or "").strip()
    output_path = str(action_input.get("output_path") or action_input.get("output") or "").strip()
    payload: dict[str, Any] = {
        "layer_ref": layer_ref,
        "output_path": output_path,
        "output_name": output_path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] if output_path else "",
    }
    try:
        layer = state.resolve(layer_ref)
    except KeyError:
        return payload
    metadata = dict(layer.metadata or {})
    payload.update(
        {
            "layer_id": layer.layer_id,
            "layer_name": layer.name,
            "kind": layer.kind,
            "crs": metadata.get("crs"),
            "geometry_type": metadata.get("geometry_type"),
            "feature_count": metadata.get("feature_count", metadata.get("row_count")),
        }
    )
    return make_json_safe(payload)


def _before_export_message(payload: dict[str, Any]) -> str:
    layer_name = str(payload.get("layer_name") or payload.get("layer_ref") or "当前图层")
    output_name = str(payload.get("output_name") or payload.get("output_path") or "指定位置")
    feature_count = payload.get("feature_count")
    count_text = f"{feature_count} 个要素" if feature_count is not None else "已生成的要素"
    return f"准备导出 {layer_name}（{count_text}）到 {output_name}。"


def _empty_result_message(warning: dict[str, Any]) -> str:
    risk = dict(warning.get("risk") or {})
    diagnosis = dict(risk.get("diagnosis") or warning.get("diagnosis") or {})
    causes = list(diagnosis.get("possible_causes") or [])
    actions = _diagnosis_action_texts(diagnosis)
    if causes:
        return "这一步执行成功但结果为空；可能原因：" + "；".join(str(item) for item in causes[:2]) + "。"
    if actions:
        return "这一步执行成功但结果为空；建议：" + "；".join(str(item) for item in actions[:2]) + "。"
    return str(warning.get("message") or "这一步执行成功但结果为空，需要检查输入范围、筛选条件和 CRS。")


def _diagnosis_action_texts(diagnosis: dict[str, Any]) -> list[str]:
    labels = [
        str(item.get("label") or "").strip()
        for item in list(diagnosis.get("suggested_action_options") or [])
        if isinstance(item, dict)
    ]
    if any(labels):
        return [label for label in labels if label]
    return [
        str(item).strip()
        for item in list(diagnosis.get("suggested_actions") or diagnosis.get("suggested_next_actions") or [])
        if str(item or "").strip()
    ]


def _risk_enriched_preflight_warnings(issues: list[Any], *, tool_name: str) -> list[dict[str, Any]]:
    if not issues:
        return []
    from pineflow_agent.risks.converters import risk_from_issue, warning_from_risk

    warnings: list[dict[str, Any]] = []
    for issue in issues:
        risk = risk_from_issue(issue, tool_name=tool_name)
        payload = warning_from_risk(risk)
        payload["stage"] = str(getattr(issue, "stage", "") or "preflight")
        payload["message"] = str(getattr(issue, "message", "") or risk.message)
        payload["params"] = make_json_safe(dict(getattr(issue, "params", None) or {}))
        warnings.append(payload)
    return warnings


def _attach_ux_summary(
    plan: ActionPlan,
    observation: Observation,
    *,
    state: GISStateTree,
    observation_narrator: ObservationNarrator,
) -> None:
    try:
        summary = observation_narrator(plan, observation, state.to_dict())
    except Exception:
        return
    text = str(summary or "").strip()
    if not text:
        return
    data = dict(observation.data or {})
    data["ux_summary"] = text
    observation.data = data


def _attach_observation_summary(plan: ActionPlan, observation: Observation) -> None:
    data = dict(observation.data or {})
    layer = data.get("layer") if isinstance(data.get("layer"), dict) else {}
    metadata = dict(layer.get("metadata") or {}) if isinstance(layer, dict) else {}
    output_artifact = data.get("output_artifact") if isinstance(data.get("output_artifact"), dict) else {}
    warnings = []
    for key in ("preflight_warnings", "postflight_warnings", "warnings", "quality_findings"):
        for item in list(data.get(key) or []):
            if not isinstance(item, dict):
                continue
            warnings.append(
                {
                    "source": key,
                    "code": str(item.get("code") or ""),
                    "severity": str(item.get("severity") or ""),
                    "message": str(item.get("message") or ""),
                }
            )
    summary = {
        "action": plan.action,
        "status": observation.status,
        "message": observation.message,
        "output_layer_id": observation.output_layer_id,
        "output_path": observation.output_path,
        "layer": {
            "layer_id": str(layer.get("layer_id") or ""),
            "name": str(layer.get("name") or ""),
            "kind": str(layer.get("kind") or ""),
            "crs": str(metadata.get("crs") or ""),
            "geometry_type": str(metadata.get("geometry_type") or ""),
            "feature_count": metadata.get("feature_count"),
            "row_count": metadata.get("row_count"),
            "field_count": metadata.get("field_count"),
        },
        "artifact": {
            "artifact_id": str(output_artifact.get("artifact_id") or ""),
            "role": str(output_artifact.get("role") or ""),
            "name": str(output_artifact.get("name") or ""),
            "path": str(output_artifact.get("path") or ""),
        },
        "warnings": warnings[:8],
        "timing": dict(data.get("timing") or {}),
    }
    data["observation_summary"] = _drop_empty_summary_values(summary)
    observation.data = data


def _drop_empty_summary_values(value: Any) -> Any:
    if isinstance(value, dict):
        result = {
            key: _drop_empty_summary_values(item)
            for key, item in value.items()
            if item not in ("", None, [], {})
        }
        return {key: item for key, item in result.items() if item not in ("", None, [], {})}
    if isinstance(value, list):
        return [_drop_empty_summary_values(item) for item in value if item not in ("", None, [], {})]
    return value


def _key_event_message(
    narrator: KeyEventNarrator | None,
    event: str,
    payload: dict[str, Any],
    *,
    fallback: str,
) -> str:
    if narrator is None:
        return fallback
    try:
        text = str(narrator(event, payload) or "").strip()
    except Exception:
        return fallback
    return text or fallback


def register_observation_artifact(
    observation: Observation,
    *,
    plan: ActionPlan | None = None,
    source_step: int,
    source_run_id: str = "",
    artifact_index: ArtifactIndex | None,
) -> ArtifactRecord | None:
    if artifact_index is None or not observation.is_success:
        return None
    layer = _observation_layer(observation)
    if not layer:
        return None
    role = _artifact_role_for_layer(layer)
    try:
        artifact = artifact_index.register_layer(
            layer,
            role=role,
            source_step=source_step,
            source_run_id=source_run_id,
            source_action=str(getattr(plan, "action", "") or ""),
        )
    except Exception:
        return None
    _attach_output_artifact(observation, artifact)
    return artifact


def resolve_observation_artifact(
    observation: Observation,
    *,
    artifact_index: ArtifactIndex | None,
) -> ArtifactRecord | None:
    layer = _observation_layer(observation)
    if not observation.is_success or not layer:
        return None
    role = _artifact_role_for_layer(layer)
    output_artifact = _observation_output_artifact(observation)
    if artifact_index is not None:
        artifact = artifact_index.find_record(
            artifact_id=str(output_artifact.get("artifact_id") or layer.get("artifact_id") or ""),
            layer_id=str(layer.get("layer_id") or observation.output_layer_id or ""),
            path=str(layer.get("source") or observation.output_path or ""),
            role=role,
        )
        if artifact is not None:
            return artifact
    payload = dict(layer)
    if output_artifact:
        payload.update({key: value for key, value in output_artifact.items() if key not in payload or not payload.get(key)})
    try:
        source_step = output_artifact.get("source_step")
        if source_step in (None, ""):
            normalized_source_step = None
        else:
            normalized_source_step = int(source_step)
        return ArtifactRecord.from_dict(
            ArtifactRecord.from_layer(
                payload,
                role=role,
                source_step=normalized_source_step,
                artifact_id=str(output_artifact.get("artifact_id") or payload.get("artifact_id") or ""),
            ).to_dict()
        )
    except Exception:
        return None


def _observation_layer(observation: Observation) -> dict[str, Any]:
    data = dict(observation.data or {})
    layer = data.get("layer")
    return dict(layer) if isinstance(layer, dict) else {}


def _artifact_role_for_layer(layer: dict[str, Any]) -> str:
    metadata = dict(layer.get("metadata") or {})
    artifact = dict(metadata.get("artifact") or {})
    role = str(layer.get("role") or artifact.get("role") or metadata.get("artifact_role") or "").strip().lower()
    if role in {"input", "intermediate", "final", "report"}:
        return role
    source_action = str(layer.get("source_action") or metadata.get("source_action") or "").strip()
    if source_action in {"load_vector", "load_raster", "load_csv"}:
        return "input"
    return "final" if str(layer.get("algorithm_id") or "") == "export_result" else "intermediate"


def _observation_output_artifact(observation: Observation) -> dict[str, Any]:
    data = dict(observation.data or {})
    artifact = data.get("output_artifact") or data.get("artifact")
    return dict(artifact) if isinstance(artifact, dict) else {}


def _attach_output_artifact(observation: Observation, artifact: ArtifactRecord) -> None:
    data = dict(observation.data or {})
    artifact_payload = artifact.output_dict()
    data["output_artifact"] = artifact_payload
    layer = data.get("layer")
    if isinstance(layer, dict):
        next_layer = dict(layer)
        next_layer.setdefault("artifact_id", artifact.artifact_id)
        next_layer.setdefault("role", artifact.role)
        next_layer.setdefault("lineage", artifact.lineage)
        next_layer.setdefault("source_run_id", artifact.source_run_id)
        next_layer.setdefault("source_action", artifact.source_action)
        next_layer.setdefault("source_step", artifact.source_step)
        next_layer.setdefault("materialized", artifact.materialized)
        next_layer.setdefault("reusable", artifact.reusable)
        data["layer"] = next_layer
    observation.data = data
