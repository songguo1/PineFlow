"""File-system-driven GIS skill registry.

Scans resources/skills/*.md, parses YAML frontmatter, and exposes skill metadata
for tool definition generation and runtime skill loading.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_agent.tools.registry.skill_activation import SkillActivationContext, activation_for_skill, should_activate_skill


@dataclass(frozen=True)
class SkillMeta:
    name: str
    description: str
    requires_toolkits: tuple[str, ...] = ()
    workspace_attention: tuple[str, ...] = ()
    risk_awareness: tuple[str, ...] = ()
    strategy_guidance: tuple[str, ...] = ()
    default_preferences: tuple[str, ...] = ()
    analysis_hints: tuple[str, ...] = ()
    clarification_policy: tuple[dict[str, Any], ...] = ()
    assumption_preferences: tuple[str, ...] = ()
    workspace_queries: tuple[dict[str, Any], ...] = ()
    soft_clarification_hints: tuple[dict[str, Any], ...] = ()
    max_chars: int = 0
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return make_json_safe({
            "name": self.name,
            "description": self.description,
            "requires_toolkits": list(self.requires_toolkits),
            "workspace_attention": list(self.workspace_attention),
            "risk_awareness": list(self.risk_awareness),
            "strategy_guidance": list(self.strategy_guidance),
            "default_preferences": list(self.default_preferences),
            "analysis_hints": list(self.analysis_hints),
            "clarification_policy": [dict(item) for item in self.clarification_policy],
            "assumption_preferences": list(self.assumption_preferences),
            "workspace_queries": [dict(item) for item in self.workspace_queries],
            "soft_clarification_hints": [dict(item) for item in self.soft_clarification_hints],
            "max_chars": self.max_chars,
            "path": self.path,
        })


def _parse_skill(path: Path) -> SkillMeta | None:
    """Parse a skill .md file, extracting YAML frontmatter and body."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    name = path.stem
    description = ""
    requires_toolkits: tuple[str, ...] = ()
    workspace_attention: tuple[str, ...] = ()
    risk_awareness: tuple[str, ...] = ()
    strategy_guidance: tuple[str, ...] = ()
    default_preferences: tuple[str, ...] = ()
    analysis_hints: tuple[str, ...] = ()
    clarification_policy: tuple[dict[str, Any], ...] = ()
    assumption_preferences: tuple[str, ...] = ()
    workspace_queries: tuple[dict[str, Any], ...] = ()
    soft_clarification_hints: tuple[dict[str, Any], ...] = ()
    max_chars = 0

    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            frontmatter = text[3:end].strip()
            data = _parse_frontmatter(frontmatter)
            name = str(data.get("name") or name).strip() or name
            description = str(data.get("description") or "").strip()
            requires_toolkits = _parse_string_list(data.get("requires_toolkits"))
            workspace_attention = _parse_string_list(data.get("workspace_attention"))
            risk_awareness = _parse_string_list(data.get("risk_awareness"))
            strategy_guidance = _parse_string_list(data.get("strategy_guidance"))
            default_preferences = _parse_string_list(data.get("default_preferences"))
            analysis_hints = _parse_string_list(data.get("analysis_hints"))
            clarification_policy = _parse_dict_list(data.get("clarification_policy"))
            assumption_preferences = _parse_string_list(data.get("assumption_preferences"))
            workspace_queries = _parse_dict_list(data.get("workspace_queries"))
            soft_clarification_hints = _parse_dict_list(data.get("soft_clarification_hints"))
            try:
                max_chars = int(data.get("max_chars") or 0)
            except (ValueError, TypeError):
                max_chars = 0

    if not description:
        description = name.replace("_", " ").title()

    return SkillMeta(
        name=name,
        description=description,
        requires_toolkits=requires_toolkits,
        workspace_attention=workspace_attention,
        risk_awareness=risk_awareness,
        strategy_guidance=strategy_guidance,
        default_preferences=default_preferences,
        analysis_hints=analysis_hints,
        clarification_policy=clarification_policy,
        assumption_preferences=assumption_preferences,
        workspace_queries=workspace_queries,
        soft_clarification_hints=soft_clarification_hints,
        max_chars=max_chars,
        path=str(path),
    )


