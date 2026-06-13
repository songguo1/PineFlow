"""Application-level intent resolution before GIS run execution."""

from __future__ import annotations

from typing import Any, Callable

from pineflow_api.application.execution import build_llm
from pineflow_api.contracts.models import QGISAgentRequest
from pineflow_api.routing.command_router import CommandRouter
from pineflow_api.routing.triage_router import TriageRouter
from pineflow_api.routing.turn_intent import TurnIntent
from pineflow_api.routing.turn_routing import TurnRoute


class TurnIntentService:
    """Resolves slash commands and LLM triage into non-execution intents."""

    def __init__(
        self,
        *,
        command_router: CommandRouter | None = None,
        triage_router: TriageRouter | None = None,
        llm_factory: Callable[[QGISAgentRequest], Any] = build_llm,
    ) -> None:
        self.command_router = command_router or CommandRouter()
        self.triage_router = triage_router or TriageRouter()
        self.llm_factory = llm_factory

    def resolve(self, route: TurnRoute, request: QGISAgentRequest) -> TurnIntent | None:
        if route.kind not in {"new_session", "continue_session"}:
            return None
        if request.options.reset_session:
            return None
        command = self.command_router.match(request.message)
        if command is not None:
            intent = command.intent
        else:
            intent = self.triage_router.classify(
                request.message,
                route.session_state,
                llm=self._build_triage_llm(request),
            )
            if intent.kind == "session_control":
                intent = TurnIntent(
                    "gis_execute",
                    reason=f"structured_session_control_required:{intent.reason}",
                    confidence=intent.confidence,
                )
        if intent.kind == "gis_execute":
            return None
        return intent

    def _build_triage_llm(self, request: QGISAgentRequest) -> Any:
        try:
            return self.llm_factory(request)
        except Exception:
            return None
