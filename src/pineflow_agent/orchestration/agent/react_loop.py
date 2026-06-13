"""ReAct loop that reasons, calls QGIS toolbox actions, and feeds back observations."""

from __future__ import annotations

from typing import Any, Callable

from pineflow_agent.orchestration.execution.duplicate_action_guard import DuplicateActionGuard
from pineflow_agent.orchestration.event_stream import EventHandler, emit_event
from pineflow_agent.orchestration.execution.final_answer_runtime import FinalAnswerRuntime
from pineflow_agent.orchestration.hooks.contexts import HookPoint, ToolContext
from pineflow_agent.orchestration.hooks.pipeline import get_pipeline
from pineflow_agent.orchestration.meta.meta_action_runtime import MetaActionRuntime
from pineflow_agent.orchestration.meta.meta_tool_dispatcher import MetaToolDispatcher
from pineflow_agent.orchestration.execution.execution_step import execute_action_step
from pineflow_agent.orchestration.execution.pause_runtime import PauseRuntime
from pineflow_agent.orchestration.agent.prompt_assembler import PromptAssembler
from pineflow_agent.orchestration.agent.result_finalizer import BoundResultFinalizer, ResultFinalizer
from pineflow_agent.orchestration.agent.run_lifecycle import RunLifecycle
from pineflow_agent.orchestration.agent.ux_narrator import UXNarrator
from pineflow_agent.orchestration.execution.runtime_error_runtime import RuntimeErrorRuntime
from pineflow_agent.orchestration.execution.toolbox_access import toolbox_artifacts, toolbox_artifact_index
from pineflow_agent.orchestration.resume.validation_gate import ValidationGate, pending_task_from_issue
from pineflow_agent.llm.llm import LLMClient
from pineflow_agent.core.models import ActionPlan, AgentResult, Observation, ReActStep, react_steps_from_payload
from pineflow_agent.orchestration.execution.execution_memory import ExecutionMemory
from pineflow_agent.orchestration.agent.result_builder import (
    action_selection_error_result,
    failed_result,
)
from pineflow_agent.orchestration.resume.resume_controller import ResumeController
from pineflow_agent.rules.rules_gateway import RulesGateway
from pineflow_agent.core.state_tree import GISStateTree
from pineflow_agent.tools.qgis.toolbox import QGISToolbox
from pineflow_agent.tools.registry.tool_registry import ToolRegistry
from pineflow_agent.tools.registry.toolkits import ToolDisclosureController, ToolDisclosureOptions, ToolKitRegistry
from pineflow_agent.rules.validation import ValidationIssue


