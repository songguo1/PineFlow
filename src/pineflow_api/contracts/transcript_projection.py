"""User-facing transcript projection built from saved session state."""

from __future__ import annotations

from typing import Any
from pathlib import Path

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.tools.contracts.tool_definitions import display_title_for_action, parameter_labels_for_action


def build_transcript_projection(
    *,
    messages: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable UI transcript so the frontend need not infer semantics.

    Priority is fixed:
    1. persisted transcript timeline in run/session result
    2. append-only run events with transcript items or event projection
    3. (deprecated) legacy rebuild from react_trace for pre-consolidation sessions only
    """
    result_payload = dict(result or {})
    message_timeline = _message_items(messages)
    existing = _existing_timeline(result_payload)
    if existing:
        return make_json_safe({"timeline": _dedupe_timeline(existing), "version": 2})

    event_timeline = _timeline_from_events(events)
    if event_timeline:
        timeline = message_timeline + _drop_duplicate_assistant_answers(event_timeline, message_timeline)
        if not any(item.get("type") == "assistant_answer" for item in timeline):
            answer = _assistant_text(result_payload)
            if answer:
                timeline.append({"type": "assistant_answer", "text": answer})
        return make_json_safe({"timeline": _dedupe_timeline(timeline), "version": 2})

    # Deprecated legacy fallback: rebuild timeline from react_trace for sessions
    # created before the transcript consolidation. New sessions always reach one
    # of the two return statements above. Remove after a 2-release window.
    timeline: list[dict[str, Any]] = list(message_timeline)
    timeline.extend(_cards_from_result(result_payload))
    timeline.extend(_workflow_steps(result_payload))
    artifact_summary = _artifact_summary(result_payload)
    if artifact_summary:
        timeline.append(artifact_summary)
    if not any(item.get("type") == "assistant_answer" for item in timeline):
        answer = _assistant_text(result_payload)
        if answer:
            timeline.append({"type": "assistant_answer", "text": answer})
    return make_json_safe({"timeline": _dedupe_timeline(timeline), "version": 2})


def append_transcript_item(transcript: dict[str, Any] | None, item: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(transcript or {})
    timeline = [dict(entry) for entry in list(payload.get("timeline") or []) if isinstance(entry, dict)]
    next_item = ensure_transcript_item_identity(dict(item or {}))
    if not next_item or not next_item.get("type"):
        return make_json_safe({"timeline": _dedupe_timeline(timeline), "version": max(int(payload.get("version") or 0), 2)})
    timeline.append(next_item)
    return make_json_safe({"timeline": _dedupe_timeline(timeline), "version": max(int(payload.get("version") or 0), 2)})


def append_report_artifact_summary(
    transcript: dict[str, Any] | None,
    outputs: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    reports = [
        dict(item)
        for item in list(outputs or [])
        if isinstance(item, dict) and str(item.get("role") or item.get("kind") or "").strip() == "report"
    ]
    if not reports:
        payload = dict(transcript or {})
        return make_json_safe(
            {
                "timeline": _dedupe_timeline([dict(entry) for entry in list(payload.get("timeline") or []) if isinstance(entry, dict)]),
                "version": max(int(payload.get("version") or 0), 2),
            }
        )
    item = {
        "type": "artifact_summary",
        "display_title": "分析报告",
        "display_summary": _report_artifact_text(reports),
        "text": _report_artifact_text(reports),
        "artifacts": reports,
        "event_key": _report_artifact_event_key(reports),
    }
    return append_transcript_item(transcript, item)


def merge_transcript_projection(
    current: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    current_payload = dict(current or {})
    incoming_payload = dict(incoming or {})
    timeline = [dict(entry) for entry in list(current_payload.get("timeline") or []) if isinstance(entry, dict)]
    timeline.extend(dict(entry) for entry in list(incoming_payload.get("timeline") or []) if isinstance(entry, dict))
    return make_json_safe(
        {
            "timeline": _dedupe_timeline(timeline),
            "version": max(int(current_payload.get("version") or 0), int(incoming_payload.get("version") or 0), 2),
        }
    )


def ensure_transcript_item_identity(item: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(item or {})
    key = _transcript_item_event_key(payload)
    if key:
        payload.setdefault("event_key", key)
        payload.setdefault("id", key)
    return payload


def transcript_item_from_event(event: dict[str, Any], *, step_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Project one run event into a transcript delta item."""
    payload = dict(event or {})
    fact = _event_fact(payload)
    event_name = _canonical_event_name(
        str(payload.get("event") or "").strip(),
        str(payload.get("event_type") or "").strip(),
    )
    if event_name == "tool_started":
        tool = str(fact.get("action") or payload.get("action") or "")
        parameters = dict(fact.get("action_input") or payload.get("action_input") or {})
        return _workflow_step_item(
            index=int(fact.get("step_index") or payload.get("step_index") or 0),
            tool=tool,
            parameters=parameters,
            observation={"status": "running", "message": str(payload.get("display_summary") or payload.get("message") or "")},
            warnings=[],
            outputs=[],
            display_title=str(payload.get("display_title") or ""),
            display_summary=str(payload.get("display_summary") or payload.get("message") or ""),
            event_type=str(payload.get("event_type") or "tool.started"),
            event_meta=_event_item_meta(payload),
        )
    if event_name == "observation":
        observation = dict(fact.get("observation") or payload.get("observation") or {})
        context = dict(step_context or {})
        tool = str(fact.get("action") or context.get("action") or payload.get("action") or "")
        parameters = dict(fact.get("action_input") or context.get("action_input") or {})
        output_artifact = _event_output_artifact(fact, observation)
        return _workflow_step_item(
            index=int(fact.get("step_index") or payload.get("step_index") or 0),
            tool=tool,
            parameters=parameters,
            observation=observation,
            warnings=[],
            outputs=[],
            output_artifact=output_artifact,
            display_title=str(payload.get("display_title") or ""),
            display_summary=str(payload.get("display_summary") or ""),
            event_type=str(payload.get("event_type") or ""),
            event_meta=_event_item_meta(payload),
        )
    if event_name == "artifact":
        artifact = dict(fact.get("artifact") or payload.get("artifact") or {})
        if not artifact:
            return {}
        artifact_summary = str(
            payload.get("display_summary")
            or artifact.get("display_summary")
            or payload.get("message")
            or _artifact_card_text([artifact])
        )
        item = {
            "type": "artifact_summary",
            "display_title": str(payload.get("display_title") or artifact.get("display_title") or "输出结果"),
            "display_summary": artifact_summary,
            "text": artifact_summary,
            "artifacts": [artifact],
        }
        item.update(_event_item_meta(payload))
        return ensure_transcript_item_identity(item)
    if event_name == "warning":
        warning = dict(fact.get("warning") or payload.get("warning") or {})
        risk = dict(fact.get("risk") or payload.get("risk") or warning.get("risk") or {})
        item = {
            "type": "warning_card",
            "display_title": _warning_card_title(risk=risk, warning=warning),
            "display_summary": str(risk.get("message") or payload.get("message") or ""),
            "text": str(risk.get("message") or payload.get("message") or ""),
            "risk": risk,
            "warning": warning,
        }
        item.update(_event_item_meta(payload))
        return ensure_transcript_item_identity(item)
    if event_name == "empty_result":
        warning = dict(fact.get("warning") or payload.get("warning") or {})
        risk = dict(fact.get("risk") or payload.get("risk") or warning.get("risk") or {})
        item = {
            "type": "warning_card",
            "display_title": _warning_card_title(risk=risk, warning=warning),
            "display_summary": str(payload.get("message") or risk.get("message") or "结果为空，需要检查输入条件。"),
            "text": str(payload.get("message") or risk.get("message") or "结果为空，需要检查输入条件。"),
            "risk": risk,
            "warning": warning,
        }
        item.update(_event_item_meta(payload))
        return ensure_transcript_item_identity(item)
    if event_name in {"before_export", "repair_success"}:
        text = str(payload.get("message") or "").strip()
        if not text:
            return {}
        item = {"type": "assistant_answer", "text": text}
        item.update(_event_item_meta(payload))
        return ensure_transcript_item_identity(item)
    if event_name == "question":
        result = dict(payload.get("result") or {})
        pending_task = dict(payload.get("pending_task") or result.get("pending_task") or {})
        risk = dict(payload.get("risk") or pending_task.get("risk") or {})
        item = {
            "type": "question_card",
            "display_title": "需要补充输入",
            "display_summary": str(payload.get("ux_explanation") or pending_task.get("ux_explanation") or risk.get("message") or payload.get("message") or result.get("next_question") or ""),
            "text": str(payload.get("ux_explanation") or pending_task.get("ux_explanation") or risk.get("message") or payload.get("message") or result.get("next_question") or ""),
            "risk": risk,
            "missing_slots": list(pending_task.get("missing_slots") or []),
            "choices": list(pending_task.get("choices") or []),
            "actions": list(pending_task.get("allowed_actions") or []),
        }
        item.update(_event_item_meta(payload))
        return ensure_transcript_item_identity(item)
    if event_name in {"confirmation", "repair"}:
        result = dict(payload.get("result") or {})
        pending_task = dict(payload.get("pending_task") or result.get("pending_task") or {})
        risk = dict(payload.get("risk") or pending_task.get("risk") or {})
        repair = dict(payload.get("repair") or result.get("repair") or {})
        if not (pending_task or risk or repair):
            return {}
        item = {
            "type": "confirmation_card",
            "display_title": "需要确认",
            "display_summary": str(payload.get("ux_explanation") or pending_task.get("ux_explanation") or risk.get("message") or payload.get("message") or repair.get("message") or ""),
            "text": str(payload.get("ux_explanation") or pending_task.get("ux_explanation") or risk.get("message") or payload.get("message") or repair.get("message") or ""),
            "risk": risk,
            "repair": repair,
            "actions": list(pending_task.get("allowed_actions") or []),
        }
        item.update(_event_item_meta(payload))
        return ensure_transcript_item_identity(item)
    if event_name == "resume":
        clarification = dict(fact.get("clarification_decision") or {})
        if clarification:
            text = _clarification_answer_text(clarification)
            if not text:
                return {}
            item = {"type": "assistant_answer", "text": text}
            item.update(_event_item_meta(payload))
            return ensure_transcript_item_identity(item)
    if event_name in {"completed", "failed", "cancelled"}:
        result = dict(payload.get("result") or {})
        text = _assistant_text(result) or str(payload.get("message") or "").strip()
        if not text:
            return {}
        item = {"type": "assistant_answer", "text": text}
        item.update(_event_item_meta(payload))
        return ensure_transcript_item_identity(item)
    return {}


def _event_item_meta(event: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(event or {})
    meta: dict[str, Any] = {}
    for key in ("session_id", "run_id", "created_at"):
        value = str(payload.get(key) or "").strip()
        if value:
            meta[key] = value
    seq = int(payload.get("seq") or 0)
    if seq > 0:
        meta["seq"] = seq
    return meta


def _canonical_event_name(event_name: str, event_type: str) -> str:
    if event_name in {
        "observation",
        "warning",
        "empty_result",
        "before_export",
        "repair_success",
        "review",
        "question",
        "confirmation",
        "repair",
        "completed",
        "failed",
        "cancelled",
    }:
        return event_name
    return {
        "tool.started": "tool_started",
        "tool.completed": "observation",
        "tool.failed": "observation",
        "artifact.created": "artifact",
        "risk.warning": "warning",
        "warning.emitted": "warning",
        "result.empty": "empty_result",
        "export.before": "before_export",
        "repair.completed": "repair_success",
        "repair.failed": "failed",
        "user_input.requested": "question",
        "repair.confirmation_requested": "confirmation",
        "run.completed": "completed",
        "run.failed": "failed",
        "run.cancelled": "cancelled",
    }.get(event_type, event_name)


def _cards_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    status = str(result.get("status") or "").strip()
    pending_task = dict(result.get("pending_task") or {})
    risk = dict(pending_task.get("risk") or {})
    repair = dict(result.get("repair") or {})
    if status == "awaiting_confirmation" and pending_task:
        cards.append(
            {
                "type": "confirmation_card",
                "display_title": "需要确认",
                "display_summary": str(pending_task.get("ux_explanation") or risk.get("message") or repair.get("message") or pending_task.get("last_question") or ""),
                "text": str(pending_task.get("ux_explanation") or risk.get("message") or repair.get("message") or pending_task.get("last_question") or ""),
                "risk": risk,
                "repair": repair,
                "actions": list(pending_task.get("allowed_actions") or []),
            }
        )
    elif status == "awaiting_user" and pending_task:
        cards.append(
            {
                "type": "question_card",
                "display_title": "需要补充输入",
                "display_summary": str(pending_task.get("ux_explanation") or risk.get("message") or pending_task.get("last_question") or result.get("next_question") or ""),
                "text": str(pending_task.get("ux_explanation") or risk.get("message") or pending_task.get("last_question") or result.get("next_question") or ""),
                "risk": risk,
                "missing_slots": list(pending_task.get("missing_slots") or []),
                "choices": list(pending_task.get("choices") or []),
                "actions": list(pending_task.get("allowed_actions") or []),
            }
        )

    for risk_item in list(result.get("risks") or []):
        if not isinstance(risk_item, dict):
            continue
        if risk_item.get("blocking") or risk_item.get("confirmation_required"):
            continue
        cards.append(
            {
                "type": "warning_card",
                "display_title": _warning_card_title(risk=dict(risk_item)),
                "display_summary": str(risk_item.get("message") or risk_item.get("code") or ""),
                "text": str(risk_item.get("message") or risk_item.get("code") or ""),
                "risk": dict(risk_item),
            }
        )
    for finding in list(result.get("quality_findings") or []):
        if not isinstance(finding, dict):
            continue
        cards.append(
            {
                "type": "warning_card",
                "display_title": "质量检查",
                "display_summary": str(finding.get("message") or finding.get("code") or "结果质量提示"),
                "text": str(finding.get("message") or finding.get("code") or "结果质量提示"),
                "quality_finding": dict(finding),
            }
        )
    return cards


def _existing_timeline(result: dict[str, Any]) -> list[dict[str, Any]]:
    transcript = result.get("transcript")
    if not isinstance(transcript, dict):
        return []
    timeline = transcript.get("timeline")
    if not isinstance(timeline, list):
        return []
    return [ensure_transcript_item_identity(dict(item)) for item in timeline if isinstance(item, dict) and item.get("type")]


def _message_items(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for index, message in enumerate(list(messages or []), start=1):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            timeline.append({"type": "user_message", "text": content, "event_key": f"message:{index}:user", "id": f"message:{index}:user"})
        elif role == "assistant":
            timeline.append({"type": "assistant_answer", "text": content, "event_key": f"message:{index}:assistant", "id": f"message:{index}:assistant"})
    return timeline


def _timeline_from_events(events: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    step_contexts: dict[int, dict[str, Any]] = {}
    for event in list(events or []):
        if not isinstance(event, dict):
            continue
        step_index = int(event.get("step_index") or 0)
        event_name = str(event.get("event") or "")
        if step_index > 0 and event_name in {"action", "command"} and event.get("action"):
            step_contexts[step_index] = {
                "action": str(event.get("action") or ""),
                "action_input": dict(event.get("action_input") or {}),
            }
        item = dict(event.get("transcript_item") or {})
        if not item:
            item = transcript_item_from_event(event, step_context=step_contexts.get(step_index) or {})
        if item:
            timeline.append(item)
    return timeline


def _drop_duplicate_assistant_answers(
    timeline: list[dict[str, Any]],
    message_timeline: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    message_answers = {
        _normalized_text(item.get("text"))
        for item in message_timeline
        if item.get("type") == "assistant_answer" and _normalized_text(item.get("text"))
    }
    if not message_answers:
        return timeline
    filtered: list[dict[str, Any]] = []
    for item in timeline:
        if item.get("type") == "assistant_answer" and _normalized_text(item.get("text")) in message_answers:
            continue
        filtered.append(item)
    return filtered


def _normalized_text(value: Any) -> str:
    return "\n".join(str(value or "").strip().split())


def _event_fact(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        merged = dict(event)
        merged.update(payload)
        return merged
    return dict(event)


def _workflow_steps(result: dict[str, Any]) -> list[dict[str, Any]]:
    # Legacy rebuild path only. New runs should project workflow from transcript/events first.
    steps: list[dict[str, Any]] = []
    outputs = [dict(item) for item in list(result.get("outputs") or []) if isinstance(item, dict)]
    for item in list(result.get("react_trace") or []):
        if not isinstance(item, dict):
            continue
        tool = str(item.get("action") or "")
        observation = dict(item.get("observation") or {})
        data = dict(observation.get("data") or {})
        warnings = [dict(warning) for warning in list(data.get("postflight_warnings") or []) if isinstance(warning, dict)]
        steps.append(
            _workflow_step_item(
                index=int(item.get("index") or len(steps) + 1),
                tool=tool,
                parameters=dict(item.get("action_input") or {}),
                observation=observation,
                warnings=warnings,
                outputs=outputs,
                display_title="",
                display_summary="",
            )
        )
    return steps


def _workflow_step_item(
    *,
    index: int,
    tool: str,
    parameters: dict[str, Any],
    observation: dict[str, Any],
    warnings: list[dict[str, Any]],
    outputs: list[dict[str, Any]],
    output_artifact: dict[str, Any] | None = None,
    display_title: str = "",
    display_summary: str = "",
    event_type: str = "",
    event_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_path = str(observation.get("output_path") or "")
    status = str(observation.get("status") or "")
    summary = str(observation.get("message") or "")
    data = dict(observation.get("data") or {})
    ux_summary = str(data.get("ux_summary") or "")
    resolved_title = display_title or _display_title(tool)
    resolved_summary = display_summary or ux_summary or _display_summary(tool=tool, status=status, summary=summary, output_path=output_path)
    progress_summary = _progress_summary(
        tool=tool,
        title=resolved_title,
        status=status,
        output_path=output_path,
        data=data,
    )
    artifact = dict(output_artifact or {})
    item = {
        "type": "workflow_step",
        "event_type": event_type or "tool.completed",
        "index": index,
        "tool": tool,
        "display_title": resolved_title,
        "display_summary": resolved_summary,
        "parameters": dict(parameters or {}),
        "parameter_labels": parameter_labels_for_action(tool),
        "status": status,
        "summary": summary,
        "output_path": output_path,
        "data": data,
        "progress_summary": progress_summary,
        "artifact_refs": _artifact_refs(output_path, outputs, output_artifact=artifact),
        "warnings": warnings,
    }
    if artifact:
        item["output_artifact"] = artifact
    item.update(_event_item_meta(event_meta or {}))
    return ensure_transcript_item_identity(item)


def _artifact_summary(result: dict[str, Any]) -> dict[str, Any]:
    outputs = [dict(item) for item in list(result.get("outputs") or []) if isinstance(item, dict)]
    if not outputs:
        return {}
    return {
        "type": "artifact_summary",
        "display_title": "输出结果",
        "display_summary": _artifact_card_text(outputs),
        "text": _artifact_card_text(outputs),
        "artifacts": outputs,
    }


def _report_artifact_text(reports: list[dict[str, Any]]) -> str:
    if not reports:
        return ""
    names = [
        str(item.get("name") or item.get("artifact_id") or Path(str(item.get("path") or item.get("source") or "")).name).strip()
        for item in reports
        if isinstance(item, dict)
    ]
    names = [item for item in names if item]
    if len(names) == 1:
        return f"已生成分析报告 {names[0]}。"
    return f"已生成 {len(names)} 份分析报告：{', '.join(names[:3])}。"


def _report_artifact_event_key(reports: list[dict[str, Any]]) -> str:
    stable = [
        str(item.get("artifact_id") or item.get("path") or item.get("source") or item.get("name") or "").strip()
        for item in reports
        if isinstance(item, dict)
    ]
    stable = [item for item in stable if item]
    return f"artifact:report:{'|'.join(stable) or 'analysis_report'}"


def _assistant_text(result: dict[str, Any]) -> str:
    status = str(result.get("status") or "").strip().lower()
    if status in {"awaiting_user", "awaiting_confirmation"}:
        return ""
    final_message = str(result.get("final_message") or "").strip()
    if status == "paused" and final_message.lower() == "paused by user request.":
        return ""
    if final_message:
        return final_message
    next_question = str(result.get("next_question") or "").strip()
    if next_question:
        return next_question
    for error in list(result.get("errors") or []):
        text = str(error or "").strip()
        if text:
            return text
    return ""


def _display_title(tool: str) -> str:
    return display_title_for_action(tool)


def _display_summary(*, tool: str, status: str, summary: str, output_path: str) -> str:
    if str(status or "").lower() not in {"success", "completed"}:
        return summary
    title = _display_title(tool)
    if output_path:
        return f"{title}已完成，输出文件：{Path(output_path).name}"
    if summary:
        return _productize_summary(summary, title)
    return f"{title}已完成。"


def _productize_summary(summary: str, title: str) -> str:
    text = str(summary or "").strip()
    if text.lower().startswith("executed "):
        return f"{title}已完成。"
    if text.lower().startswith("exported "):
        return text
    return text


def _progress_summary(
    *,
    tool: str,
    title: str,
    status: str,
    output_path: str,
    data: dict[str, Any],
) -> dict[str, str]:
    normalized = str(status or "").strip().lower()
    action_title = _progress_title(tool=tool, title=title)
    if normalized == "running":
        return {"doing": f"正在处理{action_title}。"}
    if normalized in {"success", "completed", "done"}:
        done = _done_progress_text(
            tool=tool,
            title=action_title,
            output_path=output_path,
            data=data,
        )
        return {"done": done} if done else {}
    if normalized in {"error", "failed"}:
        return {"done": f"{action_title}没有完成。"}
    return {}


def _progress_title(*, tool: str, title: str) -> str:
    text = str(title or "").strip() or _display_title(tool)
    return {
        "CSV 转点图层": "CSV 转点",
        "缓冲区分析": "缓冲区分析",
        "按位置筛选": "按位置筛选",
        "导出结果": "导出结果",
        "修复几何": "几何修复",
    }.get(text, text)


def _done_progress_text(
    *,
    tool: str,
    title: str,
    output_path: str,
    data: dict[str, Any],
) -> str:
    if tool == "export_result" and output_path:
        return f"结果已导出为 {Path(output_path).name}。"
    layer = dict(data.get("layer") or {})
    metadata = dict(layer.get("metadata") or {})
    count = metadata.get("feature_count")
    unit = "个要素"
    if count is None:
        count = metadata.get("row_count")
        unit = "行"
    if count is not None:
        return f"已完成{title}，得到 {count} {unit}。"
    return f"已完成{title}。"


def _artifact_refs(output_path: str, outputs: list[dict[str, Any]], *, output_artifact: dict[str, Any] | None = None) -> list[str]:
    artifact = dict(output_artifact or {})
    direct_ref = str(artifact.get("artifact_id") or artifact.get("name") or "").strip()
    if direct_ref:
        return [direct_ref]
    if not output_path:
        return []
    refs: list[str] = []
    normalized = str(Path(output_path).name).lower()
    for artifact in outputs:
        artifact_path = str(artifact.get("path") or artifact.get("output_path") or artifact.get("source") or "")
        if not artifact_path:
            continue
        if str(Path(artifact_path).name).lower() == normalized:
            ref = str(artifact.get("artifact_id") or artifact.get("name") or artifact_path)
            if ref:
                refs.append(ref)
    return list(dict.fromkeys(refs))


def _event_output_artifact(fact: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
    artifact = fact.get("output_artifact")
    if isinstance(artifact, dict) and artifact:
        return dict(artifact)
    data = observation.get("data")
    if isinstance(data, dict):
        nested = data.get("output_artifact") or data.get("artifact")
        if isinstance(nested, dict) and nested:
            return dict(nested)
    return {}


def _artifact_card_text(artifacts: list[dict[str, Any]]) -> str:
    if not artifacts:
        return "已记录输出产物。"
    summaries = [
        str(item.get("display_summary") or "").strip()
        for item in artifacts
        if isinstance(item, dict) and str(item.get("display_summary") or "").strip()
    ]
    if len(summaries) == 1:
        return summaries[0]
    if len(summaries) > 1:
        return "\n".join(summaries[:3])
    names = [
        str(item.get("name") or item.get("layer_id") or item.get("artifact_id") or "").strip()
        for item in artifacts
        if isinstance(item, dict)
    ]
    names = [item for item in names if item]
    if not names:
        return "已记录输出产物。"
    if len(names) == 1:
        return f"已记录输出产物 {names[0]}。"
    return f"已记录 {len(names)} 个输出产物：{', '.join(names[:3])}。"


def _warning_card_title(*, risk: dict[str, Any] | None = None, warning: dict[str, Any] | None = None) -> str:
    payload = dict(risk or {})
    if not payload and isinstance(warning, dict):
        payload = dict(warning.get("risk") or warning)
    category = str(payload.get("category") or "").strip()
    code = str(payload.get("code") or "").strip()
    if category == "crs_risk":
        return "CRS 提示"
    if category == "raster_risk":
        return "栅格提示"
    if category == "output_risk":
        return "输出提示"
    if category == "empty_result_risk" or code.startswith("empty_") or code == "contour_empty_output":
        return "空结果诊断"
    if category == "field_risk":
        return "字段提示"
    if category == "layer_ambiguity":
        return "图层提示"
    return "数据质量提示"


def _clarification_answer_text(decision: dict[str, Any]) -> str:
    selected = [
        str(item.get("label") or item.get("value") or "").strip()
        for item in list(decision.get("selected_choices") or [])
        if isinstance(item, dict) and str(item.get("label") or item.get("value") or "").strip()
    ]
    patch = dict(decision.get("slot_patch") or {})
    if selected:
        detail = "、".join(selected)
    elif patch:
        detail = "；".join(f"{key}={value}" for key, value in patch.items())
    else:
        detail = ""
    action = _display_title(str(decision.get("active_intent") or ""))
    if detail and action:
        return f"已确认 {detail}，继续{action}。"
    if detail:
        return f"已补充 {detail}。"
    if action:
        return f"已确认，继续{action}。"
    return ""


def _dedupe_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in timeline:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        item_type = str(item.get("type") or "").strip()
        if not item_type:
            continue
        item = ensure_transcript_item_identity(item)
        item_key = _timeline_item_key(item)
        if item_key:
            compact = [entry for entry in compact if _timeline_item_key(entry) != item_key]
            compact.append(dict(item))
            continue
        previous = compact[-1] if compact else {}
        if previous.get("type") == item_type and str(previous.get("text") or "").strip() == text:
            continue
        compact.append(dict(item))
    return compact


def _timeline_item_key(item: dict[str, Any]) -> tuple[str, ...]:
    event_key = str(item.get("event_key") or item.get("id") or "").strip()
    if event_key:
        return ("event_key", event_key)
    key = _transcript_item_event_key(item)
    if key:
        return ("event_key", key)
    return ()


def _transcript_item_event_key(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "").strip()
    run_id = str(item.get("run_id") or "").strip()
    message_key = str(item.get("message_id") or "").strip()
    if message_key:
        return f"message:{message_key}"
    if item_type == "workflow_step":
        index = int(item.get("index") or 0)
        if run_id and index > 0:
            return f"run:{run_id}:step:{index}"
        if index > 0:
            return f"legacy:step:{index}"
    seq = int(item.get("seq") or 0)
    if run_id and seq > 0:
        return f"run:{run_id}:seq:{seq}:{item_type}"
    return ""
