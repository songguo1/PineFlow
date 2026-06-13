"""Prompt assembly and native tool selection for the ReAct loop."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pineflow_agent.core.models import ActionPlan, ReActStep
from pineflow_agent.llm.llm import LLMClient
from pineflow_agent.llm.prompts import SYSTEM_PROMPT, build_react_prompt
from pineflow_agent.orchestration.hooks.contexts import HookPoint, PromptBuildContext, PromptContext
from pineflow_agent.tools.contracts.tool_definitions import action_contracts_for
from pineflow_agent.tools.registry.tool_registry import ToolRegistry
from pineflow_agent.tools.registry.toolkits import ToolDisclosureController, ToolDisclosureOptions, ToolKitRegistry


@dataclass
class PromptAssembler:
    llm: LLMClient
    hooks: Any
    tool_registry: ToolRegistry
    toolkit_registry: ToolKitRegistry
    tool_disclosure_options: ToolDisclosureOptions

    def next_action(
        self,
        *,
        user_request: str,
        state: dict[str, Any],
        steps: list[ReActStep],
        session_memory: str = "",
        artifacts: list[dict[str, Any]] | None = None,
        run_context: dict[str, Any] | None = None,
    ) -> tuple[ActionPlan, ToolDisclosureController]:
        tool_disclosure = ToolDisclosureController.from_steps(
            steps,
            options=self.tool_disclosure_options,
            toolkit_registry=self.toolkit_registry,
        )
        allow: tuple[str, ...] = ()
        if should_unlock_run_algorithm(steps):
            allow = allow + ("run_algorithm",)
        selected_tools = tool_disclosure.visible_tools(
            self.tool_registry,
            allow=allow,
        )
        prompt_ctx = PromptContext(
            user_request=user_request,
            state=state,
            previous_steps=[step.to_dict() for step in steps],
            visible_tools=list(selected_tools),
            tool_disclosure=tool_disclosure.prompt_catalog(self.tool_registry),
            loaded_skills=[],
            session_memory=session_memory,
            artifacts=list(artifacts or []),
            run_context=dict(run_context or {}),
        )
        self.hooks.emit(HookPoint.BEFORE_PROMPT_BUILD, prompt_ctx)

        prompt = build_react_prompt(
            user_request=user_request,
            state=prompt_ctx.state,
            previous_steps=prompt_ctx.previous_steps,
            visible_tools=prompt_ctx.visible_tools,
            visible_action_contracts=action_contracts_for(list(selected_tools)),
            tool_disclosure=prompt_ctx.tool_disclosure,
            artifacts=prompt_ctx.artifacts,
            session_memory=prompt_ctx.session_memory,
            run_context=prompt_ctx.run_context,
            loaded_skills=prompt_ctx.loaded_skills,
            skill_hints=list(prompt_ctx.data.get("skill_hints") or []),
            suggested_skills=list(prompt_ctx.data.get("suggested_skills") or []),
            already_compacted=True,
        )
        prompt_payload = json.loads(prompt)
        build_ctx = self.hooks.emit(
            HookPoint.AFTER_PROMPT_BUILD,
            PromptBuildContext(payload=prompt_payload, user_request=user_request),
        )
        prompt = json.dumps(build_ctx.payload, ensure_ascii=False, indent=2)

        plan = self.llm.tool_call(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            tools=[tool.json_schema for tool in selected_tools.values()],
        )
        if plan.action not in selected_tools:
            visible = ", ".join(selected_tools)
            catalog = ", ".join(self.toolkit_registry.names())
            raise ValueError(
                f"tool_unavailable: Model selected unavailable tool {plan.action or '<empty>'}. "
                f"Visible tools: {visible}. Available ToolKits: {catalog}. "
                "Call select_toolkit before using hidden tools."
            )
        return plan, tool_disclosure


def should_unlock_run_algorithm(steps: list[ReActStep]) -> bool:
    for step in reversed(list(steps or [])):
        if not step.observation.is_success:
            continue
        if step.action in {"discover_algorithms", "algorithm_help"}:
            return True
    return False
