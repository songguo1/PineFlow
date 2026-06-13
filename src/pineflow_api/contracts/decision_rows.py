"""Decision row projection for audit-oriented UI panels."""

from __future__ import annotations

from typing import Any

from pineflow_agent.core.json_safety import make_json_safe


def build_decision_rows(result: dict[str, Any], events: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    payload = dict(result or {})
    rows: list[dict[str, Any]] = []
    rows.extend(_report_audit_rows(payload.get("report_audit")))
    rows.extend(_event_rows(events or []))
    rows.extend(_risk_rows(payload.get("risks")))
    rows.extend(_quality_rows(payload.get("quality_findings")))
    return _dedupe_rows(rows)


def normalize_decision_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(value or []) if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        row = {
            "id": str(item.get("id") or ""),
            "kind": str(item.get("kind") or "decision"),
            "status": str(item.get("status") or ""),
            "severity": str(item.get("severity") or "info"),
            "title": str(item.get("title") or item.get("kind") or "Decision"),
            "summary": str(item.get("summary") or ""),
            "details": _details(item.get("details")),
        }
        rows.append(make_json_safe(row))
    return rows


def _event_rows(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        fact = _event_fact(event)
        clarification = fact.get("clarification_decision")
        if isinstance(clarification, dict) and clarification:
            rows.append(
                _row(
                    row_id=f"{fact.get('seq') or len(rows)}-clarification",
                    kind="clarification",
                    status=str(clarification.get("decision") or "answered"),
                    title="Clarification answered",
                    summary=str(clarification.get("question") or fact.get("display_summary") or fact.get("message") or ""),
                    details=[
                        ("Intent", clarification.get("active_intent")),
                        ("Choice", "; ".join(_selected_choice_labels(clarification))),
                        ("Selected CRS", clarification.get("selected_crs")),
                        ("Slot patch", _format_mapping(clarification.get("slot_patch"))),
                    ],
                )
            )
        risk_decision = fact.get("risk_decision")
        if isinstance(risk_decision, dict) and risk_decision:
            risk = dict(fact.get("risk") or {}) if isinstance(fact.get("risk"), dict) else {}
            rows.append(
                _row(
                    row_id=f"{fact.get('seq') or len(rows)}-risk",
                    kind="risk",
                    status=str(risk_decision.get("decision") or risk_decision.get("kind") or "confirmed"),
                    severity=str(risk.get("severity") or "warning"),
                    title="Risk decision",
                    summary=str(risk.get("message") or fact.get("display_summary") or fact.get("message") or ""),
                    details=[
                        ("Risk type", risk.get("category") or risk.get("code")),
                        ("Decision", risk_decision.get("decision") or risk_decision.get("kind")),
                        ("Affects trust", "yes" if risk.get("affects_result_trust") is True else ""),
                    ],
                )
            )
        event_type = str(fact.get("event_type") or "")
        if event_type == "export.before":
            export = dict(fact.get("export") or {}) if isinstance(fact.get("export"), dict) else {}
            rows.append(_export_row(fact, export))
        if event_type == "result.empty":
            warning = dict(fact.get("warning") or {}) if isinstance(fact.get("warning"), dict) else {}
            risk = dict(fact.get("risk") or warning.get("risk") or {}) if isinstance(fact.get("risk") or warning.get("risk"), dict) else {}
            rows.append(_empty_result_row(fact, warning, risk))
        if event_type in {"repair.completed", "repair.failed"}:
            rows.append(_repair_row(fact, event_type))
        risk = dict(fact.get("risk") or {}) if isinstance(fact.get("risk"), dict) else {}
        crs = _crs_recommendation(risk)
        if crs:
            rows.append(_crs_row(crs, risk, row_id=f"{fact.get('seq') or len(rows)}-crs"))
    return rows


def _report_audit_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or not value:
        return []
    rows: list[dict[str, Any]] = []
    payload = dict(value)
    for item in list(payload.get("clarification_decisions") or []):
        if not isinstance(item, dict):
            continue
        rows.append(
            _row(
                row_id=f"audit-clarification-{len(rows)}",
                kind="clarification",
                status=str(item.get("decision") or "answered"),
                title="Clarification answered",
                summary=str(item.get("question") or item.get("message") or ""),
                details=[
                    ("Intent", item.get("active_intent")),
                    ("Choice", "; ".join(_selected_choice_labels(item))),
                    ("Selected CRS", item.get("selected_crs")),
                    ("Slot patch", _format_mapping(item.get("slot_patch"))),
                ],
            )
        )
    for item in list(payload.get("source_loads") or []):
        if not isinstance(item, dict):
            continue
        source = dict(item.get("source") or {})
        alias = item.get("alias") or source.get("alias") or source.get("path")
        rows.append(
            _row(
                row_id=f"audit-source-load-{len(rows)}",
                kind="source_load",
                status=str(item.get("phase") or "loaded"),
                title="Source attached",
                summary=str(item.get("message") or alias or ""),
                details=[
                    ("Source", alias),
                    ("Slot", item.get("slot_label") or item.get("slot")),
                    ("Intent", item.get("active_intent")),
                    ("Path", item.get("path")),
                ],
            )
        )
    for item in list(payload.get("user_confirmations") or []):
        if not isinstance(item, dict):
            continue
        selected = "; ".join(_selected_choice_labels(item))
        rows.append(
            _row(
                row_id=f"audit-decision-{len(rows)}",
                kind="risk",
                status=str(item.get("decision") or "resolved"),
                severity="warning",
                title="User resolution" if selected else "Risk decision",
                summary=str(item.get("message") or item.get("risk_code") or ""),
                details=[
                    ("Risk type", item.get("risk_category") or item.get("risk_code")),
                    ("Decision", item.get("decision")),
                    ("Choice", selected),
                    ("Selected CRS", item.get("selected_crs")),
                    ("Slot patch", _format_mapping(item.get("slot_patch"))),
                ],
            )
        )
    return rows


def _risk_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for risk in list(value or []) if isinstance(value, list) else []:
        if not isinstance(risk, dict):
            continue
        diagnosis = dict(risk.get("diagnosis") or {})
        rows.append(
            _row(
                row_id=f"risk-{risk.get('code') or len(rows)}",
                kind="risk",
                status=str(risk.get("severity") or "warning"),
                severity=str(risk.get("severity") or "warning"),
                title="Runtime risk",
                summary=str(risk.get("message") or risk.get("code") or ""),
                details=[
                    ("Risk type", risk.get("category") or risk.get("code")),
                    ("Blocking", "yes" if risk.get("blocking") is True else ""),
                    ("Affects trust", "yes" if risk.get("affects_result_trust") is True else ""),
                    *_diagnosis_detail_pairs(diagnosis),
                ],
            )
        )
        crs = _crs_recommendation(risk)
        if crs:
            rows.append(_crs_row(crs, risk, row_id=f"crs-{risk.get('code') or len(rows)}"))
    return rows


def _export_row(fact: dict[str, Any], export: dict[str, Any]) -> dict[str, Any]:
    artifact = dict(export.get("artifact") or {}) if isinstance(export.get("artifact"), dict) else {}
    source_artifact = dict(export.get("source_artifact") or {}) if isinstance(export.get("source_artifact"), dict) else {}
    return _row(
        row_id=f"{fact.get('seq') or 0}-export",
        kind="export",
        status="before_export",
        title="Export decision",
        summary=str(
            artifact.get("display_summary")
            or fact.get("display_summary")
            or fact.get("message")
            or export.get("output_path")
            or ""
        ),
        details=[
            ("Artifact", artifact.get("name") or artifact.get("artifact_id")),
            ("Layer", export.get("layer_name") or export.get("layer_ref") or export.get("layer_id")),
            ("Source", source_artifact.get("name") or source_artifact.get("artifact_id")),
            ("Feature count", export.get("feature_count")),
            ("Output path", export.get("output_path")),
            ("CRS", export.get("crs")),
        ],
    )


def _empty_result_row(fact: dict[str, Any], warning: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    diagnosis = dict(fact.get("diagnosis") or risk.get("diagnosis") or warning.get("diagnosis") or {})
    causes = _string_items(diagnosis.get("possible_causes"))
    actions = _diagnosis_action_texts(diagnosis)
    return _row(
        row_id=f"{fact.get('seq') or 0}-empty-result",
        kind="empty_result",
        status=str(risk.get("severity") or warning.get("severity") or "warning"),
        severity=str(risk.get("severity") or warning.get("severity") or "warning"),
        title="Empty result diagnosis",
        summary=str(risk.get("message") or warning.get("message") or fact.get("display_summary") or fact.get("message") or ""),
        details=[
            ("Action", fact.get("action") or warning.get("action")),
            ("Affected artifacts", ", ".join(_artifact_labels(warning.get("affected_artifacts")))),
            ("Possible causes", "; ".join(causes)),
            ("Suggested actions", "; ".join(actions)),
        ],
    )


def _repair_row(fact: dict[str, Any], event_type: str) -> dict[str, Any]:
    audit = dict(fact.get("repair_audit") or {}) if isinstance(fact.get("repair_audit"), dict) else {}
    input_layer = dict(audit.get("input") or {}) if isinstance(audit.get("input"), dict) else {}
    output_layer = dict(audit.get("output") or {}) if isinstance(audit.get("output"), dict) else {}
    return _row(
        row_id=f"{fact.get('seq') or 0}-repair",
        kind="repair",
        status="completed" if event_type == "repair.completed" else "failed",
        severity="info" if event_type == "repair.completed" else "error",
        title="Repair decision",
        summary=str(fact.get("display_summary") or fact.get("message") or audit.get("reason") or ""),
        details=[
            ("Action", audit.get("action") or fact.get("action")),
            ("Decision", audit.get("decision")),
            ("Reason", audit.get("reason") or fact.get("repair_goal")),
            ("Input layer", input_layer.get("name") or input_layer.get("layer_id")),
            ("Output layer", output_layer.get("name") or output_layer.get("layer_id")),
            ("Feature count", _feature_count_change(audit)),
        ],
    )


def _crs_row(recommendation: dict[str, Any], risk: dict[str, Any], *, row_id: str) -> dict[str, Any]:
    target = recommendation.get("target_crs") or recommendation.get("recommended_crs")
    return _row(
        row_id=row_id,
        kind="crs_recommendation",
        status=str(recommendation.get("confidence") or "unknown"),
        severity=str(risk.get("severity") or "warning"),
        title="CRS recommendation",
        summary=str(target or risk.get("message") or ""),
        details=[
            ("Target CRS", target),
            ("Confidence", recommendation.get("confidence")),
            ("Reason", recommendation.get("reason")),
            ("Source", recommendation.get("source")),
            ("Requires confirmation", "yes" if recommendation.get("requires_confirmation") is True else ""),
            ("Alternatives", "; ".join(_crs_alternative_targets(recommendation))),
        ],
    )


def _crs_recommendation(risk: dict[str, Any]) -> dict[str, Any]:
    diagnosis = risk.get("diagnosis")
    if not isinstance(diagnosis, dict):
        return {}
    recommendation = diagnosis.get("crs_recommendation")
    return dict(recommendation) if isinstance(recommendation, dict) and recommendation else {}


def _feature_count_change(audit: dict[str, Any]) -> str:
    before = audit.get("feature_count_before")
    after = audit.get("feature_count_after")
    if before is None and after is None:
        return ""
    return f"{before if before is not None else '?'} -> {after if after is not None else '?'}"


def _quality_rows(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for finding in list(value or []) if isinstance(value, list) else []:
        if not isinstance(finding, dict):
            continue
        detail = dict(finding.get("detail") or {})
        diagnosis = dict(detail.get("diagnosis") or {})
        if not diagnosis:
            risk = dict(detail.get("risk") or {})
            diagnosis = dict(risk.get("diagnosis") or {})
        rows.append(
            _row(
                row_id=f"quality-{finding.get('code') or len(rows)}",
                kind="quality",
                status=str(finding.get("severity") or "quality"),
                severity=str(finding.get("severity") or "info"),
                title="Quality finding",
                summary=str(finding.get("message") or finding.get("code") or ""),
                details=[
                    ("Code", finding.get("code")),
                    ("Blocking", "yes" if finding.get("blocking") is True else ""),
                    ("Affected artifacts", ", ".join(_artifact_labels(finding.get("affected_artifacts")))),
                    *_diagnosis_detail_pairs(diagnosis),
                ],
            )
        )
    return rows


def _row(
    *,
    row_id: str,
    kind: str,
    status: str,
    title: str,
    summary: str,
    details: list[tuple[str, Any]],
    severity: str = "info",
) -> dict[str, Any]:
    return make_json_safe(
        {
            "id": row_id,
            "kind": kind,
            "status": status,
            "severity": severity,
            "title": title,
            "summary": summary,
            "details": _details([{"label": label, "value": value} for label, value in details]),
        }
    )


def _details(value: Any) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for item in list(value or []) if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        text = str(item.get("value") or "").strip()
        if label and text:
            details.append({"label": label, "value": text})
    return details


def _event_fact(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        merged = dict(event)
        merged.update(payload)
        return merged
    return dict(event)


def _selected_choice_labels(decision: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for item in list(decision.get("selected_choices") or []):
        if isinstance(item, dict):
            value = item.get("label") or item.get("value")
        else:
            value = item
        if value:
            labels.append(str(value))
    return labels


def _format_mapping(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    return "; ".join(f"{key}: {item}" for key, item in value.items())


def _string_items(value: Any) -> list[str]:
    return [str(item) for item in list(value or []) if str(item or "").strip()] if isinstance(value, list) else []


def _diagnosis_action_texts(diagnosis: dict[str, Any]) -> list[str]:
    options = [
        str(item.get("label") or "").strip()
        for item in list(diagnosis.get("suggested_action_options") or [])
        if isinstance(item, dict)
    ]
    if any(options):
        return [item for item in options if item]
    return _string_items(diagnosis.get("suggested_actions")) or _string_items(diagnosis.get("suggested_next_actions"))


def _artifact_labels(value: Any) -> list[str]:
    labels: list[str] = []
    for item in list(value or []) if isinstance(value, list) else []:
        if isinstance(item, dict):
            label = item.get("name") or item.get("artifact_id") or item.get("path")
        else:
            label = item
        if label:
            labels.append(str(label))
    return labels


def _crs_alternative_targets(recommendation: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in list(recommendation.get("alternatives") or []):
        if not isinstance(item, dict):
            continue
        target = str(item.get("target_crs") or item.get("recommended_crs") or "").strip()
        if target:
            values.append(target)
    return values


def _diagnosis_detail_pairs(diagnosis: dict[str, Any]) -> list[tuple[str, Any]]:
    if not isinstance(diagnosis, dict) or not diagnosis:
        return []
    pairs: list[tuple[str, Any]] = []
    inputs = _string_items(diagnosis.get("input_layers"))
    if inputs:
        pairs.append(("Input layers", ", ".join(inputs)))
    output_layer = str(diagnosis.get("output_layer") or "").strip()
    if output_layer:
        pairs.append(("Output layer", output_layer))
    predicate = str(diagnosis.get("predicate") or "").strip()
    if predicate:
        pairs.append(("Predicate", predicate))
    field = str(diagnosis.get("field") or "").strip()
    if field:
        pairs.append(("Field", field))
    operator = str(diagnosis.get("operator") or "").strip()
    if operator:
        pairs.append(("Operator", operator))
    if diagnosis.get("value") not in (None, ""):
        pairs.append(("Value", diagnosis.get("value")))
    if diagnosis.get("max_distance") not in (None, ""):
        pairs.append(("Max distance", diagnosis.get("max_distance")))
    if diagnosis.get("discard_nonmatching") is True:
        pairs.append(("Discard nonmatching", "yes"))
    extent_relation = str(diagnosis.get("extent_relation") or "").strip()
    if extent_relation:
        pairs.append(("Extent relation", extent_relation))
    pixel_sizes = _pixel_size_summary(diagnosis)
    if pixel_sizes:
        pairs.append(("Pixel size", pixel_sizes))
    if diagnosis.get("nodata") not in (None, ""):
        pairs.append(("NoData", diagnosis.get("nodata")))
    resampling = _recommended_resampling_text(diagnosis)
    if resampling:
        pairs.append(("Recommended resampling", resampling))
    causes = _string_items(diagnosis.get("possible_causes"))
    if causes:
        pairs.append(("Possible causes", "; ".join(causes[:2])))
    actions = _diagnosis_action_texts(diagnosis)
    if actions:
        pairs.append(("Suggested actions", "; ".join(actions[:2])))
    return pairs


def _pixel_size_summary(diagnosis: dict[str, Any]) -> str:
    left = diagnosis.get("pixel_size_a")
    right = diagnosis.get("pixel_size_b")
    if isinstance(left, list) and isinstance(right, list) and left and right:
        return f"{left} vs {right}"
    return ""


def _recommended_resampling_text(diagnosis: dict[str, Any]) -> str:
    value = diagnosis.get("recommended_resampling")
    if not isinstance(value, dict):
        return ""
    method = str(value.get("method") or "").strip()
    semantics = str(value.get("data_semantics") or "").strip()
    if method and semantics:
        return f"{method} ({semantics})"
    return method or semantics


def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for row in normalize_decision_rows(rows):
        key = f"{row.get('kind')}|{row.get('title')}|{row.get('summary')}|{row.get('status')}"
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result