class ReActGISAgent:
    """Standalone GIS agent with Thought -> Action -> Observation iterations."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        toolbox: QGISToolbox | None = None,
        tool_registry: ToolRegistry | None = None,
        auto_repair: bool = True,
        tool_profile: str = "vector_raster_basic",
        tool_allow: list[str] | tuple[str, ...] | None = None,
        tool_deny: list[str] | tuple[str, ...] | None = None,
        should_pause: Callable[[str], bool] | None = None,
        should_cancel: Callable[[str], bool] | None = None,
    ) -> None:
        self.llm = llm
        self.toolbox = toolbox or QGISToolbox()
        self.tool_registry = tool_registry or ToolRegistry.default()
        self.auto_repair = bool(auto_repair)
        self.tool_disclosure_options = ToolDisclosureOptions(
            profile=str(tool_profile or "vector_raster_basic").strip() or "vector_raster_basic",
            allow=_normalize_tool_name_list(tool_allow),
            deny=_normalize_tool_name_list(tool_deny),
        )
        self.toolkit_registry = ToolKitRegistry()
        self.should_pause = should_pause
        self.should_cancel = should_cancel
        self.tool_disclosure = ToolDisclosureController(
            options=self.tool_disclosure_options,
            toolkit_registry=self.toolkit_registry,
        )
        self.rules_gateway = RulesGateway()
        self.hooks = get_pipeline()
        self.meta = MetaToolDispatcher()
        self._source_run_id = ""

    @property
    def state(self) -> GISStateTree:
        return self.toolbox.state

    def _resume_controller(self) -> ResumeController:
        return ResumeController(
            llm=self.llm,
            toolbox=self.toolbox,
            state=self.state,
            emit=self._emit,
            execute_action_step=self._execute_action_step,
            run_request=self.run,
            pending_task_from_issue=pending_task_from_issue,
            rules_gateway=self.rules_gateway,
            tool_registry=self.tool_registry,
            hooks=self.hooks,
        )

    def _restore_from_prior_steps(self, steps: list[ReActStep]) -> None:
        """Restore agent state from prior steps on session resume."""
        self.tool_disclosure = ToolDisclosureController.from_steps(
            steps,
            options=self.tool_disclosure_options,
            toolkit_registry=self.toolkit_registry,
        )
        self._sync_meta_dispatcher()

    def _try_pause(
        self,
        steps: list[ReActStep],
        *,
        session_id: str,
        on_event: EventHandler | None,
    ) -> AgentResult | None:
        return PauseRuntime(
            state_tree=self.state.to_dict(),
            steps=steps,
            session_id=session_id,
            on_event=on_event,
            should_pause=self.should_pause,
            should_cancel=self.should_cancel,
        ).try_pause()

    def run(
        self,
        user_request: str,
        *,
        session_id: str = "",
        on_event: EventHandler | None = None,
        prior_steps: list[ReActStep] | list[dict[str, Any]] | None = None,
        goal_contract: dict[str, Any] | None = None,
        run_context: dict[str, Any] | None = None,
    ) -> AgentResult:
        runtime_events: list[dict[str, Any]] = []
        on_event = _capturing_event_sink(on_event, runtime_events)
        run_context_payload = dict(run_context or {})
        self._source_run_id = str(run_context_payload.get("source_run_id") or "")
        steps: list[ReActStep] = react_steps_from_payload(prior_steps)
        if steps:
            self._restore_from_prior_steps(steps)
        run_state = RunLifecycle(
            hooks=self.hooks,
            toolbox=self.toolbox,
            state_tree=self.state.to_dict(),
        ).start(
            user_request,
            session_id=session_id,
            on_event=on_event,
            steps=steps,
        )
        step_total = run_state.step_total
        memory = run_state.memory
        session_memory = run_state.session_memory
        finish = self._bound_result_finalizer(
            user_request=user_request,
            steps=steps,
            session_id=session_id,
            session_memory_before=session_memory,
            runtime_events=runtime_events,
            goal_contract=goal_contract,
        )

        while True:
            index = len(steps) + 1
            try:
                plan = self._next_action(
                    user_request,
                    steps,
                    session_id=session_id,
                    on_event=on_event,
                    session_memory=session_memory,
                    run_context=run_context_payload,
                )
            except Exception as exc:
                error_text = str(exc)
                result = self._result_for_action_selection_error(
                    error_text,
                    steps,
                    session_id=session_id,
                )
                error_code = "tool_unavailable" if "tool_unavailable" in error_text else "model_adapter_error"
                self._emit(
                    on_event,
                    "failed",
                    result.final_message,
                    session_id=session_id,
                    code=error_code,
                    error=error_text,
                    result=result.to_dict(),
                )
                return finish(result)
            self._emit(
                on_event,
                "thought",
                plan.thought or "Planned the next GIS tool action.",
                session_id=session_id,
                step_index=index,
                thought=plan.thought,
            )
            meta_decision = self._meta_action_runtime(steps, session_id=session_id, on_event=on_event, step_total=step_total).handle(plan, index=index)
            if meta_decision is not None:
                if meta_decision.status == "continue":
                    paused = self._try_pause(steps, session_id=session_id, on_event=on_event)
                    if paused is not None:
                        return finish(paused)
                    continue
                if meta_decision.status == "terminal" and meta_decision.result is not None:
                    return finish(meta_decision.result)
            if meta_decision is None and self._duplicate_action_guard(memory, steps, session_id=session_id, on_event=on_event, step_total=step_total).handle(plan, index=index):
                paused = self._try_pause(steps, session_id=session_id, on_event=on_event)
                if paused is not None:
                    return finish(paused)
                continue
            validation_issues: list[ValidationIssue] = []
            preflight_warnings: list[ValidationIssue] = []
            if meta_decision is None:
                validation_issues, preflight_warnings = self._before_tool_call_issues(plan)
            if validation_issues:
                pause = ValidationGate(
                    state_tree=self.state.to_dict(),
                    steps=steps,
                    session_id=session_id,
                    user_request=user_request,
                    ux_explainer=self._ux_narrator().explain_validation_pause,
                ).pause_for_issues(
                    plan=plan,
                    issues=validation_issues,
                    step_index=index,
                    step_total=step_total,
                )
                if pause is not None:
                    self._emit(on_event, pause.event, pause.message, **pause.payload)
                    return pause.result
            if plan.action == "final_answer":
                result = self._final_answer_runtime(
                    steps,
                    session_id=session_id,
                    on_event=on_event,
                    user_request=user_request,
                ).complete_from_plan(plan)
                if result.status == "quality_blocked":
                    paused = self._try_pause(steps, session_id=session_id, on_event=on_event)
                    if paused is not None:
                        return finish(paused)
                    continue
                return finish(result)

            observation = self._execute_action_step(
                plan,
                index=index,
                step_total=step_total,
                steps=steps,
                on_event=on_event,
                session_id=session_id,
                preflight_warnings=preflight_warnings,
            )
            if observation.is_success:
                memory.remember_step(steps[-1])

            if not observation.is_success:
                error_decision = RuntimeErrorRuntime(
                    state=self.state,
                    steps=steps,
                    session_id=session_id,
                    user_request=user_request,
                    on_event=on_event,
                    step_total=step_total,
                    auto_repair=self.auto_repair,
                ).handle(plan, observation)
                if error_decision.status == "continue":
                    paused = self._try_pause(steps, session_id=session_id, on_event=on_event)
                    if paused is not None:
                        return finish(paused)
                    continue
                result = error_decision.result
                if result is None:
                    result = failed_result(
                        observation.message,
                        steps=steps,
                        state_tree=self.state.to_dict(),
                        session_id=session_id,
                    )
                return finish(result)

            paused = self._try_pause(steps, session_id=session_id, on_event=on_event)
            if paused is not None:
                return finish(paused)

    def run_confirmed_repair(
        self,
        *,
        pending_task: dict[str, Any],
        repair: dict[str, Any],
        session_id: str = "",
        on_event: EventHandler | None = None,
        prior_steps: list[ReActStep] | list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        return self._resume_controller().run_confirmed_repair(
            pending_task=pending_task,
            repair=repair,
            session_id=session_id,
            on_event=on_event,
            prior_steps=prior_steps,
        )

    def resume_pending_task(
        self,
        *,
        action: str,
        pending_task: dict[str, Any],
        repair: dict[str, Any] | None = None,
        slot_patch: dict[str, Any] | None = None,
        user_reply: str = "",
        user_request: str = "",
        session_id: str = "",
        on_event: EventHandler | None = None,
        prior_steps: list[ReActStep] | list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        return self._resume_controller().resume_pending_task(
            action=action,
            pending_task=pending_task,
            repair=repair,
            slot_patch=slot_patch,
            user_reply=user_reply,
            user_request=user_request,
            session_id=session_id,
            on_event=on_event,
            prior_steps=prior_steps,
        )

    def resume_with_slot_patch(
        self,
        *,
        pending_task: dict[str, Any],
        slot_patch: dict[str, Any],
        session_id: str = "",
        on_event: EventHandler | None = None,
        prior_steps: list[ReActStep] | list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        return self._resume_controller().resume_with_slot_patch(
            pending_task=pending_task,
            slot_patch=slot_patch,
            session_id=session_id,
            on_event=on_event,
            prior_steps=prior_steps,
        )

    def resume_with_replanned_request(
        self,
        *,
        user_request: str,
        pending_task: dict[str, Any],
        session_id: str = "",
        on_event: EventHandler | None = None,
        prior_steps: list[ReActStep] | list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        return self._resume_controller().resume_with_replanned_request(
            user_request=user_request,
            pending_task=pending_task,
            session_id=session_id,
            on_event=on_event,
            prior_steps=prior_steps,
        )

    def reject_pending_repair(
        self,
        *,
        pending_task: dict[str, Any],
        repair: dict[str, Any] | None = None,
        session_id: str = "",
        on_event: EventHandler | None = None,
    ) -> AgentResult:
        return self._resume_controller().reject_pending_repair(
            pending_task=pending_task,
            repair=repair,
            session_id=session_id,
            on_event=on_event,
        )

    def cancel_pending_task(
        self,
        *,
        pending_task: dict[str, Any],
        session_id: str = "",
        on_event: EventHandler | None = None,
    ) -> AgentResult:
        return self._resume_controller().cancel_pending_task(
            pending_task=pending_task,
            session_id=session_id,
            on_event=on_event,
        )

    def resume_with_user_reply(
        self,
        *,
        user_reply: str,
        pending_task: dict[str, Any],
        session_id: str = "",
        on_event: EventHandler | None = None,
        prior_steps: list[ReActStep] | list[dict[str, Any]] | None = None,
    ) -> AgentResult:
        return self._resume_controller().resume_with_user_reply(
            user_reply=user_reply,
            pending_task=pending_task,
            session_id=session_id,
            on_event=on_event,
            prior_steps=prior_steps,
        )

    def _result_for_action_selection_error(
        self,
        error_message: str,
        steps: list[ReActStep],
        *,
        session_id: str,
    ) -> AgentResult:
        return action_selection_error_result(
            error_message,
            steps=steps,
            state_tree=self.state.to_dict(),
            session_id=session_id,
        )

    def _next_action(
        self,
        user_request: str,
        steps: list[ReActStep],
        *,
        session_id: str = "",
        on_event: EventHandler | None = None,
        session_memory: str = "",
        run_context: dict[str, Any] | None = None,
    ) -> ActionPlan:
        try:
            artifacts = toolbox_artifacts(self.toolbox)
            plan, self.tool_disclosure = PromptAssembler(
                llm=self.llm,
                hooks=self.hooks,
                tool_registry=self.tool_registry,
                toolkit_registry=self.toolkit_registry,
                tool_disclosure_options=self.tool_disclosure_options,
            ).next_action(
                user_request=user_request,
                state=self.state.to_dict(),
                steps=steps,
                session_memory=session_memory,
                artifacts=artifacts,
                run_context=dict(run_context or {}),
            )
            self.meta.user_request = user_request
            self._sync_meta_dispatcher()
            return plan
        except Exception as exc:
            del on_event, session_id
            raise RuntimeError(f"Native tool action selection failed: {exc}") from exc

    def _sync_meta_dispatcher(self) -> None:
        self.meta.tool_disclosure = self.tool_disclosure
        self.meta.toolbox = self.toolbox
        self.meta.tool_registry = self.tool_registry
        self.meta.state = self.state

    def _before_tool_call_issues(self, plan: ActionPlan) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
        tool_ctx = ToolContext(
            plan=plan,
            state=self.state,
            tool_registry=self.tool_registry,
            rules_gateway=self.rules_gateway,
        )
        tool_ctx = self.hooks.emit(HookPoint.BEFORE_TOOL_CALL, tool_ctx)
        return list(tool_ctx.all_validation_issues()), list(tool_ctx.preflight_warnings or [])

    def _execute_action(self, plan: ActionPlan) -> Observation:
        if self.meta.is_meta(plan.action):
            return self.meta.execute(plan.action, plan.action_input)
        return self.tool_registry.execute(plan, self.toolbox)

    def _execute_action_step(
        self,
        plan: ActionPlan,
        *,
        index: int,
        step_total: int,
        steps: list[ReActStep],
        on_event: EventHandler | None,
        session_id: str,
        attempt_no: int = 0,
        preflight_warnings: list[ValidationIssue] | None = None,
    ) -> Observation:
        self.meta.steps = steps
        self.meta.session_id = session_id
        return execute_action_step(
            plan,
            index=index,
            step_total=step_total,
            steps=steps,
            on_event=on_event,
            session_id=session_id,
            state=self.state,
            execute_action=self._execute_action,
            artifact_index=toolbox_artifact_index(self.toolbox),
            source_run_id=self._source_run_id,
            attempt_no=attempt_no,
            preflight_warnings=preflight_warnings,
            observation_narrator=self._ux_narrator().summarize_observation,
            key_event_narrator=self._ux_narrator().narrate_key_event,
        )

    def _duplicate_action_guard(
        self,
        memory: ExecutionMemory,
        steps: list[ReActStep],
        *,
        session_id: str,
        on_event: EventHandler | None,
        step_total: int,
    ) -> DuplicateActionGuard:
        return DuplicateActionGuard(
            memory=memory,
            state=self.state,
            steps=steps,
            session_id=session_id,
            on_event=on_event,
            step_total=step_total,
        )

    def _meta_action_runtime(
        self,
        steps: list[ReActStep],
        *,
        session_id: str,
        on_event: EventHandler | None,
        step_total: int,
    ) -> MetaActionRuntime:
        return MetaActionRuntime(
            meta=self.meta,
            tool_disclosure=self.tool_disclosure,
            tool_registry=self.tool_registry,
            state_tree=self.state.to_dict(),
            steps=steps,
            session_id=session_id,
            on_event=on_event,
            step_total=step_total,
            execute_action_step=self._execute_action_step,
        )

    def _final_answer_runtime(
        self,
        steps: list[ReActStep],
        *,
        session_id: str,
        on_event: EventHandler | None,
        user_request: str = "",
    ) -> FinalAnswerRuntime:
        return FinalAnswerRuntime(
            state_tree=self.state.to_dict(),
            steps=steps,
            session_id=session_id,
            on_event=on_event,
            user_request=user_request,
        )

    def _bound_result_finalizer(
        self,
        *,
        user_request: str,
        steps: list[ReActStep],
        session_id: str,
        session_memory_before: str,
        runtime_events: list[dict[str, Any]] | None = None,
        goal_contract: dict[str, Any] | None = None,
    ) -> BoundResultFinalizer:
        return BoundResultFinalizer(
            finalizer=ResultFinalizer(hooks=self.hooks, toolbox=self.toolbox),
            user_request=user_request,
            steps=steps,
            session_id=session_id,
            session_memory_before=session_memory_before,
            runtime_events=runtime_events,
            goal_contract=goal_contract,
        )

    def _ux_narrator(self) -> UXNarrator:
        return UXNarrator(self.llm)

    @staticmethod
    def _emit(
        on_event: EventHandler | None,
        event: str,
        message: str,
        **payload: Any,
    ) -> None:
        emit_event(on_event, event, message, **payload)


def _normalize_tool_name_list(value: list[str] | tuple[str, ...] | str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items = [value]
    else:
        raw_items = list(value)
    return tuple(str(item or "").strip() for item in raw_items if str(item or "").strip())

def _capturing_event_sink(
    on_event: EventHandler | None,
    runtime_events: list[dict[str, Any]],
) -> EventHandler:
    def capture(event: dict[str, Any]) -> None:
        if isinstance(event, dict):
            runtime_events.append(dict(event))
        if on_event is not None:
            on_event(event)

    return capture
