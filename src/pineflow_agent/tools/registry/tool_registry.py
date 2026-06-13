"""Tool registry and provider interfaces for the GIS ReAct runtime."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pineflow_agent.core.models import ActionPlan, Observation
from pineflow_agent.tools.semantic.semantic_tools import is_semantic_action
from pineflow_agent.tools.contracts.tool_definitions import tool_definitions
from pineflow_agent.tools.qgis.toolbox import QGISToolbox

ToolExecutor = Callable[[QGISToolbox, dict[str, Any]], Observation]


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    json_schema: dict[str, Any]
    category: str
    provider: str = "builtin_qgis"
    executor: ToolExecutor | None = None
    groups: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    layer_requirements: tuple[tuple[str, str], ...] = ()
    geometry_requirements: tuple[tuple[str, str], ...] = ()
    field_requirements: tuple[tuple[str, tuple[str, ...]], ...] = ()
    semantic_rules: tuple[str, ...] = ()
    preflight_rules: tuple[str, ...] = ()
    terminal: bool = False

    def execute(self, toolbox: QGISToolbox, action_input: dict[str, Any]) -> Observation:
        if self.terminal:
            return Observation(
                status="error",
                message=f"Terminal tool {self.name} must be handled by the agent runtime.",
                data={"action": self.name, "provider": self.provider, "terminal": True},
            )
        if self.executor is None:
            return Observation(
                status="error",
                message=f"Tool {self.name} is registered but has no executor.",
                data={"action": self.name, "provider": self.provider},
            )
        return self.executor(toolbox, dict(action_input or {}))


class ToolProvider(Protocol):
    name: str

    def tools(self) -> Iterable[RegisteredTool]:
        """Return tools exposed by this provider."""


@dataclass
class ToolRegistry:
    providers: list[ToolProvider] = field(default_factory=list)

    @classmethod
    def default(cls) -> "ToolRegistry":
        return cls(providers=[BuiltinQGISToolProvider(), SkillToolProvider()])

    def registered_tools(self) -> dict[str, RegisteredTool]:
        tools: dict[str, RegisteredTool] = {}
        for provider in self.providers:
            for tool in provider.tools():
                tools[tool.name] = tool
        return tools

    def openai_tools(self) -> list[dict[str, Any]]:
        return [tool.json_schema for tool in self.registered_tools().values()]

    def execute(self, plan: ActionPlan, toolbox: QGISToolbox) -> Observation:
        tool = self.registered_tools().get(plan.action)
        if tool is None:
            return Observation(
                status="error",
                message=f"Unknown action: {plan.action}",
                data={"action": plan.action},
            )
        action_input = dict(plan.action_input or {})
        if is_semantic_action(plan.action):
            action_input["__action"] = plan.action
        return tool.execute(toolbox, action_input)


class BuiltinQGISToolProvider:
    name = "builtin_qgis"

    def tools(self) -> Iterable[RegisteredTool]:
        for definition in tool_definitions().values():
            yield RegisteredTool(
                name=definition.name,
                description=definition.description,
                json_schema=definition.openai_schema(),
                category=definition.category,
                provider=self.name,
                executor=definition.executor,
                groups=definition.groups,
                tags=definition.tags,
                layer_requirements=definition.layer_requirements,
                geometry_requirements=definition.geometry_requirements,
                field_requirements=definition.field_requirements,
                semantic_rules=definition.semantic_rules,
                preflight_rules=definition.preflight_rules,
                terminal=definition.terminal,
            )


class SkillToolProvider:
    """Expose GIS skills as loadable tool references via the registry."""
    name = "skill"

    def tools(self) -> Iterable[RegisteredTool]:
        try:
            from pineflow_agent.tools.registry.skill_registry import default_skill_registry
            registry = default_skill_registry()
        except Exception:
            return

        for name, meta in registry._skills.items():
            yield RegisteredTool(
                name=f"skill/{name}",
                description=meta.description,
                json_schema={
                    "type": "function",
                    "function": {
                        "name": f"skill/{name}",
                        "description": meta.description,
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                },
                category="skill",
                provider=self.name,
                executor=None,
                groups=(),
                tags=(),
            )
