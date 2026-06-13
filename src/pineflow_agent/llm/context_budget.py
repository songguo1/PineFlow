"""Unified token budget controller for ReAct prompt assembly.

Provides a single point of control for how many tokens each section of the
prompt payload (state_tree, previous_steps, loaded_skills, session_memory)
is allowed to consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any


@dataclass
class ContextBudget:
    max_tokens: int = 6000
    allocations: dict[str, int] = field(default_factory=lambda: {
        "state_tree": 800,
        "previous_steps": 1500,
        "loaded_skills": 1000,
        "session_memory": 600,
        "artifacts": 400,
        "outputs": 300,
        "user_request": 500,
        "decision_rules": 600,
        "canonicalization_rules": 300,
        "tool_disclosure": 500,
    })

    def allocate(self, section: str, content: str) -> str:
        """Trim *content* so its estimated token count does not exceed the *section* budget."""
        limit = self.allocations.get(section)
        if limit is None or limit <= 0:
            return content
        return _trim_to_token_estimate(content, limit)

    def remaining(self, *, used_sections: dict[str, int] | None = None) -> int:
        """Return remaining token budget after subtracting *used_sections*."""
        used = sum(used_sections.values()) if used_sections else 0
        return max(0, self.max_tokens - used)

    def section_limit(self, section: str) -> int:
        return self.allocations.get(section, 0)


def _trim_to_token_estimate(text: str, max_tokens: int) -> str:
    """Rough token estimator: ~4 chars per token for CJK, ~3.5 for ASCII."""
    if not text:
        return text
    chars_per_token = 3.5
    max_chars = int(max_tokens * chars_per_token)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n... (truncated)"


def estimate_tokens(text: str) -> int:
    """Rough token count estimate. Not exact but directionally correct for budget decisions."""
    if not text:
        return 0
    cjk_chars = sum(1 for ch in text if '一' <= ch <= '鿿' or '　' <= ch <= '〿')
    ascii_chars = len(text) - cjk_chars
    return int(cjk_chars / 1.5 + ascii_chars / 4.0)


def build_context_budget_report(payload: dict[str, Any], *, max_tokens: int = 6000) -> dict[str, Any]:
    """Return a rough token report for prompt payload sections."""
    sections: dict[str, dict[str, int]] = {}
    total = 0
    for key, value in dict(payload or {}).items():
        text = _section_text(value)
        tokens = estimate_tokens(text)
        total += tokens
        sections[str(key)] = {
            "estimated_tokens": tokens,
            "chars": len(text),
        }
    return {
        "estimated_total_tokens": total,
        "max_tokens": max_tokens,
        "remaining_tokens": max(0, max_tokens - total),
        "over_budget": total > max_tokens,
        "sections": sections,
    }


def _section_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    except Exception:
        return str(value)


def budget_for_prompt(
    *,
    user_request: str = "",
    state: dict[str, Any] | None = None,
    previous_steps: list[dict[str, Any]] | None = None,
    loaded_skills: list[dict[str, Any]] | None = None,
    session_memory: str = "",
    artifacts: list[dict[str, Any]] | None = None,
    outputs: list[dict[str, Any]] | None = None,
) -> ContextBudget:
    """Convenience factory: creates a budget and trims all provided sections inline."""
    budget = ContextBudget()

    def _json_len(obj: Any) -> int:
        try:
            return len(json.dumps(obj, ensure_ascii=False, default=str))
        except Exception:
            return len(str(obj))

    if state:
        state_str = json.dumps(state, ensure_ascii=False, default=str)
        if len(state_str) > budget.section_limit("state_tree") * 4:
            state = {"_trimmed": True, "layers": state.get("layers", [])}
    if previous_steps:
        limit = budget.section_limit("previous_steps")
        total = 0
        trimmed: list[dict[str, Any]] = []
        for step in reversed(list(previous_steps)):
            s = _json_len(step)
            if total + s > limit * 4:
                break
            trimmed.insert(0, step)
            total += s
        previous_steps = trimmed

    return budget
