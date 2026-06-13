"""Prompt builders for the ReAct GIS agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.llm.context_budget import ContextBudget, build_context_budget_report
from pineflow_agent.llm.context_builder import build_workspace_snapshot, compact_observation, compact_state_tree, compact_steps
from pineflow_agent.llm.step_context_pack import build_step_context_pack, compact_steps_for_prompt
from pineflow_agent.tools.contracts.tool_definitions import action_contracts, action_contracts_for


SYSTEM_PROMPT = """You are a GIS analysis agent using QGIS tools.

Operate as a ReAct tool-use loop. In each turn, reason about the current GIS state,
choose the next grounded GIS operation, and call exactly one provided native tool.
Do not answer with free text, Markdown, Python code, or multiple alternatives.
Do not call tools in parallel. Even when operations look independent, choose the
single next prerequisite tool and wait for its observation before choosing another.
If several requested operations are all currently possible, choose only the earliest
operation in the user's request order and wait before doing the next one.

Use only the provided tools. Do not invent tool names, QGIS algorithms, layer names,
file paths, fields, CRS values, or numeric parameters. If required information is
missing, choose the most grounded partial tool call and let validation ask the user;
do not hide uncertainty inside guessed arguments.

The prompt payload contains visible_tools and action_contracts for this turn. Never
call a tool that is not listed in visible_tools. If run_algorithm is not listed in
visible_tools, do not use it.

If a needed GIS capability is not available in visible_tools, do not explain that in
free text. Call select_toolkit with the needed ToolKit name(s), then wait for the
next turn when those tools become visible.

Prefer semantic GIS tools such as buffer_layer, clip_layer, dissolve_layer,
merge_layers, reproject_layer, intersect_layer, difference_layer,
extract_by_attribute, extract_by_location, join_by_location, field_calculator,
and csv_to_points when they match the request. Use discover_algorithms,
algorithm_help, and run_algorithm only for unsupported or uncommon Processing
algorithms.

Use existing state_tree layers when they are already loaded. Reference layers by
exact name or layer_id from available_layers unless loading a new path explicitly
supplied by the user.

Return schema-ready arguments:
- distances are positive numbers, with unit set to "meter" or "kilometer";
- CRS values use recognizable strings such as "EPSG:4326" or "EPSG:3857";
- layer references are exact layer names or layer_id values;
- booleans are true or false, not text;
- final_answer must include a message argument.

Call final_answer only when the GIS workflow is complete. After creating or exporting
outputs, the message must include the final output file name and full output path from
recent_artifacts, recent_outputs, or previous_steps. If multiple outputs exist, mention
the most relevant final product first.

"""

def available_layers(state: dict[str, Any]) -> list[dict[str, Any]]:
    layers = []
    for layer in list((state or {}).get("layers") or []):
        if not isinstance(layer, dict):
            continue
        metadata = dict(layer.get("metadata") or {})
        layers.append(
            {
                "layer_id": layer.get("layer_id"),
                "name": layer.get("name"),
                "kind": layer.get("kind"),
                "crs": metadata.get("crs"),
                "geometry_type": metadata.get("geometry_type"),
                "feature_count": metadata.get("feature_count"),
            }
        )
    return layers

RESUME_SYSTEM_PROMPT = """You are resuming a paused GIS agent task.

Use semantic judgement, not regex extraction and not a rigid form-filling workflow.
Read the saved task, the last question, the current GIS state, and the user's new reply.
Decide whether the reply gives enough meaning to continue the pending GIS operation.
Return only valid JSON. Do not wrap the JSON in Markdown fences.

Allowed decisions:
- patch_slots: the reply supplies, corrects, or confirms parameters for the pending task.
- replan: the reply changes the user's intent or asks for a different GIS action.
- ask_again: the reply is chat, ambiguous, unsafe, or still missing required GIS meaning.

