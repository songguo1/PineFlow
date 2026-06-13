"""Data contracts for the standalone ReAct GIS agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Literal

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.policies.output_policy import should_expose_layer_output
from pineflow_agent.rules.validation import PendingTask, RepairProposal, ValidationIssue, issues_to_dict
from pineflow_agent.risks.models import GISRisk

LayerKind = Literal["vector", "raster", "table", "memory", "unknown"]
StepStatus = Literal["success", "error"]


@dataclass
class ActionPlan:
    """One LLM-generated ReAct step."""

    thought: str
    action: str
    action_input: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ActionPlan":
        action = str(payload.get("action") or payload.get("tool") or payload.get("action_name") or "").strip()
        action_input = _normalize_action_input(payload)
        if action == "final_answer" and "message" not in action_input and payload.get("message") is not None:
            action_input["message"] = payload.get("message")
        action_input.pop("step_status", None)
        return cls(
            thought=str(payload.get("thought") or "").strip(),
            action=action,
            action_input=action_input,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "thought": self.thought,
            "action": self.action,
            "action_input": dict(self.action_input),
        }


def _normalize_action_input(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("action_input", "arguments", "args", "input", "params"):
        value = payload.get(key)
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and value.strip().startswith("{"):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return dict(parsed)
    return {}


@dataclass
class Observation:
    """Tool execution feedback returned into the ReAct loop."""

    status: StepStatus
    message: str
    output_layer_id: str = ""
    output_path: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == "success"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "message": self.message,
            "output_layer_id": self.output_layer_id,
            "output_path": self.output_path,
            "data": dict(self.data),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Observation":
        return cls(
            status=str(payload.get("status") or "error"),
            message=str(payload.get("message") or ""),
            output_layer_id=str(payload.get("output_layer_id") or ""),
            output_path=str(payload.get("output_path") or ""),
            data=dict(payload.get("data") or {}),
        )


@dataclass
class ReActStep:
    index: int
    thought: str
    action: str
    action_input: dict[str, Any]
    observation: Observation
    attempt_no: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "thought": self.thought,
            "action": self.action,
            "action_input": dict(self.action_input),
            "observation": self.observation.to_dict(),
            "attempt_no": self.attempt_no,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReActStep":
        return cls(
            index=int(payload.get("index") or 0),
            thought=str(payload.get("thought") or ""),
            action=str(payload.get("action") or ""),
            action_input=dict(payload.get("action_input") or {}),
            observation=Observation.from_dict(dict(payload.get("observation") or {})),
            attempt_no=int(payload.get("attempt_no") or 0),
        )


@dataclass
class AgentResult:
    success: bool
    final_message: str
    steps: list[ReActStep] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    status: str = ""
    logs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    next_question: str = ""
    issues: list[ValidationIssue] = field(default_factory=list)
    risks: list[GISRisk] = field(default_factory=list)
    pending_task: PendingTask | None = None
    repair: RepairProposal | None = None
    goal_contract: dict[str, Any] = field(default_factory=dict)
    quality_findings: list[dict[str, Any]] = field(default_factory=list)
    report_audit: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        state_tree = make_json_safe(self.state)
        react_trace = [step.to_dict() for step in self.steps]
        logs = list(self.logs) or [step.observation.message for step in self.steps if step.observation.message]
        errors = list(self.errors) or [
            step.observation.message for step in self.steps if not step.observation.is_success
        ]
        status = self.status or ("completed" if self.success else "failed")
        risks = list(self.risks)
        if not risks and self.issues:
            from pineflow_agent.risks.converters import risks_from_issues
            risks = risks_from_issues(self.issues, state_tree=state_tree)
        if self.steps:
            from pineflow_agent.risks.converters import risk_from_warning
            for step in self.steps:
                data = step.observation.data or {}
                for key in ("preflight_warnings", "postflight_warnings"):
                    for warning in list(data.get(key) or []):
                        if isinstance(warning, dict):
                            risks.append(risk_from_warning(warning, tool_name=step.action))
        risks = _dedupe_risks(risks)
        return {
            "session_id": self.session_id,
            "status": status,
            "success": self.success,
            "final_message": self.final_message,
            "state_tree": state_tree,
            "outputs": self._collect_outputs(state_tree),
            "logs": make_json_safe(logs),
            "errors": make_json_safe(errors),
            "next_question": self.next_question,
            "issues": issues_to_dict(self.issues),
            "risks": [risk.to_dict() for risk in risks],
            "pending_task": self.pending_task.to_dict() if self.pending_task else {},
            "repair": self.repair.to_dict() if self.repair else {},
            "goal_contract": make_json_safe(dict(self.goal_contract or {})),
            "quality_findings": make_json_safe(list(self.quality_findings or [])),
            "report_audit": make_json_safe(dict(self.report_audit or {})),
        }

    @staticmethod
    def _collect_outputs(state_tree: dict[str, Any]) -> list[dict[str, Any]]:
        from pineflow_agent.core.artifacts import ArtifactRecord

        outputs: list[dict[str, Any]] = []
        for layer in list(state_tree.get("layers") or []):
            if not isinstance(layer, dict):
                continue
            if not should_expose_layer_output(layer):
                continue
            algorithm_id = str(layer.get("algorithm_id") or "")
            role = "final" if algorithm_id == "export_result" else "intermediate"
            outputs.append(
                ArtifactRecord.from_layer(
                    dict(layer),
                    role=role,
                    artifact_id=str(layer.get("artifact_id") or layer.get("layer_id") or layer.get("name") or ""),
                ).output_dict()
            )
        return outputs

    def get_react_trace(self) -> list[dict[str, Any]]:
        """Return ReAct trace for internal agent continuation.

        This is the canonical structured step list for TurnContext,
        report audit, and workspace state persistence. It must NOT
        leak to the API surface — the API serves transcript.timeline.
        """
        return [step.to_dict() for step in self.steps]

def _dedupe_risks(risks: list[GISRisk]) -> list[GISRisk]:
    seen: set[tuple[str, str, str]] = set()
    result: list[GISRisk] = []
    for risk in risks:
        key = (risk.code, risk.stage, risk.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(risk)
    return result


def react_steps_from_payload(payload: list[Any] | None) -> list[ReActStep]:
    steps: list[ReActStep] = []
    for item in list(payload or []):
        if isinstance(item, ReActStep):
            steps.append(item)
            continue
        if not isinstance(item, dict):
            continue
        steps.append(ReActStep.from_dict(item))
    return steps
