"""Run snapshot contract helpers."""

from __future__ import annotations

from typing import Any

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.orchestration.agent.report_audit import normalize_report_audit
from pineflow_api.contracts.decision_rows import build_decision_rows, normalize_decision_rows
from pineflow_api.contracts.pending_tasks import normalize_pending_task
from pineflow_api.contracts.run_lifecycle import RunStatus, normalize_run_status


def normalize_run_snapshot(
    value: Any,
    *,
    run_id: str = "",
    session_id: str = "",
    status: str = "",
    updated_at: str = "",
) -> dict[str, Any]:
    """Normalize the run-scoped recovery snapshot without dropping legacy fields."""
    payload = make_json_safe(dict(value or {})) if isinstance(value, dict) else {}
    result = make_json_safe(dict(payload.get("result") or {})) if isinstance(payload.get("result"), dict) else {}
    effective_status = normalize_run_status(
        status or payload.get("status") or result.get("status") or "",
        default=RunStatus.RUNNING,
    )
    result["status"] = effective_status
    snapshot = dict(payload)
    snapshot["run_id"] = str(run_id or payload.get("run_id") or "")
    snapshot["session_id"] = str(session_id or payload.get("session_id") or "")
    snapshot["status"] = effective_status
    snapshot["result"] = result
    if updated_at or payload.get("updated_at"):
        snapshot["updated_at"] = str(updated_at or payload.get("updated_at") or "")

    pending_task = normalize_pending_task(payload.get("pending_task") or result.get("pending_task"))
    snapshot["pending_task"] = pending_task
    result["pending_task"] = pending_task

    transcript = _dict(payload.get("transcript") or result.get("transcript"))
    snapshot["transcript"] = transcript
    result["transcript"] = transcript

    workflow = _dict(payload.get("workflow"))
    snapshot["workflow"] = workflow

    prior_steps = _list(payload.get("prior_steps"))
    if prior_steps:
        snapshot["prior_steps"] = prior_steps

    tool_state = _dict(payload.get("tool_state"))
    snapshot["tool_state"] = tool_state

    quality_findings = _quality_findings(payload.get("quality_findings") or result.get("quality_findings"))
    snapshot["quality_findings"] = quality_findings
    result["quality_findings"] = quality_findings
    report_audit = normalize_report_audit(payload.get("report_audit") or result.get("report_audit"))
    if report_audit and any(report_audit.get(key) for key in report_audit):
        snapshot["report_audit"] = report_audit
        result["report_audit"] = report_audit
    decision_rows = normalize_decision_rows(payload.get("decision_rows") or result.get("decision_rows"))
    if not decision_rows:
        decision_rows = build_decision_rows(result)
    snapshot["decision_rows"] = decision_rows
    result["decision_rows"] = decision_rows
    return make_json_safe(snapshot)


def _dict(value: Any) -> dict[str, Any]:
    return make_json_safe(dict(value or {})) if isinstance(value, dict) and value else {}


def _list(value: Any) -> list[Any]:
    return make_json_safe(list(value or [])) if isinstance(value, list) and value else []


def _quality_findings(value: Any) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        payload["code"] = str(payload.get("code") or "quality_finding")
        payload["severity"] = str(payload.get("severity") or "warning")
        payload["message"] = str(payload.get("message") or payload["code"])
        payload["blocking"] = bool(payload.get("blocking"))
        payload["detail"] = _dict(payload.get("detail"))
        payload["affected_artifacts"] = [
            make_json_safe(dict(artifact))
            for artifact in list(payload.get("affected_artifacts") or [])
            if isinstance(artifact, dict)
        ]
        findings.append(make_json_safe(payload))
    return findings
