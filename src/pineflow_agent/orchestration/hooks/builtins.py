"""Built-in hook implementations for the ReAct GIS agent."""

from __future__ import annotations

import logging
from typing import Any

from pineflow_agent.orchestration.hooks.contexts import HookPoint, ObservationContext, PromptContext, RunResultContext, ToolContext

_log = logging.getLogger(__name__)

def _register_builtin_hooks(pipeline: HookPipeline) -> None:
    """Register the built-in hooks that replace current hardcoded logic."""

    # BEFORE_PROMPT_BUILD: skill injection + context compaction
    pipeline.register(
        HookPoint.BEFORE_PROMPT_BUILD,
        _skill_inject_hook,
        name="skill_inject",
        priority=90,
    )
    pipeline.register(
        HookPoint.BEFORE_PROMPT_BUILD,
        _compact_context_hook,
        name="context_compact",
        priority=100,
    )

    # BEFORE_TOOL_CALL: hard validation gate
    pipeline.register(
        HookPoint.BEFORE_TOOL_CALL,
        _validation_rules_hook,
        name="validation_rules",
        priority=100,
        critical=True,
    )

    # AFTER_TOOL_CALL: artifact registration
    pipeline.register(
        HookPoint.AFTER_TOOL_CALL,
        _artifact_register_hook,
        name="artifact_register",
        priority=100,
    )
    pipeline.register(
        HookPoint.AFTER_TOOL_CALL,
        _postflight_hook,
        name="postflight_check",
        priority=110,
    )

    # AFTER_RUN: session memory write
    pipeline.register(
        HookPoint.AFTER_RUN,
        _session_memory_write_hook,
        name="session_memory_write",
        priority=100,
    )


# ── Built-in hook implementations ──────────────────────────────────────────

def _validation_rules_hook(ctx: ToolContext) -> ToolContext:
    """Run semantic + preflight validation before tool execution."""
    plan = ctx.plan
    if plan is None:
        return ctx
    gateway = ctx.rules_gateway
    if gateway is None:
        from pineflow_agent.rules.rules_gateway import RulesGateway
        gateway = RulesGateway()
    semantic = gateway.semantic_issues(plan, ctx.tool_registry)
    if semantic:
        ctx.add_hard_issues(list(semantic))
        return ctx
    preflight = gateway.preflight_issues(plan, ctx.state)
    if preflight:
        hard, warnings = _split_blocking_validation_issues(preflight)
        ctx.add_hard_issues(hard)
        ctx.add_preflight_warnings(warnings)
    return ctx


def _split_blocking_validation_issues(issues: list[Any]) -> tuple[list[Any], list[Any]]:
    hard: list[Any] = []
    warnings: list[Any] = []
    for issue in list(issues or []):
        repair = getattr(issue, "repair", None)
        requires_confirmation = bool(getattr(repair, "requires_confirmation", False))
        if str(getattr(issue, "severity", "") or "") == "warning" and not requires_confirmation:
            warnings.append(issue)
        else:
            hard.append(issue)
    return hard, warnings


def _compact_context_hook(ctx: PromptContext) -> PromptContext:
    """Compact state tree and previous steps to control token usage."""
    from pineflow_agent.llm.context_builder import compact_state_tree, compact_steps
    from pineflow_agent.llm.context_budget import ContextBudget
    from pineflow_agent.core.json_safety import make_json_safe

    budget = ContextBudget()
    ctx.state = make_json_safe(dict(compact_state_tree(ctx.state)))
    ctx.previous_steps = make_json_safe(list(compact_steps(ctx.previous_steps)))
    ctx.session_memory = budget.allocate("session_memory", str(ctx.session_memory or ""))
    ctx.loaded_skills = _budget_loaded_skills(ctx.loaded_skills, budget=budget)
    return ctx


def _skill_inject_hook(ctx: PromptContext) -> PromptContext:
    """Attach explicit skill content or non-invasive skill suggestions.

    Priority chain:
    1. Skills explicitly loaded via load_skill steps -> inject their content.
    2. Runtime context signals -> suggest skill names only.
    """
    existing = _loaded_skills_from_steps(ctx.previous_steps)
    if existing:
        ctx.loaded_skills = existing
        return ctx

    user_request = str(ctx.user_request or "").strip()
    if not user_request:
        return ctx

    try:
        from pineflow_agent.tools.registry.skill_registry import default_skill_registry
        from pineflow_agent.tools.registry.skill_activation import build_skill_activation_context
        registry = default_skill_registry()
        activation_context = build_skill_activation_context(
            user_request=user_request,
            state=ctx.state,
            artifacts=ctx.artifacts,
            visible_tools=ctx.visible_tools,
            tool_disclosure=ctx.tool_disclosure,
            previous_steps=ctx.previous_steps,
        )
        suggested = registry.suggest(user_request, limit=3, context=activation_context)
    except Exception:
        _log.warning("Skill activation failed", exc_info=True)
        return ctx

    if not suggested:
        return ctx

    ctx.data["skill_activation_context"] = activation_context.to_dict()
    ctx.data["skill_hints"] = [str(item.get("name") or "") for item in suggested if str(item.get("name") or "")]
    ctx.data["suggested_skills"] = suggested
    return ctx


