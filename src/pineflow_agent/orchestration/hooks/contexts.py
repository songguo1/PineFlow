"""Hook context objects and lifecycle points."""

from __future__ import annotations

import enum
from collections.abc import Callable
from typing import Any

class HookPoint(enum.Enum):
    BEFORE_RUN = "before_run"
    BEFORE_PROMPT_BUILD = "before_prompt_build"
    AFTER_PROMPT_BUILD = "after_prompt_build"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    AFTER_RUN = "after_run"


# Hook callable signatures
BeforeRunHook = Callable[["HookContext"], "HookContext"]
BeforePromptHook = Callable[["PromptContext"], "PromptContext"]
AfterPromptHook = Callable[["PromptBuildContext"], "PromptBuildContext"]
BeforeToolHook = Callable[["ToolContext"], "ToolContext"]
AfterToolHook = Callable[["ObservationContext"], "ObservationContext"]
AfterRunHook = Callable[["RunResultContext"], "RunResultContext"]


class HookContext:
    """Mutable context passed through BEFORE_RUN hooks."""

    __slots__ = ("user_request", "session_id", "session_memory", "state_tree", "prior_steps", "data")

    def __init__(
        self,
        user_request: str = "",
        session_id: str = "",
        session_memory: str = "",
        state_tree: dict[str, Any] | None = None,
        prior_steps: list[Any] | None = None,
    ) -> None:
        self.user_request = user_request
        self.session_id = session_id
        self.session_memory = session_memory
        self.state_tree = state_tree or {}
        self.prior_steps = prior_steps or []
        self.data: dict[str, Any] = {}


class PromptContext:
    """Mutable context passed through BEFORE_PROMPT_BUILD hooks."""

    __slots__ = ("user_request", "state", "previous_steps", "visible_tools", "tool_disclosure", "loaded_skills", "session_memory", "artifacts", "run_context", "data")

    def __init__(
        self,
        user_request: str = "",
        state: dict[str, Any] | None = None,
        previous_steps: list[Any] | None = None,
        visible_tools: list[str] | None = None,
        tool_disclosure: dict[str, Any] | None = None,
        loaded_skills: list[dict[str, Any]] | None = None,
        session_memory: str = "",
        artifacts: list[dict[str, Any]] | None = None,
        run_context: dict[str, Any] | None = None,
    ) -> None:
        self.user_request = user_request
        self.state = state or {}
        self.previous_steps = previous_steps or []
        self.visible_tools = visible_tools or []
        self.tool_disclosure = tool_disclosure or {}
        self.loaded_skills = loaded_skills or []
        self.session_memory = session_memory
        self.artifacts = artifacts or []
        self.run_context = run_context or {}
        self.data: dict[str, Any] = {}


class PromptBuildContext:
    """Mutable context passed through AFTER_PROMPT_BUILD hooks."""

    __slots__ = ("payload", "user_request", "data")

    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        user_request: str = "",
    ) -> None:
        self.payload = payload or {}
        self.user_request = user_request
        self.data: dict[str, Any] = {}


class ToolContext:
    """Mutable context passed through BEFORE_TOOL_CALL hooks."""

    __slots__ = ("plan", "state", "tool_registry", "rules_gateway", "_hard_validation_issues", "validation_issues", "preflight_warnings", "data")

    def __init__(
        self,
        plan: Any = None,
        state: Any = None,
        tool_registry: Any = None,
        rules_gateway: Any = None,
    ) -> None:
        self.plan = plan
        self.state = state
        self.tool_registry = tool_registry
        self.rules_gateway = rules_gateway
        self._hard_validation_issues: list[Any] = []
        self.validation_issues: list[Any] = []
        self.preflight_warnings: list[Any] = []
        self.data: dict[str, Any] = {}

    def add_hard_issues(self, issues: list[Any] | tuple[Any, ...]) -> None:
        self._hard_validation_issues.extend(list(issues or ()))

    def add_preflight_warnings(self, issues: list[Any] | tuple[Any, ...]) -> None:
        self.preflight_warnings.extend(list(issues or ()))

    def all_validation_issues(self) -> list[Any]:
        return list(self._hard_validation_issues) + list(self.validation_issues or [])


class ObservationContext:
    """Mutable context passed through AFTER_TOOL_CALL hooks."""

    __slots__ = ("plan", "observation", "step_index", "state", "artifact_index", "source_run_id", "data")

    def __init__(
        self,
        plan: Any = None,
        observation: Any = None,
        step_index: int = 0,
        state: Any = None,
        artifact_index: Any = None,
        source_run_id: str = "",
    ) -> None:
        self.plan = plan
        self.observation = observation
        self.step_index = step_index
        self.state = state
        self.artifact_index = artifact_index
        self.source_run_id = source_run_id
        self.data: dict[str, Any] = {}


class RunResultContext:
    """Mutable context passed through AFTER_RUN hooks."""

    __slots__ = ("result", "session_id", "user_request", "steps", "toolbox", "session_memory_before", "data")

    def __init__(
        self,
        result: Any = None,
        session_id: str = "",
        user_request: str = "",
        steps: list[Any] | None = None,
        toolbox: Any = None,
        session_memory_before: str = "",
    ) -> None:
        self.result = result
        self.session_id = session_id
        self.user_request = user_request
        self.steps = steps or []
        self.toolbox = toolbox
        self.session_memory_before = session_memory_before
        self.data: dict[str, Any] = {}
