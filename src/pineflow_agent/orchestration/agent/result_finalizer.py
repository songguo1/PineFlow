"""AFTER_RUN result finalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pineflow_agent.core.models import AgentResult, ReActStep
from pineflow_agent.orchestration.agent.final_report import write_final_report
from pineflow_agent.orchestration.agent.goal_contract import attach_goal_contract
from pineflow_agent.orchestration.agent.report_audit import build_report_audit_dict
from pineflow_agent.orchestration.hooks.contexts import HookPoint, RunResultContext


@dataclass(frozen=True)
class ResultFinalizer:
    hooks: Any
    toolbox: Any

    def finalize(
        self,
        result: AgentResult,
        *,
        user_request: str,
        steps: list[ReActStep],
        session_id: str,
        session_memory_before: str,
        runtime_events: list[dict[str, Any]] | None = None,
        goal_contract: dict[str, Any] | None = None,
    ) -> AgentResult:
        ctx = RunResultContext(
            result=result,
            session_id=session_id,
            user_request=user_request,
            steps=steps,
            toolbox=self.toolbox,
            session_memory_before=session_memory_before,
        )
        finalized = self.hooks.emit(HookPoint.AFTER_RUN, ctx)
        final_result = getattr(finalized, "result", result) or result
        if goal_contract:
            final_result.goal_contract = dict(goal_contract)
        attach_goal_contract(final_result, user_request)
        final_result.report_audit = build_report_audit_dict(final_result, runtime_events=runtime_events)
        write_final_report(final_result, toolbox=self.toolbox, user_request=user_request, runtime_events=runtime_events)
        return final_result


@dataclass(frozen=True)
class BoundResultFinalizer:
    finalizer: ResultFinalizer
    user_request: str
    steps: list[ReActStep]
    session_id: str
    session_memory_before: str
    runtime_events: list[dict[str, Any]] | None = None
    goal_contract: dict[str, Any] | None = None

    def __call__(self, result: AgentResult) -> AgentResult:
        return self.finalizer.finalize(
            result,
            user_request=self.user_request,
            steps=self.steps,
            session_id=self.session_id,
            session_memory_before=self.session_memory_before,
            runtime_events=self.runtime_events,
            goal_contract=self.goal_contract,
        )
