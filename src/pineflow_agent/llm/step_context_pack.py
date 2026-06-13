"""Step-scoped context packing for ReAct prompt assembly."""

from __future__ import annotations

from typing import Any

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.llm.context_builder import compact_layer, compact_observation, compact_value


MAX_ACTIVE_LAYERS = 8
MAX_COMPLETED_STEPS = 6
MAX_PENDING_STEPS = 6
MAX_RECENT_OBSERVATIONS = 5
MAX_ACTIVE_RISKS = 5


def build_step_context_pack(
    *,
    user_request: str,
    state: dict[str, Any] | None = None,
    previous_steps: list[dict[str, Any]] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    run_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact task-state view for the current ReAct turn."""
    context = dict(run_context or {})
    workflow = _workflow_plan(context)
    current_step = _current_step(context, workflow)
    completed_steps = _steps_by_status(workflow, {"completed", "skipped"})[-MAX_COMPLETED_STEPS:]
    failed_steps = _steps_by_status(workflow, {"failed", "awaiting_user", "awaiting_confirmation", "paused", "cancelled"})
    pending_steps = _steps_by_status(workflow, {"pending", "running"})[:MAX_PENDING_STEPS]
    compact_steps = [step for step in list(previous_steps or []) if isinstance(step, dict)]
    pack = {
        "goal": _goal(context, workflow, user_request),
        "current_step": _compact_workflow_step(current_step),
        "completed_steps": [_compact_workflow_step(step) for step in completed_steps],
        "pending_steps": [_compact_workflow_step(step) for step in pending_steps],
        "failed_steps": [_compact_workflow_step(step) for step in failed_steps[-3:]],
        "active_layers": _active_layers(state),
        "active_risks": _active_risks(compact_steps),
        "recent_relevant_observations": _recent_relevant_observations(compact_steps),
        "expected_outputs": _expected_outputs(current_step, workflow, context),
        "recent_artifacts": _recent_artifacts(artifacts),
    }
    return make_json_safe({key: value for key, value in pack.items() if value not in ("", [], {})})


def compact_steps_for_prompt(previous_steps: list[dict[str, Any]], *, max_steps: int = 6) -> list[dict[str, Any]]:
    """Keep failed/output-producing steps plus recent steps for the prompt."""
    steps = [dict(step) for step in list(previous_steps or []) if isinstance(step, dict)]
    if len(steps) <= max_steps:
        return steps
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add(step: dict[str, Any]) -> None:
        index = int(step.get("index") or len(selected) + 1)
        if index in seen:
            return
        seen.add(index)
        selected.append(step)

    for step in steps:
        observation = step.get("observation") if isinstance(step.get("observation"), dict) else {}
        status = str(observation.get("status") or "")
        if status and status != "success":
            add(step)
    for step in steps:
        observation = step.get("observation") if isinstance(step.get("observation"), dict) else {}
        if observation.get("output_layer_id") or observation.get("output_path"):
            add(step)
    for step in steps[-max_steps:]:
        add(step)
    return sorted(selected[-max_steps:], key=lambda item: int(item.get("index") or 0))


def _workflow_plan(context: dict[str, Any]) -> dict[str, Any]:
    workflow = context.get("workflow_plan") or context.get("workflow")
    if isinstance(workflow, dict):
        return dict(workflow)
    return {}


def _current_step(context: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
    current = context.get("current_step")
    if isinstance(current, dict) and current:
        return dict(current)
    for step in list(workflow.get("steps") or []):
        if isinstance(step, dict) and str(step.get("status") or "") in {"running", "pending"}:
            return dict(step)
    return {}


def _steps_by_status(workflow: dict[str, Any], statuses: set[str]) -> list[dict[str, Any]]:
    return [
        dict(step)
        for step in list(workflow.get("steps") or [])
        if isinstance(step, dict) and str(step.get("status") or "") in statuses
    ]


def _goal(context: dict[str, Any], workflow: dict[str, Any], user_request: str) -> str:
    goal_contract = context.get("goal_contract")
    if isinstance(goal_contract, dict) and goal_contract.get("goal"):
        return str(goal_contract.get("goal") or "")
    if workflow.get("goal"):
        return str(workflow.get("goal") or "")
    return str(user_request or "")


def _compact_workflow_step(step: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(step, dict) or not step:
        return {}
    payload = {
        "step_id": str(step.get("step_id") or step.get("id") or ""),
        "objective": str(step.get("objective") or step.get("title") or ""),
        "status": str(step.get("status") or ""),
        "expected_outputs": list(step.get("expected_outputs") or [])[:5],
        "verification_checks": list(step.get("verification_checks") or [])[:5],
        "failure_reason": str(step.get("failure_reason") or ""),
    }
    return {key: value for key, value in payload.items() if value not in ("", [], {})}


def _active_layers(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    layers = [layer for layer in list((state or {}).get("layers") or []) if isinstance(layer, dict)]
    return [compact_layer(layer) for layer in layers[-MAX_ACTIVE_LAYERS:]]


def _active_risks(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for step in steps[-12:]:
        observation = step.get("observation") if isinstance(step, dict) else {}
        data = observation.get("data") if isinstance(observation, dict) else {}
        if not isinstance(data, dict):
            continue
        for key in ("preflight_warnings", "postflight_warnings", "warnings", "quality_findings"):
            for item in list(data.get(key) or []):
                if isinstance(item, dict):
                    risks.append(
                        {
                            "step_index": step.get("index"),
                            "action": str(step.get("action") or ""),
                            "code": str(item.get("code") or ""),
                            "message": str(item.get("message") or ""),
                            "severity": str(item.get("severity") or ""),
                        }
                    )
    return make_json_safe(risks[-MAX_ACTIVE_RISKS:])


def _recent_relevant_observations(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for step in steps[-12:]:
        observation = step.get("observation") if isinstance(step, dict) else {}
        if not isinstance(observation, dict):
            continue
        data = observation.get("data") if isinstance(observation.get("data"), dict) else {}
        summary = data.get("observation_summary") if isinstance(data, dict) else {}
        item = {
            "step_index": step.get("index"),
            "action": str(step.get("action") or ""),
            "summary": compact_value(summary) if isinstance(summary, dict) and summary else compact_observation(observation),
        }
        status = str(observation.get("status") or "")
        if status != "success" or observation.get("output_layer_id") or observation.get("output_path") or summary:
            observations.append(item)
    return make_json_safe(observations[-MAX_RECENT_OBSERVATIONS:])


def _expected_outputs(current_step: dict[str, Any], workflow: dict[str, Any], context: dict[str, Any]) -> list[Any]:
    if isinstance(current_step, dict) and current_step.get("expected_outputs"):
        return list(current_step.get("expected_outputs") or [])[:6]
    if isinstance(workflow, dict) and workflow.get("required_outputs"):
        return list(workflow.get("required_outputs") or [])[:6]
    goal_contract = context.get("goal_contract")
    if isinstance(goal_contract, dict):
        return list(goal_contract.get("required_outputs") or [])[:6]
    return []


def _recent_artifacts(artifacts: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in list(artifacts or [])[-5:]:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "artifact_id": str(item.get("artifact_id") or ""),
                "role": str(item.get("role") or ""),
                "name": str(item.get("name") or ""),
                "path": str(item.get("path") or item.get("output_path") or ""),
                "layer_id": str(item.get("layer_id") or ""),
                "source_action": str(item.get("source_action") or ""),
            }
        )
    return make_json_safe([{key: value for key, value in item.items() if value} for item in result])
