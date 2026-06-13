"""Deterministic Markdown final report generation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pineflow_agent.core.models import AgentResult, ReActStep
from pineflow_agent.core.workspace import safe_workspace_name
from pineflow_agent.orchestration.agent.report_audit import (
    build_report_audit_dict,
    normalize_report_audit,
)
from pineflow_agent.tools.contracts.tool_definitions import algorithm_id_for_action, display_title_for_action


REPORT_ARTIFACT_NAME = "analysis_report"
REPORT_ALGORITHM_ID = "analysis_report"
REPORT_LATEST_NAME = "latest_analysis_report.md"


def write_final_report(
    result: AgentResult,
    *,
    toolbox: Any,
    user_request: str,
    runtime_events: list[dict[str, Any]] | None = None,
) -> str:
    """Write a reproducible Markdown report and register it as a report artifact."""

    if str(result.status or "").strip() not in {"", "completed"} and not result.success:
        return ""
    workspace = getattr(toolbox, "workspace", None)
    artifacts = getattr(toolbox, "artifacts", None)
    if workspace is None:
        return ""
    report_run_id = _report_run_id(result, runtime_events=runtime_events)
    report_name = _report_file_name(result, report_run_id=report_run_id)
    report_dir = Path(workspace.outputs_dir) / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / report_name
    latest_report_path = Path(workspace.output_path(REPORT_LATEST_NAME))
    artifact_outputs = _artifact_outputs(artifacts)
    markdown = build_final_report_markdown(
        result,
        user_request=user_request,
        artifact_outputs=artifact_outputs,
        runtime_report=_runtime_report(toolbox),
        runtime_events=runtime_events,
    )
    try:
        report_path.write_text(markdown, encoding="utf-8")
        latest_report_path.write_text(markdown, encoding="utf-8")
    except OSError:
        return ""
    if artifacts is not None and hasattr(artifacts, "register_layer"):
        try:
            artifacts.register_layer(
                {
                    "layer_id": f"report_{safe_workspace_name(report_run_id or result.session_id or 'session')}_{_timestamp()}",
                    "name": REPORT_ARTIFACT_NAME,
                    "kind": "report",
                    "source": str(report_path),
                    "algorithm_id": REPORT_ALGORITHM_ID,
                    "metadata": {
                        "fields": [],
                        "quality_flags": _warning_codes(result.steps),
                        "latest_report_path": str(latest_report_path),
                        "source_run_id": report_run_id,
                    },
                },
                role="report",
                source_run_id=report_run_id,
                source_action=REPORT_ALGORITHM_ID,
            )
        except Exception:
            return str(report_path)
    return str(report_path)


def build_final_report_markdown(
    result: AgentResult,
    *,
    user_request: str,
    artifact_outputs: list[dict[str, Any]] | None = None,
    runtime_report: dict[str, Any] | None = None,
    runtime_events: list[dict[str, Any]] | None = None,
) -> str:
    steps = list(result.steps or [])
    audit_payload = _report_audit_payload(
        result,
        runtime_events=runtime_events,
        artifact_outputs=artifact_outputs,
    )
    warnings = [dict(item) for item in list(audit_payload.get("warnings") or []) if isinstance(item, dict)]
    repair_steps = _repair_steps(steps)
    audit_decisions = [dict(item) for item in list(audit_payload.get("user_confirmations") or []) if isinstance(item, dict)]
    clarification_decisions = [
        dict(item) for item in list(audit_payload.get("clarification_decisions") or [])
        if isinstance(item, dict)
    ]
    source_loads = [dict(item) for item in list(audit_payload.get("source_loads") or []) if isinstance(item, dict)]
    repairs = [dict(item) for item in list(audit_payload.get("repairs") or []) if isinstance(item, dict)]
    audit_repairs = [dict(item.get("repair_audit") or item) for item in repairs if isinstance(item, dict)]
    outputs = [dict(item) for item in list(audit_payload.get("artifacts") or []) if isinstance(item, dict)]
    outputs = outputs or list(result.to_dict().get("outputs") or [])
    final_outputs = [item for item in outputs if _is_final_artifact(item)]
    inputs = _input_artifacts(outputs, source_loads=source_loads) or _input_layers(dict(result.state or {}))
    trust = _trust_assessment(warnings=warnings, repairs=repairs or audit_repairs or repair_steps, outputs=outputs)
    runtime = dict(runtime_report or {})
    goal_contract = dict(result.goal_contract or {})
    lines = [
        "# PineFlow GIS Analysis Report",
        "",
        "## 任务",
        str(user_request or "").strip() or "-",
        "",
        "## 目标契约",
        f"- 目标：{goal_contract.get('goal') or str(user_request or '').strip() or '-'}",
        f"- 来源：{goal_contract.get('source') or '未记录'}",
        *_goal_contract_lines(goal_contract),
        "",
        "## 结论",
        str(result.final_message or "").strip() or "-",
        "",
        "## 输入数据",
    ]
    if inputs:
        for layer in inputs:
            lines.append(
                f"- {layer['name']}: {layer['source']} "
                f"({layer['kind']}, CRS={layer['crs']}, geometry={layer['geometry_type']}, "
                f"features={layer['feature_count']}, fields={layer['field_count']})"
            )
    else:
        lines.append("- 未记录独立输入图层。")
    lines.extend(["", "## 输出结果"])
    if final_outputs:
        for output in final_outputs:
            summary = str(output.get("display_summary") or "").strip()
            path = str(output.get("path") or "").strip()
            if summary:
                lines.append(f"- {summary}")
                if path:
                    lines.append(f"  - 文件：{path}")
            else:
                lines.append(
                    f"- {output.get('name') or output.get('layer_id') or 'output'}: "
                    f"{path} "
                    f"({output.get('kind') or 'unknown'}, {output.get('feature_count', 'unknown')} features)"
                )
    else:
        lines.append("- 未记录最终输出文件。")
    lines.extend(["", "## 产物登记"])
    artifact_catalog_lines = _artifact_catalog_lines(outputs)
    if artifact_catalog_lines:
        lines.extend(artifact_catalog_lines)
    else:
        lines.append("- 未记录中间或最终产物。")
    lines.extend(["", "## 数据质量和风险提示"])
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning.get('message') or warning.get('code') or 'warning'}")
            detail_lines = _warning_detail_lines(warning)
            for item in detail_lines:
                lines.append(f"  - {item}")
    else:
        lines.append("- 未记录阻断性或非阻断性数据质量提示。")
    lines.extend(["", "## 自动修复和处理决策"])
    if repairs:
        for repair in repairs:
            event_type = str(repair.get("event_type") or "")
            if event_type not in {"repair.completed", "legacy.repair"}:
                continue
            repair_audit = dict(repair.get("repair_audit") or repair)
            action = str(repair.get("action") or repair_audit.get("action") or "repair")
            reason = str(repair.get("repair_goal") or repair_audit.get("reason") or "根据运行时错误或前序风险执行修复。")
            step_index = repair.get("repair_step_index") or repair.get("step_index") or "-"
            lines.append(f"- 第 {step_index} 步：{display_title_for_action(action)}，决策依据：{reason}")
    elif repair_steps:
        for step in repair_steps:
            lines.append(f"- 第 {step.index} 步：{display_title_for_action(step.action)}，决策依据：{_repair_reason(step)}")
    else:
        lines.append("- 未记录自动修复步骤。")
    lines.extend(["", "## 关键决策表"])
    decision_table = _decision_table_lines(
        source_loads=source_loads,
        clarification_decisions=clarification_decisions,
        audit_decisions=audit_decisions,
        audit_repairs=audit_repairs,
        warnings=warnings,
        empty_results=[dict(item) for item in list(audit_payload.get("empty_results") or []) if isinstance(item, dict)],
        exports=[dict(item) for item in list(audit_payload.get("exports") or []) if isinstance(item, dict)],
    )
    if decision_table:
        lines.extend(decision_table)
    else:
        lines.extend(
            [
                "| 类型 | 决策 | 依据 | 影响 |",
                "| --- | --- | --- | --- |",
                "| - | 未记录关键决策 | - | - |",
            ]
        )
    lines.extend(["", "## 审计轨迹"])
    audit_lines = _audit_lines(
        audit_decisions=audit_decisions,
        clarification_decisions=clarification_decisions,
        source_loads=source_loads,
        audit_repairs=audit_repairs,
        warnings=warnings,
        empty_results=[dict(item) for item in list(audit_payload.get("empty_results") or []) if isinstance(item, dict)],
        exports=[dict(item) for item in list(audit_payload.get("exports") or []) if isinstance(item, dict)],
    )
    if audit_lines:
        lines.extend(audit_lines)
    else:
        lines.append("- 未记录用户确认、字段歧义、输出覆盖、CRS 推断或自动修复审计项。")
    lines.extend(["", "## 结果可信度"])
    lines.append(f"- 等级：{trust['level']}")
    for factor in trust["factors"]:
        lines.append(f"- 影响因素：{factor}")
    lines.extend(["", "## 结果质量检查"])
    quality_findings = [dict(item) for item in list(result.quality_findings or []) if isinstance(item, dict)]
    if quality_findings:
        for finding in quality_findings:
            blocking = "阻断" if finding.get("blocking") else "提示"
            lines.append(
                f"- [{blocking}] {finding.get('code') or 'quality_finding'}："
                f"{finding.get('message') or ''}"
            )
            affected = _affected_artifact_names(finding)
            if affected:
                lines.append(f"  - 影响对象：{', '.join(affected)}")
            for item in _quality_detail_lines(finding):
                lines.append(f"  - {item}")
    else:
        lines.append("- 未记录结果质量问题。")
    lines.extend(["", "## 运行环境"])
    lines.append(f"- QGIS version：{runtime.get('qgis_version') or '未记录'}")
    lines.append(f"- QGIS prefix path：{runtime.get('prefix_path') or '未记录'}")
    lines.append(f"- Runtime initialized：{runtime.get('initialized') if 'initialized' in runtime else '未记录'}")
    vector_formats = runtime.get("supported_vector_formats")
    if isinstance(vector_formats, dict) and vector_formats:
        lines.append(f"- Supported vector formats：{', '.join(sorted(vector_formats)[:8])}")
    else:
        lines.append("- Supported vector formats：未记录")
    lines.extend(["", "## 可复现步骤"])
    executed_tools = [dict(item) for item in list(audit_payload.get("executed_tools") or []) if isinstance(item, dict)]
    if executed_tools:
        for tool in executed_tools:
            action = str(tool.get("action") or "")
            step_index = tool.get("step_index") or "-"
            lines.append(f"- 第 {step_index} 步 `{display_title_for_action(action)}`")
            action_input = dict(tool.get("action_input") or {})
            if action_input:
                lines.append(f"  - 参数：`{action_input}`")
            algorithm_id = _tool_algorithm_id(tool)
            if algorithm_id:
                lines.append(f"  - QGIS 算法：`{algorithm_id}`")
            output_path = str(tool.get("output_path") or "")
            if output_path:
                lines.append(f"  - 输出：`{output_path}`")
            output_artifact = dict(tool.get("output_artifact") or {})
            if output_artifact:
                lines.append(
                    "  - 产物："
                    f"{output_artifact.get('name') or output_artifact.get('artifact_id') or 'artifact'}"
                    f" ({_artifact_role_label(str(output_artifact.get('role') or ''))})"
                )
            if str(tool.get("status") or "") == "failed":
                lines.append(f"  - 状态：failed")
    else:
        lines.append("- 无工具执行步骤。")
    lines.append("")
    return "\n".join(lines)


def _report_file_name(result: AgentResult, *, report_run_id: str = "") -> str:
    owner = report_run_id or result.session_id or "run"
    return f"{_timestamp()}_{safe_workspace_name(owner)}_{REPORT_ARTIFACT_NAME}.md"


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _report_run_id(result: AgentResult, *, runtime_events: list[dict[str, Any]] | None = None) -> str:
    for event in reversed(list(runtime_events or [])):
        if not isinstance(event, dict):
            continue
        run_id = str(event.get("run_id") or "").strip()
        if run_id:
            return run_id
    return str(getattr(result, "run_id", "") or "").strip()


def _report_audit_payload(
    result: AgentResult,
    *,
    artifact_outputs: list[dict[str, Any]] | None = None,
    runtime_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    existing = normalize_report_audit(getattr(result, "report_audit", None))
    if any(existing.get(key) for key in existing):
        return existing
    return build_report_audit_dict(
        result,
        runtime_events=runtime_events,
        artifact_outputs=artifact_outputs,
    )


def _goal_contract_lines(goal_contract: dict[str, Any]) -> list[str]:
    if not goal_contract:
        return ["- 必要输出：未记录", "- 质量检查：未记录"]
    required_outputs = [str(item) for item in list(goal_contract.get("required_outputs") or []) if str(item)]
    constraints = [str(item) for item in list(goal_contract.get("constraints") or []) if str(item)]
    quality_checks = [str(item) for item in list(goal_contract.get("quality_checks") or []) if str(item)]
    return [
        f"- 必要输出：{', '.join(required_outputs) if required_outputs else '未声明'}",
        f"- 约束：{', '.join(constraints) if constraints else '未声明'}",
        f"- 质量检查：{', '.join(quality_checks) if quality_checks else '未声明'}",
    ]


def _affected_artifact_names(finding: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in list(finding.get("affected_artifacts") or []):
        if not isinstance(item, dict):
            continue
        label = str(item.get("name") or item.get("artifact_id") or item.get("layer_id") or item.get("path") or "").strip()
        if label:
            names.append(label)
    return list(dict.fromkeys(names))


def _warning_detail_lines(warning: dict[str, Any]) -> list[str]:
    risk = dict(warning.get("risk") or {})
    diagnosis = dict(risk.get("diagnosis") or warning.get("diagnosis") or {})
    return _diagnosis_lines(diagnosis)


def _quality_detail_lines(finding: dict[str, Any]) -> list[str]:
    detail = dict(finding.get("detail") or {})
    diagnosis = dict(detail.get("diagnosis") or {})
    if not diagnosis:
        risk = dict(detail.get("risk") or {})
        diagnosis = dict(risk.get("diagnosis") or {})
    return _diagnosis_lines(diagnosis)


def _diagnosis_lines(diagnosis: dict[str, Any]) -> list[str]:
    if not diagnosis:
        return []
    lines: list[str] = []
    input_layers = [str(item) for item in list(diagnosis.get("input_layers") or []) if str(item or "").strip()]
    if input_layers:
        lines.append(f"相关图层：{', '.join(input_layers[:3])}")
    output_layer = str(diagnosis.get("output_layer") or "").strip()
    if output_layer:
        lines.append(f"输出图层：{output_layer}")
    predicate = str(diagnosis.get("predicate") or "").strip()
    if predicate:
        lines.append(f"空间关系：{predicate}")
    field = str(diagnosis.get("field") or "").strip()
    if field:
        lines.append(f"字段：{field}")
    operator = str(diagnosis.get("operator") or "").strip()
    if operator:
        lines.append(f"运算符：{operator}")
    if diagnosis.get("value") not in (None, ""):
        lines.append(f"值：{diagnosis.get('value')}")
    if diagnosis.get("max_distance") not in (None, ""):
        lines.append(f"最大距离：{diagnosis.get('max_distance')}")
    if diagnosis.get("discard_nonmatching") is True:
        lines.append("未匹配输入要素已被丢弃")
    extent_relation = str(diagnosis.get("extent_relation") or "").strip()
    if extent_relation:
        lines.append(f"范围关系：{extent_relation}")
    pixel_sizes = _pixel_size_summary(diagnosis)
    if pixel_sizes:
        lines.append(f"像元大小：{pixel_sizes}")
    if diagnosis.get("nodata") not in (None, ""):
        lines.append(f"NoData：{diagnosis.get('nodata')}")
    resampling = _recommended_resampling_text(diagnosis)
    if resampling:
        lines.append(f"建议重采样：{resampling}")
    causes = _string_list(diagnosis.get("possible_causes"))
    if causes:
        lines.append(f"可能原因：{'；'.join(causes[:2])}")
    actions = _empty_result_action_texts(diagnosis)
    if actions:
        lines.append(f"建议操作：{'；'.join(actions[:2])}")
    return lines


def _warnings(steps: list[ReActStep]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for step in steps:
        data = dict(step.observation.data or {})
        for key in ("preflight_warnings", "postflight_warnings"):
            items.extend(dict(item) for item in list(data.get(key) or []) if isinstance(item, dict))
    return items


def _audit_items(steps: list[ReActStep], key: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for step in steps:
        data = dict(step.observation.data or {})
        for item in list(data.get(key) or []):
            if isinstance(item, dict):
                payload = dict(item)
                payload.setdefault("step_index", step.index)
                payload.setdefault("step_action", step.action)
                items.append(payload)
    return items


def _decision_table_lines(
    *,
    source_loads: list[dict[str, Any]] | None = None,
    clarification_decisions: list[dict[str, Any]] | None = None,
    audit_decisions: list[dict[str, Any]],
    audit_repairs: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    empty_results: list[dict[str, Any]] | None = None,
    exports: list[dict[str, Any]] | None = None,
) -> list[str]:
    rows = ["| 类型 | 决策 | 依据 | 影响 |", "| --- | --- | --- | --- |"]
    for item in list(source_loads or []):
        alias = _source_load_label(item)
        slot_label = str(item.get("slot_label") or item.get("slot") or "数据源").strip()
        intent = display_title_for_action(str(item.get("active_intent") or "")) or "继续当前分析"
        rows.append(
            _decision_row(
                "补充数据",
                alias,
                str(item.get("message") or f"已补充 {slot_label}。"),
                f"用于{slot_label}，{intent}。",
            )
        )
    for item in list(clarification_decisions or []):
        detail = _selected_choice_labels(item) or _format_slot_patch(dict(item.get("slot_patch") or {})) or "已写入 slot patch"
        rows.append(
            _decision_row(
                "主动澄清",
                detail,
                str(item.get("question") or "运行中需要用户确认歧义。"),
                display_title_for_action(str(item.get("active_intent") or "")) or "继续当前分析意图",
            )
        )
    for item in audit_decisions:
        crs_recommendation = _audit_crs_recommendation(item)
        decision_text = _decision_label(str(item.get("decision") or "decision"))
        impact = str(item.get("risk_category") or item.get("risk_code") or "继续执行当前流程")
        selected_choices = _selected_choice_labels(item)
        slot_patch = _format_slot_patch(dict(item.get("slot_patch") or {}))
        if str(item.get("selected_crs") or "").strip():
            decision_text += f"（{item.get('selected_crs')}）"
        elif selected_choices:
            decision_text += f"（{selected_choices}）"
        if crs_recommendation:
            impact = _crs_impact_text(crs_recommendation, item)
        elif selected_choices or slot_patch:
            impact = selected_choices or slot_patch or impact
        rows.append(
            _decision_row(
                "用户确认",
                decision_text,
                str(item.get("message") or item.get("risk_code") or "风险确认"),
                impact,
            )
        )
    for item in audit_repairs:
        input_layer = dict(item.get("input") or {})
        output_layer = dict(item.get("output") or {})
        change = _feature_count_change_text(item)
        rows.append(
            _decision_row(
                "自动修复",
                str(item.get("action") or "repair"),
                str(item.get("reason") or "根据运行时错误或前序风险执行修复。"),
                (
                    f"{input_layer.get('name') or input_layer.get('ref') or '输入图层'} -> "
                    f"{output_layer.get('name') or output_layer.get('ref') or '输出图层'}"
                    f"{change}"
                ),
            )
        )
    for item in list(exports or []):
        artifact = dict(item.get("artifact") or {})
        source_artifact = dict(item.get("source_artifact") or {})
        decision = str(item.get("output_path") or item.get("output_name") or "未记录")
        evidence = str(
            artifact.get("name")
            or item.get("layer_name")
            or item.get("layer_ref")
            or "输出图层"
        )
        if source_artifact:
            evidence += f" <- {source_artifact.get('name') or source_artifact.get('artifact_id') or 'source'}"
        rows.append(
            _decision_row(
                "导出",
                decision,
                evidence,
                _export_impact_text(item),
            )
        )
    for warning in warnings:
        code = str(warning.get("code") or "")
        if code in {"distance_requires_projected_crs", "overlay_crs_mismatch", "unknown_crs"}:
            recommendation = _crs_recommendation_from_warning(warning)
            rows.append(
                _decision_row(
                    "CRS",
                    str(recommendation.get("target_crs") or code),
                    str(recommendation.get("reason") or warning.get("message") or code),
                    f"置信度 {recommendation.get('confidence') or 'unknown'}",
                )
            )
        if code in {"output_exists", "output_auto_renamed"}:
            rows.append(
                _decision_row(
                    "输出覆盖",
                    code,
                    str(warning.get("message") or code),
                    "影响导出路径或命名。",
                )
            )
    for item in list(empty_results or []):
        diagnosis = _empty_result_diagnosis(item)
        impact = "；".join(_empty_result_action_texts(diagnosis)[:2]) or "需要检查条件、空间关系或输入范围。"
        rows.append(
            _decision_row(
                "空结果",
                str(item.get("code") or "empty_result"),
                str(item.get("message") or "结果为空。"),
                impact,
            )
        )
    return _dedupe_lines(rows)


def _audit_lines(
    *,
    audit_decisions: list[dict[str, Any]],
    clarification_decisions: list[dict[str, Any]] | None = None,
    source_loads: list[dict[str, Any]] | None = None,
    audit_repairs: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    empty_results: list[dict[str, Any]] | None = None,
    exports: list[dict[str, Any]] | None = None,
) -> list[str]:
    lines: list[str] = []
    for item in list(source_loads or []):
        lines.append(f"- 数据补充：{str(item.get('message') or _source_load_fallback_text(item) or '已补充数据源。')}")
    for item in list(clarification_decisions or []):
        question = str(item.get("question") or "")
        active_intent = str(item.get("active_intent") or "")
        slot_patch = dict(item.get("slot_patch") or {})
        selected = _selected_choice_labels(item)
        detail = selected or _format_slot_patch(slot_patch)
        selected_crs = str(item.get("selected_crs") or "").strip()
        lines.append(
            f"- 主动澄清：{question or '未记录问题'}"
            f"{f'（{display_title_for_action(active_intent)}）' if active_intent else ''}"
            f"，用户选择：{detail or '未记录'}"
            f"{f'，最终 CRS：{selected_crs}' if selected_crs else ''}。"
        )
    for item in list(exports or []):
        artifact = dict(item.get("artifact") or {})
        source_artifact = dict(item.get("source_artifact") or {})
        layer_name = str(
            artifact.get("name")
            or item.get("layer_name")
            or item.get("layer_ref")
            or "输出图层"
        )
        output_path = str(item.get("output_path") or item.get("output_name") or "")
        count = item.get("feature_count")
        count_text = f"，要素数 {count}" if count is not None else ""
        source_text = ""
        if source_artifact:
            source_text = f"，来源 {source_artifact.get('name') or source_artifact.get('artifact_id') or 'source'}"
        lines.append(f"- 导出决策：{layer_name}{count_text}{source_text}，目标 `{output_path or '未记录'}`。")
    for item in audit_decisions:
        decision = str(item.get("decision") or "decision")
        risk_code = str(item.get("risk_code") or "")
        category = str(item.get("risk_category") or "")
        message = str(item.get("message") or "")
        crs_recommendation = _audit_crs_recommendation(item)
        selected_crs = str(item.get("selected_crs") or "").strip()
        selected_choices = _selected_choice_labels(item)
        slot_patch = _format_slot_patch(dict(item.get("slot_patch") or {}))
        lines.append(
            f"- 用户决策：{_decision_label(decision)}"
            f"{f'，风险 {risk_code}' if risk_code else ''}"
            f"{f'（{category}）' if category else ''}"
            f"{f'：{message}' if message else '。'}"
        )
        if selected_choices or slot_patch:
            lines.append(
                f"- 歧义消解："
                f"{f'选择 {selected_choices}' if selected_choices else ''}"
                f"{'；' if selected_choices and slot_patch else ''}"
                f"{f'写入 {slot_patch}' if slot_patch else ''}。"
            )
        if crs_recommendation:
            lines.append(
                f"- CRS 决策：推荐 {crs_recommendation.get('target_crs') or crs_recommendation.get('recommended_crs')}"
                f"{f'，最终采用 {selected_crs}' if selected_crs else ''}"
                f"，置信度 {crs_recommendation.get('confidence') or 'unknown'}，"
                f"依据：{crs_recommendation.get('reason') or '未记录'}。"
            )
    for item in audit_repairs:
        input_layer = dict(item.get("input") or {})
        output_layer = dict(item.get("output") or {})
        before = item.get("feature_count_before")
        after = item.get("feature_count_after")
        change = f"，要素数 {before} -> {after}" if before is not None and after is not None else ""
        lines.append(
            f"- 修复记录：{item.get('action') or 'repair'}，"
            f"{input_layer.get('name') or input_layer.get('ref') or '输入图层'} -> "
            f"{output_layer.get('name') or output_layer.get('ref') or '输出图层'}{change}。"
        )
    for warning in warnings:
        code = str(warning.get("code") or "")
        if code in {"output_exists", "output_auto_renamed", "unknown_field", "unknown_layer", "distance_requires_projected_crs", "overlay_crs_mismatch", "unknown_crs"}:
            lines.append(f"- 关键风险：{code}，{warning.get('message') or '已记录。'}")
            crs_recommendation = _crs_recommendation_from_warning(warning)
            if crs_recommendation:
                lines.append(
                    f"- CRS 推荐：{crs_recommendation.get('target_crs')}，"
                    f"置信度 {crs_recommendation.get('confidence') or 'unknown'}，"
                    f"依据：{crs_recommendation.get('reason') or '未记录'}"
                )
    for item in list(empty_results or []):
        diagnosis = _empty_result_diagnosis(item)
        causes = "；".join(_string_list(diagnosis.get("possible_causes"))[:2])
        actions = "；".join(_empty_result_action_texts(diagnosis)[:2])
        affected = ", ".join(_affected_artifact_names(item))
        suffix = ""
        if affected:
            suffix += f" 影响对象：{affected}。"
        if causes:
            suffix += f" 可能原因：{causes}。"
        if actions:
            suffix += f" 建议：{actions}。"
        lines.append(f"- 空结果诊断：{item.get('message') or item.get('code') or '结果为空，需要检查输入条件。'}{suffix}")
    return _dedupe_lines(lines)

def _decision_row(kind: str, decision: str, evidence: str, impact: str) -> str:
    return f"| {_table_text(kind)} | {_table_text(decision)} | {_table_text(evidence)} | {_table_text(impact)} |"


def _table_text(value: Any) -> str:
    text = str(value or "").replace("\n", " ").replace("|", "/").strip()
    return text or "-"


def _feature_count_change_text(item: dict[str, Any]) -> str:
    before = item.get("feature_count_before")
    after = item.get("feature_count_after")
    if before is None and after is None:
        return ""
    return f"（要素数 {before if before is not None else '?'} -> {after if after is not None else '?'}）"


def _export_impact_text(item: dict[str, Any]) -> str:
    count = item.get("feature_count")
    if count is None:
        return "生成显式导出文件。"
    return f"导出 {count} 个要素。"


def _selected_choice_labels(item: dict[str, Any]) -> str:
    labels: list[str] = []
    for choice in list(item.get("selected_choices") or []):
        if not isinstance(choice, dict):
            continue
        label = str(choice.get("label") or choice.get("value") or "").strip()
        if label:
            labels.append(label)
    return "，".join(labels)


def _format_slot_patch(slot_patch: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in slot_patch.items():
        if isinstance(value, list):
            text = ", ".join(str(item) for item in value)
        else:
            text = str(value)
        parts.append(f"{key}={text}")
    return "；".join(parts)


def _crs_recommendation_from_warning(warning: dict[str, Any]) -> dict[str, Any]:
    risk = dict(warning.get("risk") or {})
    diagnosis = dict(risk.get("diagnosis") or warning.get("diagnosis") or {})
    recommendation = diagnosis.get("crs_recommendation")
    return dict(recommendation) if isinstance(recommendation, dict) else {}


def _decision_label(value: str) -> str:
    labels = {
        "user_confirmed": "用户确认继续",
        "user_confirmed_repair": "用户确认修复",
        "user_supplied_resolution": "用户补充或选择参数",
        "auto_repair": "系统自动修复",
    }
    return labels.get(str(value or ""), str(value or "decision"))


def _audit_crs_recommendation(item: dict[str, Any]) -> dict[str, Any]:
    recommendation = item.get("crs_recommendation")
    return dict(recommendation) if isinstance(recommendation, dict) else {}


def _crs_impact_text(recommendation: dict[str, Any], item: dict[str, Any]) -> str:
    target = str(recommendation.get("target_crs") or recommendation.get("recommended_crs") or "").strip()
    selected = str(item.get("selected_crs") or "").strip()
    confidence = str(recommendation.get("confidence") or "unknown").strip()
    parts: list[str] = []
    if target:
        parts.append(f"推荐 {target}")
    if selected:
        parts.append(f"采用 {selected}")
    if confidence:
        parts.append(f"置信度 {confidence}")
    alternatives = [
        str(option.get("target_crs") or option.get("recommended_crs") or "").strip()
        for option in list(recommendation.get("alternatives") or [])
        if isinstance(option, dict)
    ]
    if any(alternatives):
        parts.append("备选 " + ", ".join(item for item in alternatives if item))
    return "；".join(parts) or str(item.get("risk_category") or item.get("risk_code") or "继续执行当前流程")


def _empty_result_diagnosis(item: dict[str, Any]) -> dict[str, Any]:
    risk = dict(item.get("risk") or {})
    return dict(risk.get("diagnosis") or item.get("diagnosis") or {})


def _empty_result_action_texts(diagnosis: dict[str, Any]) -> list[str]:
    options = [
        str(action.get("label") or "").strip()
        for action in list(diagnosis.get("suggested_action_options") or [])
        if isinstance(action, dict)
    ]
    if any(options):
        return [item for item in options if item]
    return _string_list(diagnosis.get("suggested_actions")) or _string_list(diagnosis.get("suggested_next_actions"))


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in list(value or []) if str(item or "").strip()] if isinstance(value, list) else []


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


def _artifact_outputs(artifacts: Any) -> list[dict[str, Any]]:
    if artifacts is None or not hasattr(artifacts, "outputs"):
        return []
    try:
        return [
            dict(item)
            for item in list(artifacts.outputs() or [])
            if isinstance(item, dict) and str(item.get("role") or "") != "report"
        ]
    except Exception:
        return []


def _artifact_catalog_lines(outputs: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        role = _artifact_role_label(str(item.get("role") or ""))
        name = str(item.get("name") or item.get("layer_id") or item.get("artifact_id") or "artifact")
        path = str(item.get("path") or "")
        lines.append(f"- [{role}] {name}: {path or '未记录路径'}")
        summary = str(item.get("display_summary") or "").strip()
        if summary:
            lines.append(f"  - 摘要：{summary}")
        detail_parts: list[str] = []
        source_step = item.get("source_step")
        if source_step is not None:
            detail_parts.append(f"source_step={source_step}")
        algorithm_id = str(item.get("algorithm_id") or "").strip()
        if algorithm_id:
            detail_parts.append(f"algorithm={algorithm_id}")
        crs = str(item.get("crs") or "").strip()
        if crs:
            detail_parts.append(f"CRS={crs}")
        geometry_type = str(item.get("geometry_type") or "").strip()
        if geometry_type:
            detail_parts.append(f"geometry={geometry_type}")
        feature_count = item.get("feature_count")
        if feature_count is not None:
            detail_parts.append(f"features={feature_count}")
        if "materialized" in item:
            detail_parts.append(f"materialized={'yes' if item.get('materialized') else 'no'}")
        if "reusable" in item:
            detail_parts.append(f"reusable={'yes' if item.get('reusable') else 'no'}")
        if detail_parts:
            lines.append(f"  - {'; '.join(detail_parts)}")
        input_artifacts = [dict(parent) for parent in list(item.get("input_artifacts") or []) if isinstance(parent, dict)]
        if input_artifacts:
            input_labels = [
                str(parent.get("name") or parent.get("layer_id") or parent.get("artifact_id") or "").strip()
                for parent in input_artifacts
                if str(parent.get("name") or parent.get("layer_id") or parent.get("artifact_id") or "").strip()
            ]
            if input_labels:
                lines.append(f"  - inputs: {', '.join(input_labels)}")
        else:
            parent_ids = [str(parent) for parent in list(item.get("parent_ids") or []) if str(parent)]
            if parent_ids:
                lines.append(f"  - inputs: {', '.join(parent_ids)}")
    return lines


def _artifact_role_label(role: str) -> str:
    return {
        "input": "输入",
        "intermediate": "中间",
        "final": "最终",
        "report": "报告",
    }.get(str(role or "").lower(), "产物")


def _is_final_artifact(item: dict[str, Any]) -> bool:
    role = str(item.get("role") or "").lower()
    algorithm_id = str(item.get("algorithm_id") or "").lower()
    return role == "final" or algorithm_id == "export_result"


def _runtime_report(toolbox: Any) -> dict[str, Any]:
    runtime = getattr(toolbox, "runtime", None)
    if runtime is None or not hasattr(runtime, "environment_report"):
        return {}
    try:
        report = runtime.environment_report()
    except Exception:
        return {}
    return dict(report or {}) if isinstance(report, dict) else {}


def _warning_codes(result_steps: list[ReActStep]) -> list[str]:
    return [str(item.get("code") or item.get("risk", {}).get("code") or "") for item in _warnings(result_steps)]


def _repair_steps(steps: list[ReActStep]) -> list[ReActStep]:
    repair_actions = {"fix_geometries", "reproject_layer"}
    return [
        step for step in steps
        if step.action in repair_actions or str(step.thought or "").lower().find("repair") >= 0
    ]


def _repair_reason(step: ReActStep) -> str:
    data = dict(step.observation.data or {})
    warnings: list[dict[str, Any]] = []
    for key in ("preflight_warnings", "postflight_warnings"):
        warnings.extend(dict(item) for item in list(data.get(key) or []) if isinstance(item, dict))
    if warnings:
        return str(warnings[0].get("message") or warnings[0].get("code") or "记录到 GIS 风险后执行修复。")
    if step.action == "fix_geometries":
        return "检测到无效几何可能导致后续空间分析失败，先修复几何再继续。"
    if step.action == "reproject_layer":
        return "检测到距离、叠加或坐标系条件需要统一 CRS，先重投影再继续。"
    return "根据运行时错误或前序风险执行修复。"


def _input_layers(state_tree: dict[str, Any]) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    for layer in list(state_tree.get("layers") or []):
        if not isinstance(layer, dict):
            continue
        if str(layer.get("algorithm_id") or "").strip() or list(layer.get("parent_ids") or []):
            continue
        metadata = dict(layer.get("metadata") or {})
        fields = list(metadata.get("fields") or [])
        inputs.append(
            {
                "name": str(layer.get("name") or layer.get("layer_id") or "input"),
                "source": str(layer.get("source") or "未记录"),
                "kind": str(layer.get("kind") or "unknown"),
                "crs": str(metadata.get("crs") or "unknown"),
                "geometry_type": str(metadata.get("geometry_type") or "-"),
                "feature_count": metadata.get("feature_count", metadata.get("row_count", "unknown")),
                "field_count": len(fields) if fields else metadata.get("field_count", "unknown"),
            }
        )
    return inputs


def _input_artifacts(outputs: list[dict[str, Any]], *, source_loads: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    inputs: list[dict[str, Any]] = []
    derived_inputs: list[dict[str, Any]] = []
    for item in outputs:
        if not isinstance(item, dict):
            continue
        if str(item.get("role") or "").strip().lower() != "input":
            derived_inputs.extend(_derived_input_artifact_rows(item))
            continue
        inputs.append(_input_row_from_artifact(item))
    if inputs:
        return _dedupe_input_rows(inputs)
    if derived_inputs:
        return _dedupe_input_rows(derived_inputs)
    for item in list(source_loads or []):
        if not isinstance(item, dict):
            continue
        source = dict(item.get("source") or {})
        path = str(item.get("path") or source.get("path") or "").strip()
        if not path:
            continue
        inputs.append(
            {
                "name": _source_load_label(item),
                "source": path,
                "kind": str(source.get("type") or "unknown"),
                "crs": "unknown",
                "geometry_type": "-",
                "feature_count": "unknown",
                "field_count": "unknown",
            }
        )
    return _dedupe_input_rows(inputs)


def _derived_input_artifact_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for parent in list(item.get("input_artifacts") or []):
        if not isinstance(parent, dict):
            continue
        if str(parent.get("role") or "").strip().lower() not in {"", "input"}:
            continue
        rows.append(_input_row_from_artifact(parent))
    return rows


def _input_row_from_artifact(item: dict[str, Any]) -> dict[str, Any]:
    fields = list(item.get("fields") or [])
    return {
        "name": str(item.get("name") or item.get("layer_id") or item.get("artifact_id") or "input"),
        "source": str(item.get("path") or "未记录"),
        "kind": str(item.get("kind") or "unknown"),
        "crs": str(item.get("crs") or "unknown"),
        "geometry_type": str(item.get("geometry_type") or "-"),
        "feature_count": item.get("feature_count", item.get("row_count", "unknown")),
        "field_count": len(fields) if fields else "unknown",
    }


def _trust_assessment(
    *,
    warnings: list[dict[str, Any]],
    repairs: list[Any],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    factors: list[str] = []
    warning_codes = {str(item.get("code") or item.get("risk", {}).get("code") or "") for item in warnings}
    if not outputs:
        factors.append("未记录最终输出文件。")
    if repairs:
        factors.append("执行过程中发生自动修复或确认后修复，需要关注修复对结果的影响。")
    if warning_codes:
        factors.append("存在数据质量或 GIS 风险提示：" + ", ".join(sorted(code for code in warning_codes if code)))
    if any("empty" in code for code in warning_codes):
        level = "低"
    elif warning_codes or repairs or not outputs:
        level = "中"
    else:
        level = "高"
        factors.append("未记录非阻断 warning、自动修复或空结果风险。")
    return {"level": level, "factors": factors}


def _dedupe_lines(lines: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        result.append(line)
    return result


def _dedupe_input_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("name") or ""), str(item.get("source") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _tool_algorithm_id(tool: dict[str, Any]) -> str:
    observation = dict(tool.get("observation") or {})
    data = dict(observation.get("data") or {})
    layer = dict(data.get("layer") or {})
    algorithm_id = str(layer.get("algorithm_id") or "").strip()
    if algorithm_id:
        return algorithm_id
    return algorithm_id_for_action(str(tool.get("action") or ""))


def _source_load_label(item: dict[str, Any]) -> str:
    source = dict(item.get("source") or {})
    alias = str(item.get("alias") or source.get("alias") or "").strip()
    if alias:
        return alias
    path = str(item.get("path") or source.get("path") or "").strip()
    return Path(path).stem if path else "数据源"


def _source_load_fallback_text(item: dict[str, Any]) -> str:
    label = _source_load_label(item)
    slot_label = str(item.get("slot_label") or item.get("slot") or "数据源").strip()
    intent = display_title_for_action(str(item.get("active_intent") or "")) or "继续当前任务"
    phase = str(item.get("phase") or "").strip()
    if phase == "resume":
        return f"已补充 {label}，用于{slot_label}，继续执行{intent}。"
    if phase == "continue_session":
        return f"已加载新增数据 {label}，继续当前会话。"
    return f"已加载数据 {label}。"