For patch_slots, return slot_patch with schema-ready values only for information grounded in
the reply plus the saved pending task context. Preserve existing filled_slots unless the reply
clearly corrects them. Do not invent missing parameters.
For replan, return action and action_input using the same action contracts as the GIS agent.
For ask_again, return a concise user-facing message.
"""


def build_react_prompt(
    *,
    user_request: str,
    state: dict[str, Any],
    previous_steps: list[dict[str, Any]],
    visible_tools: list[str] | tuple[str, ...] | None = None,
    visible_action_contracts: dict[str, dict[str, Any]] | None = None,
    tool_disclosure: dict[str, Any] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    session_memory: str = "",
    run_context: dict[str, Any] | None = None,
    loaded_skills: list[dict[str, Any]] | None = None,
    skill_hints: list[str] | None = None,
    suggested_skills: list[dict[str, Any]] | None = None,
    already_compacted: bool = False,
) -> str:
    recent_artifacts = artifact_outputs_for_prompt(artifacts or [])
    if already_compacted:
        compact_state = dict(state or {})
        compact_previous_steps = compact_steps_for_prompt(list(previous_steps or []))
    else:
        compact_state = compact_state_tree(state)
        compact_previous_steps = compact_steps_for_prompt(compact_steps(previous_steps))
    recent_output_steps = compact_previous_steps if already_compacted else previous_steps
    recent_outputs = output_layers_from_steps(recent_output_steps)
    if visible_action_contracts is not None:
        contracts = dict(visible_action_contracts)
    elif visible_tools is not None:
        contracts = action_contracts_for(list(visible_tools))
    else:
        contracts = action_contracts()
    tool_names = list(visible_tools or contracts.keys())
    semantic_preference_rule = (
        "Prefer semantic GIS tools for common operations; use run_algorithm only when no semantic tool fits."
        if "run_algorithm" in tool_names
        else "Prefer the listed semantic GIS tools for common operations and stay within visible_tools."
    )
    payload: dict[str, Any] = {
        "prompt_mode": "native_tool_action",
        "user_request": user_request,
        "state_tree": make_json_safe(compact_state),
        "workspace_snapshot": build_workspace_snapshot(
            user_request=user_request,
            state=compact_state,
            previous_steps=compact_previous_steps,
            artifacts=list(artifacts or []),
            tool_disclosure=dict(tool_disclosure or {}),
            suggested_skills=list(suggested_skills or []),
        ),
        "step_context_pack": build_step_context_pack(
            user_request=user_request,
            state=compact_state,
            previous_steps=compact_previous_steps,
            artifacts=list(artifacts or []),
            run_context=dict(run_context or {}),
        ),
        "available_layers": make_json_safe(available_layers(compact_state)),
        "visible_tools": tool_names,
        "tool_disclosure": make_json_safe(dict(tool_disclosure or {})),
        "recent_artifacts": make_json_safe(recent_artifacts),
        "recent_outputs": make_json_safe(recent_outputs),
        "previous_steps": make_json_safe(compact_previous_steps),
        "decision_rules": [
            "If a needed layer is already in state_tree.layers, use that layer name or layer_id.",
            "Use only layer references listed in available_layers unless loading a user-supplied file path.",
            "Use only tool names listed in visible_tools. Never invent or call hidden tools.",
            "If a needed capability is hidden, call select_toolkit with the relevant toolkit names instead of writing a natural-language explanation.",
            "Use inspect_workspace if you need to inspect active ToolKits, available ToolKits, or current layer summaries before choosing a GIS tool.",
            "Use suggest_skill if you need structured GIS skill recommendations; use load_skill only when you need the full guidance content.",
            semantic_preference_rule,
            "If previous_steps already contain a successful action with the same grounded inputs, reuse its result instead of repeating it.",
            "If a semantic slot requires an input layer, pass the layer alias/id, not Python code.",
            "Call exactly one tool. Do not bundle independent operations or ask for simultaneous tool calls.",
            "If several requested operations are all currently possible, choose only the earliest operation in the user's request order.",
            "Do not fill required semantic slots with guesses when the user did not provide enough information.",
            "If a QGIS error appears in previous_steps, correct the next action based on that observation.",
            "When the requested analysis is complete, call final_answer.",
            "When calling final_answer and recent_artifacts is not empty, include the final artifact file_name and full path in the message.",
            "If recent_artifacts is empty but recent_outputs is not empty, include the final output file_name and full output_path in the message.",
            "If the user requested an export and a role=final artifact exists, prefer that exported file over intermediate outputs in the final_answer message.",
        ],
    }

    if skill_hints and not loaded_skills and isinstance(payload.get("decision_rules"), list):
        hints = ", ".join(skill_hints[:3])
        payload["decision_rules"].append(
            f"The user request may benefit from GIS skill(s): {hints}. "
            "Call load_skill with the most relevant skill name before the main GIS operation only if that guidance is needed."
        )
    elif skill_hints and isinstance(payload.get("decision_rules"), list):
        hints = ", ".join(skill_hints)
        payload["decision_rules"].append(
            f"Other potentially useful GIS skills: {hints}. "
            "Consider calling load_skill with one of these if more specific guidance is needed."
        )
    if suggested_skills:
        payload["suggested_skills"] = make_json_safe(
            [
                _skill_prompt_metadata({
                    "name": str(skill.get("name") or ""),
                    "description": str(skill.get("description") or ""),
                    "requires_toolkits": list(skill.get("requires_toolkits") or []),
                    "workspace_attention": list(skill.get("workspace_attention") or []),
                    "risk_awareness": list(skill.get("risk_awareness") or []),
                    "strategy_guidance": list(skill.get("strategy_guidance") or []),
                    "default_preferences": list(skill.get("default_preferences") or []),
                    "analysis_hints": list(skill.get("analysis_hints") or []),
                    "clarification_policy": list(skill.get("clarification_policy") or []),
                    "assumption_preferences": list(skill.get("assumption_preferences") or []),
                    "workspace_queries": list(skill.get("workspace_queries") or []),
                    "soft_clarification_hints": list(skill.get("soft_clarification_hints") or []),
                    "activation": dict(skill.get("activation") or {}),
                })
                for skill in suggested_skills
                if isinstance(skill, dict) and str(skill.get("name") or "").strip()
            ][:3]
        )
        if isinstance(payload.get("decision_rules"), list):
            payload["decision_rules"].append(
                "suggested_skills are metadata only, not loaded guidance. Call load_skill before using a skill's detailed instructions."
            )
            payload["decision_rules"].append(
                "Skills are a Domain Cognitive Guidance Layer: use them to improve GIS awareness, strategy, assumptions, and spatial reasoning; they are not execution authority."
            )
            payload["decision_rules"].append(
                "Use suggested_skills.workspace_queries as active sensing hints: inspect relevant layers, fields, CRS, outputs, or artifacts before choosing a risky GIS operation."
            )
            payload["decision_rules"].append(
                "Use suggested_skills.default_preferences and assumption_preferences for soft assumptions only; hard missing slots and correctness risks still go through validation and pending tasks."
            )
            payload["decision_rules"].append(
                "Use suggested_skills.clarification_policy to decide hard clarification, soft clarification, or silent assumption; never use a skill to bypass runtime validation, audit, repair, or resume contracts."
            )

    payload["canonicalization_rules"] = [
        "Return numeric distances as numbers, not strings such as '500m'.",
        "Use unit='meter' for meters and unit='kilometer' for kilometers.",
        "Use EPSG codes for CRS when possible, for example EPSG:4326 or EPSG:3857.",
        "When filling proactive_clarification.active_intent, use a canonical action id from visible_tools, not a display title or natural-language sentence; put human-readable wording in continue_with or question instead.",
        "For multi-layer inputs, input_refs must be an array with at least two exact layer refs.",
        "If the next action cannot be grounded in user_request, state_tree, or previous_steps, choose the safest validation-triggering action rather than guessing hidden values.",
    ]
    payload["action_contracts"] = contracts
    if session_memory and session_memory.strip():
        payload["session_memory"] = session_memory.strip()
    if loaded_skills:
        payload["loaded_skills"] = make_json_safe(
            [{"name": skill.get("name"), "content": skill.get("content")} for skill in loaded_skills if isinstance(skill, dict)]
        )
    payload["context_budget_report"] = build_context_budget_report(
        {key: value for key, value in payload.items() if key != "context_budget_report"},
        max_tokens=ContextBudget().max_tokens,
    )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _skill_prompt_metadata(skill: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "name": str(skill.get("name") or ""),
        "description": str(skill.get("description") or ""),
        "requires_toolkits": list(skill.get("requires_toolkits") or []),
    }
    keys = (
        "workspace_attention",
        "risk_awareness",
        "strategy_guidance",
        "default_preferences",
        "analysis_hints",
        "clarification_policy",
        "assumption_preferences",
        "workspace_queries",
        "soft_clarification_hints",
        "activation",
    )
    for key in keys:
        if key == "activation":
            value = dict(skill.get(key) or {}) if isinstance(skill.get(key), dict) else {}
        else:
            value = list(skill.get(key) or [])
        if value:
            payload[key] = value
    return payload


def output_layers_from_steps(previous_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for step in list(previous_steps or []):
        if not isinstance(step, dict):
            continue
        observation = step.get("observation")
        if not isinstance(observation, dict):
            continue
        if str(observation.get("status") or "") != "success":
            continue
        output_path = str(observation.get("output_path") or "").strip()
        output_layer_id = str(observation.get("output_layer_id") or "").strip()
        if not output_path and not output_layer_id:
            continue
        outputs.append(
            {
                "step_index": step.get("index"),
                "action": step.get("action"),
                "output_layer_id": output_layer_id,
                "output_path": output_path,
                "file_name": Path(output_path).name if output_path else "",
            }
        )
    return outputs


def artifact_outputs_for_prompt(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    for artifact in list(artifacts or []):
        if not isinstance(artifact, dict):
            continue
        role = str(artifact.get("role") or "")
        if role == "input":
            continue
        path = str(artifact.get("path") or artifact.get("output_path") or "").strip()
        layer_id = str(artifact.get("layer_id") or "").strip()
        if not path and not layer_id:
            continue
        outputs.append(
            {
                "artifact_id": str(artifact.get("artifact_id") or ""),
                "role": role or "intermediate",
                "name": str(artifact.get("name") or ""),
                "kind": str(artifact.get("kind") or ""),
                "layer_id": layer_id,
                "path": path,
                "file_name": Path(path).name if path else "",
                "algorithm_id": str(artifact.get("algorithm_id") or ""),
                "source_step": artifact.get("source_step"),
                "parent_ids": list(artifact.get("parent_ids") or []),
                "crs": str(artifact.get("crs") or ""),
                "geometry_type": str(artifact.get("geometry_type") or ""),
                "feature_count": artifact.get("feature_count"),
                "fields": list(artifact.get("fields") or [])[:20],
                "extent": artifact.get("extent"),
                "lineage": dict(artifact.get("lineage") or {}),
                "reusable": bool(artifact.get("reusable", True)),
                "materialized": bool(artifact.get("materialized", True)),
            }
        )
    return sorted(outputs, key=lambda item: 0 if item.get("role") == "final" else 1)


def build_repair_prompt(
    *,
    user_request: str,
    state: dict[str, Any],
    previous_steps: list[dict[str, Any]],
    original_action: dict[str, Any],
    failed_observation: dict[str, Any],
    repair_session: dict[str, Any],
    visible_tools: list[str] | tuple[str, ...] | None = None,
    visible_action_contracts: dict[str, dict[str, Any]] | None = None,
) -> str:
    compact_state = compact_state_tree(state)
    compact_previous_steps = compact_steps(previous_steps)
    compact_failed_observation = compact_observation(failed_observation)
    if visible_action_contracts is not None:
        contracts = dict(visible_action_contracts)
    elif visible_tools is not None:
        contracts = action_contracts_for(list(visible_tools))
    else:
        contracts = action_contracts()
    tool_names = list(visible_tools or contracts.keys())
    repair_preference_rule = (
        "Prefer deterministic GIS repairs such as reproject_layer, fix_geometries, algorithm_help, or discover_algorithms when they address the error."
        if "run_algorithm" in tool_names
        else "Prefer deterministic GIS repairs such as reproject_layer, algorithm_help, or discover_algorithms when they address the error."
    )
    payload = {
        "prompt_mode": "native_tool_repair_action",
        "repair_mode": True,
        "user_request": user_request,
        "state_tree": make_json_safe(compact_state),
        "available_layers": make_json_safe(available_layers(compact_state)),
        "visible_tools": tool_names,
        "previous_steps": make_json_safe(compact_previous_steps),
        "original_action": make_json_safe(original_action),
        "failed_observation": make_json_safe(compact_failed_observation),
        "repair_session": make_json_safe(repair_session),
        "repair_rules": [
            "Choose exactly one tool call that repairs the failed original_action.",
            "Do not call final_answer in repair mode.",
            "Do not retry the original_action yourself; the system will retry it after the repair tool succeeds.",
            "Use only tool names listed in visible_tools.",
            "Use existing layer names or layer_id values from available_layers.",
            repair_preference_rule,
            "Do not guess missing user intent; if the error cannot be repaired with a grounded tool call, choose the safest inspection or discovery tool.",
        ],
        "action_contracts": contracts,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_resume_prompt(
    *,
    user_reply: str,
    pending_task: dict[str, Any],
    state: dict[str, Any],
) -> str:
    compact_state = compact_state_tree(state)
    payload = {
        "resume_task": True,
        "user_reply": user_reply,
        "task_context": {
            "active_intent": pending_task.get("active_intent"),
            "original_request": pending_task.get("original_request"),
            "last_question": pending_task.get("last_question"),
            "filled_slots": pending_task.get("filled_slots") or {},
            "missing_slots": pending_task.get("missing_slots") or [],
            "correction_history": pending_task.get("correction_history") or [],
        },
        "pending_task": make_json_safe(pending_task),
        "state_tree": make_json_safe(compact_state),
        "available_layers": make_json_safe(available_layers(compact_state)),
        "decision_schema": {
            "decision": "patch_slots | replan | ask_again",
            "reason": "short explanation",
            "slot_patch": "object; only for patch_slots",
            "action": "string; only for replan",
            "action_input": "object; only for replan",
            "message": "short user-facing question; only for ask_again",
        },
        "semantic_judgement_guidance": [
            "Treat short replies as answers to last_question when the meaning is clear in context.",
            "Return canonical values expected by action_contracts, not prose.",
            "For distances, set distance to a positive number and unit to meter or kilometer when the reply expresses a unit.",
            "For CRS, use a recognizable CRS string such as EPSG:3857 when the reply clearly names one.",
            "For layer references, choose only from available_layers by layer_id or name.",
            "If the reply is unrelated small talk, acknowledge briefly with ask_again and repeat what is needed.",
        ],
        "action_contracts": action_contracts(),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