def _artifact_register_hook(ctx: ObservationContext) -> ObservationContext:
    """Register successful tool outputs as artifacts."""
    if ctx.artifact_index is None:
        return ctx
    obs = ctx.observation
    if obs is None or not getattr(obs, "is_success", False):
        return ctx
    try:
        from pineflow_agent.orchestration.execution.execution_step import register_observation_artifact
        register_observation_artifact(
            obs,
            plan=ctx.plan,
            source_step=ctx.step_index,
            source_run_id=str(getattr(ctx, "source_run_id", "") or ""),
            artifact_index=ctx.artifact_index,
        )
    except Exception:
        _log.warning("Artifact registration failed for step %s", ctx.step_index, exc_info=True)
    return ctx


def _postflight_hook(ctx: ObservationContext) -> ObservationContext:
    """Attach non-blocking warnings about suspicious successful outputs."""
    obs = ctx.observation
    if obs is None or not getattr(obs, "is_success", False):
        return ctx
    data = dict(getattr(obs, "data", None) or {})
    existing = [item for item in list(data.get("postflight_warnings") or []) if isinstance(item, dict)]
    warnings = _postflight_warnings(ctx)
    all_warnings = _risk_enriched_warnings(existing + warnings, tool_name=str(getattr(ctx.plan, "action", "") or ""))
    if not all_warnings:
        return ctx
    data["postflight_warnings"] = all_warnings
    obs.data = data
    ctx.data["postflight_warnings"] = all_warnings
    return ctx


def _risk_enriched_warnings(warnings: list[dict[str, Any]], *, tool_name: str) -> list[dict[str, Any]]:
    if not warnings:
        return []
    from pineflow_agent.risks.converters import risk_from_warning, warning_from_risk

    enriched: list[dict[str, Any]] = []
    for warning in warnings:
        payload = dict(warning)
        if not isinstance(payload.get("risk"), dict) or not payload.get("risk"):
            risk = risk_from_warning(payload, tool_name=tool_name)
            payload.update(warning_from_risk(risk))
        enriched.append(payload)
    return enriched


def _session_memory_write_hook(ctx: RunResultContext) -> RunResultContext:
    """Append a run summary to session_memory.md."""
    result = ctx.result
    if result is None:
        return ctx
    if str(getattr(result, "status", "") or "").startswith("awaiting_"):
        return ctx

    workspace = getattr(ctx.toolbox, "workspace", None)
    if workspace is None:
        return ctx

    try:
        from pineflow_agent.core.workspace_state import WorkspaceStateStore

        store = WorkspaceStateStore(workspace)
        meta_actions = {"final_answer", "select_toolkit", "inspect_workspace", "load_skill", "proactive_clarification"}
        tools_used = list(dict.fromkeys(
            str(getattr(step, "action", "") or "")
            for step in list(ctx.steps or [])
            if str(getattr(step, "action", "") or "") and str(getattr(step, "action", "") or "") not in meta_actions
        ))
        final_message = str(getattr(result, "final_message", "") or "")
        lines = [
            "",
            "---",
            "",
            f"## {_utc_now_for_memory()}",
            f"- **Request**: {ctx.user_request[:200]}",
            f"- **Steps**: {len(ctx.steps)}",
        ]
        if tools_used:
            lines.append(f"- **Tools**: {', '.join(tools_used)}")
        if final_message:
            lines.append(f"- **Result**: {final_message[:300]}")
        lines.append("")
        store.write_memory((ctx.session_memory_before or "").rstrip() + "\n".join(lines))
    except Exception:
        _log.warning("Session memory write failed for session %s", ctx.session_id, exc_info=True)
        return ctx
    return ctx