def _parse_frontmatter(frontmatter: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return {}
    return dict(data) if isinstance(data, dict) else {}


def _parse_string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = value.strip().strip("[").strip("]")
        items = value.split(",") if "," in value else [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return ()
    return tuple(str(item or "").strip().strip('"').strip("'") for item in items if str(item or "").strip())


def _parse_dict_list(value: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(make_json_safe(dict(item)))
        elif str(item or "").strip():
            result.append({"hint": str(item or "").strip()})
    return tuple(result)


class SkillRegistry:
    """Scans the resources/skills directory and indexes available GIS skills."""

    def __init__(self, skills_root: str | Path | None = None) -> None:
        self._root = Path(skills_root) if skills_root else _default_skills_root()
        self._skills: dict[str, SkillMeta] = {}
        self._refresh()

    def _refresh(self) -> None:
        self._skills.clear()
        if not self._root.exists() or not self._root.is_dir():
            return
        for path in sorted(self._root.glob("*.md")):
            meta = _parse_skill(path)
            if meta is not None:
                self._skills[meta.name] = meta

    @property
    def root(self) -> Path:
        return self._root

    def names(self) -> tuple[str, ...]:
        return tuple(self._skills.keys())

    def get(self, name: str) -> SkillMeta | None:
        return self._skills.get(str(name or "").strip())

    def catalog(self) -> list[dict[str, Any]]:
        return [meta.to_dict() for meta in self._skills.values()]

    def load_skill_description(self) -> str:
        """Build the load_skill tool description from discovered skills."""
        if not self._skills:
            return "Load a GIS best-practice skill document for reference. No skills are currently available."
        entries = [
            f"{meta.name} ({meta.description})"
            for meta in self._skills.values()
        ]
        return (
            "Load a GIS best-practice skill document for reference. "
            "Loading a skill injects its guidance into subsequent turns. "
            "Available skills: " + "; ".join(entries) + "."
        )

    def read_skill_content(self, name: str) -> str:
        """Read the full Markdown body of a skill (after frontmatter)."""
        meta = self.get(name)
        if meta is None:
            return ""
        try:
            text = Path(meta.path).read_text(encoding="utf-8")
        except OSError:
            return ""

        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                text = text[end + 3:].strip()

        if meta.max_chars and len(text) > meta.max_chars:
            text = text[: meta.max_chars] + "\n\n... (truncated)"

        return text

    def required_toolkits_for(self, name: str) -> tuple[str, ...]:
        meta = self.get(name)
        if meta is None:
            return ()
        return meta.requires_toolkits

    def suggest(
        self,
        user_request: str,
        *,
        limit: int = 3,
        context: SkillActivationContext | None = None,
    ) -> list[dict[str, Any]]:
        if context is None:
            return []

        scored: list[tuple[int, str, dict[str, Any]]] = []
        for meta in self._skills.values():
            activation = activation_for_skill(meta, context)
            if should_activate_skill(activation):
                scored.append((int(activation.get("score") or 0), meta.name, activation))
        scored.sort(key=lambda item: (-item[0], item[1]))

        result = []
        for _score, name, activation in scored[: max(1, int(limit or 3))]:
            meta = self.get(name)
            if meta is None:
                continue
            payload = meta.to_dict()
            payload["activation"] = activation
            result.append(payload)
        return result


def _dedupe_skill_names(names: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        text = str(name or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _default_skills_root() -> Path:
    path = Path(__file__).resolve()
    for parent in path.parents:
        candidate = parent / "resources" / "skills"
        if candidate.exists() and candidate.is_dir():
            return candidate.resolve()
    return (path.parents[4] / "resources" / "skills").resolve()


def default_skill_registry() -> SkillRegistry:
    return SkillRegistry()
