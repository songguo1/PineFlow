"""Decision policy for GIS risks."""

from __future__ import annotations

from collections.abc import Callable

from pineflow_agent.risks.models import GISRisk, RiskDecision


class RiskPolicy:
    """Small v1 policy layer that preserves current behavior while exposing intent."""

    def evaluate(self, risks: list[GISRisk] | tuple[GISRisk, ...]) -> RiskDecision:
        ordered = tuple(risks or ())
        if not ordered:
            return RiskDecision("proceed")
        disambiguation = _first_matching(ordered, lambda risk: bool(risk.suggested_choices))
        if disambiguation:
            return RiskDecision("ask_disambiguation", disambiguation, ordered)
        confirmation = _first_matching(ordered, lambda risk: bool(risk.confirmation_required))
        if confirmation:
            return RiskDecision("ask_confirmation", confirmation, ordered)
        auto_repair = _first_matching(
            ordered,
            lambda risk: bool(risk.auto_repair_available and risk.repair_action and not risk.confirmation_required),
        )
        if auto_repair:
            return RiskDecision("auto_repair", auto_repair, ordered)
        blocking = _first_matching(ordered, lambda risk: bool(risk.blocking or risk.severity == "error"))
        if blocking:
            return RiskDecision("ask_user", blocking, ordered)
        return RiskDecision("warn", ordered[0], ordered)


def _first_matching(risks: tuple[GISRisk, ...], predicate: Callable[[GISRisk], bool]) -> GISRisk | None:
    for risk in risks:
        if predicate(risk):
            return risk
    return None