def _loaded_skills_from_steps(steps: list[Any]) -> list[dict[str, Any]]:
    """Extract recently loaded skills for prompt injection."""
    try:
        from pineflow_agent.tools.registry.skill_registry import default_skill_registry
    except Exception:
        _log.warning("Skill registry import failed", exc_info=True)
        return []

    skills: list[dict[str, Any]] = []
    seen: set[str] = set()
    registry = default_skill_registry()
    total_chars = 0
    max_total_chars = 3000

    for step in list(steps or [])[-12:]:
        action = getattr(step, "action", None) if hasattr(step, "action") else (step.get("action") if isinstance(step, dict) else None)
        if action != "load_skill":
            continue
        obs = getattr(step, "observation", None) if hasattr(step, "observation") else (step.get("observation") if isinstance(step, dict) else None)
        if not obs:
            continue
        data = getattr(obs, "data", None) if hasattr(obs, "data") else (obs.get("data") if isinstance(obs, dict) else None)
        if not isinstance(data, dict):
            continue
        name = str(data.get("skill_name") or "").strip()
        content = str(data.get("skill_content") or "").strip()
        if not name or not content or name in seen:
            continue

        meta = registry.get(name)
        limit = meta.max_chars if meta is not None else 0
        if limit and len(content) > limit:
            content = content[:limit] + "\n\n... (truncated)"

        if total_chars + len(content) > max_total_chars:
            remaining = max_total_chars - total_chars
            if remaining > 200:
                content = content[:remaining] + "\n\n... (truncated)"
            else:
                break

        seen.add(name)
        total_chars += len(content)
        skills.append({"name": name, "content": content})
    return skills


def _budget_loaded_skills(skills: list[dict[str, Any]], *, budget: Any) -> list[dict[str, Any]]:
    if not skills:
        return []
    remaining_chars = max(budget.section_limit("loaded_skills") * 3, 0)
    result: list[dict[str, Any]] = []
    for item in list(skills or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        content = str(item.get("content") or "").strip()
        if not name or not content or remaining_chars <= 0:
            continue
        if len(content) > remaining_chars:
            if remaining_chars <= 200:
                break
            content = content[:remaining_chars] + "\n\n... (truncated)"
        remaining_chars -= len(content)
        result.append({"name": name, "content": content})
    return result


def _utc_now_for_memory() -> str:
    from datetime import UTC, datetime
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")




def _postflight_warnings(ctx: ObservationContext) -> list[dict[str, Any]]:
    observation = ctx.observation
    from pineflow_agent.risks.converters import risk_from_warning, warning_from_risk
    from pineflow_agent.risks.empty_result import EmptyResultDiagnoser

    data = dict(getattr(observation, "data", None) or {})
    layer = data.get("layer")
    if not isinstance(layer, dict):
        return []
    metadata = dict(layer.get("metadata") or {})
    layer_name = str(layer.get("name") or layer.get("layer_id") or "").strip()
    kind = str(layer.get("kind") or "").strip()
    algorithm_id = str(layer.get("algorithm_id") or "").strip()
    warnings: list[dict[str, Any]] = []

    # ── 1. 空输出 ──────────────────────────────────────────────────────
    if metadata.get("feature_count") == 0:
        warnings.append(
            {"code": "empty_feature_output",
             "message": f"Output layer '{layer_name or '<unknown>'}' contains 0 features.",
             "layer": layer_name,
             "artifact": dict(layer)}
        )
    if metadata.get("row_count") == 0:
        warnings.append(
            {"code": "empty_table_output",
             "message": f"Output table '{layer_name or '<unknown>'}' contains 0 rows.",
             "layer": layer_name,
             "artifact": dict(layer)}
        )

    # ── 2. 几何类型缺失 ────────────────────────────────────────────────
    if kind == "vector" and not str(metadata.get("geometry_type") or "").strip():
        warnings.append(
            {"code": "unknown_geometry_type",
             "message": f"Output vector layer '{layer_name or '<unknown>'}' has unknown geometry type.",
             "layer": layer_name,
             "artifact": dict(layer)}
        )

    # ── 3. 输出文件是否存在 ────────────────────────────────────────────
    output_path = str(getattr(observation, "output_path", "") or "").strip()
    if output_path:
        try:
            from pathlib import Path
            if not Path(output_path).exists():
                warnings.append(
                    {"code": "output_file_missing",
                     "message": f"Output file not found on disk: {output_path}",
                     "layer": layer_name, "output_path": output_path, "artifact": dict(layer)}
                )
        except Exception:
            pass

    # ── 4. CRS 检查 ────────────────────────────────────────────────────
    crs = str(metadata.get("crs") or "").strip()
    if not crs:
        warnings.append(
            {"code": "output_crs_unknown",
             "message": f"Output layer '{layer_name or '<unknown>'}' has no CRS defined.",
             "layer": layer_name,
             "artifact": dict(layer)}
        )

    # ── 5. 字段检查 ────────────────────────────────────────────────────
    fields = metadata.get("field_summaries") if isinstance(metadata.get("field_summaries"), list) else metadata.get("fields")
    if isinstance(fields, list) and len(fields) > 0:
        numeric_fields = _numeric_field_count(fields)
        if numeric_fields == 0:
            warnings.append(
                {"code": "no_numeric_fields",
                 "message": f"Output layer '{layer_name or '<unknown>'}' has no numeric fields "
                            f"({len(fields)} fields all non-numeric).",
                    "layer": layer_name, "field_count": len(fields), "artifact": dict(layer)}
            )

    # ── 6. feature_count 异常变化 ──────────────────────────────────────
    fc = metadata.get("feature_count")
    if isinstance(fc, int) and fc > 0:
        parent_ids = list(layer.get("parent_ids") or [])
        if parent_ids:
            action = str(getattr(observation, "action", "") or layer.get("action") or algorithm_id or "").strip()
            if any(op in action.lower() for op in ("clip", "extract", "intersect", "filter")):
                warnings.append(
                    {"code": "feature_count_check_note",
                     "message": f"'{layer_name or '<unknown>'}' produced {fc} features from a clip/extract/extraction; "
                                "verify output is not empty and count is within expected range.",
                     "layer": layer_name, "feature_count": fc, "artifact": dict(layer)}
                )
            if "join" in action.lower():
                warnings.append(
                    {"code": "feature_count_check_note",
                     "message": f"'{layer_name or '<unknown>'}' produced {fc} features from a join; "
                                "verify count matches expectations (many-to-one joins may inflate counts).",
                     "layer": layer_name, "feature_count": fc, "artifact": dict(layer)}
                )

    # ── 7. 栅格 postflight: slope 值范围 ──────────────────────────────────
    if algorithm_id == "gdal:slope":
        if kind == "raster":
            warnings.append(
                {"code": "raster_slope_output",
                 "message": f"Slope raster '{layer_name or '<unknown>'}' generated. "
                            "Verify values are in 0-90° range; values outside this range may indicate DEM NoData artifacts.",
                 "layer": layer_name,
                 "artifact": dict(layer),
                 "algorithm_id": algorithm_id}
            )

    # ── 8. 栅格 postflight: hillshade 检查 ────────────────────────────────
    if algorithm_id == "gdal:hillshade":
        if kind == "raster":
            warnings.append(
                {"code": "raster_hillshade_output",
                 "message": f"Hillshade raster '{layer_name or '<unknown>'}' generated. "
                            "Visually verify the output is not all-black. If so, check Z_FACTOR (elevation units) or SCALE settings.",
                 "layer": layer_name,
                 "artifact": dict(layer),
                 "algorithm_id": algorithm_id}
            )

    # ── 9. 栅格 postflight: contour 非空 ──────────────────────────────────
    if algorithm_id == "gdal:contour":
        if kind == "vector" and metadata.get("feature_count") == 0:
            warnings.append(
                {"code": "contour_empty_output",
                 "message": f"Contour layer '{layer_name or '<unknown>'}' has 0 features. "
                            "The contour interval may be larger than the DEM elevation range. Try a smaller interval.",
                 "layer": layer_name,
                 "artifact": dict(layer),
                 "algorithm_id": algorithm_id}
            )

    for warning in warnings:
        if warning.get("code") in {"empty_feature_output", "empty_table_output", "contour_empty_output"}:
            warning["diagnosis"] = EmptyResultDiagnoser().diagnose(
                plan=ctx.plan,
                observation=observation,
                state=ctx.state,
            )
            warning["affects_result_trust"] = True
        risk = risk_from_warning(warning, tool_name=str(getattr(ctx.plan, "action", "") or ""))
        warning.update(warning_from_risk(risk))
    return warnings


def _numeric_field_count(fields: list) -> int | None:
    numeric_types = ("int", "integer", "float", "double", "real", "numeric", "decimal", "number", "long", "short")
    count = 0
    typed_count = 0
    for f in fields:
        field_type = _field_type_text(f)
        if not field_type:
            continue
        typed_count += 1
        if any(kw in field_type for kw in numeric_types):
            count += 1
    if typed_count == 0:
        return None
    return count


def _field_type_text(field: Any) -> str:
    if not isinstance(field, dict):
        return ""
    value = field.get("type") or field.get("field_type") or field.get("type_name") or field.get("typeName")
    return str(value or "").strip().lower()
