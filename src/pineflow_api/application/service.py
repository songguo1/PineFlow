"""Runtime orchestration for PineFlow API requests."""

from __future__ import annotations

from typing import Any, Callable

from pineflow_agent.tools.qgis.toolbox import QGISToolbox

from pineflow_api.application.agent_runtime_factory import AgentRuntimeFactoryService
from pineflow_api.application.qgis_runtime_info import QGISRuntimeInfoService
from pineflow_api.application.run_commands import RunCommandService
from pineflow_api.application.run_runtime import RunManager
from pineflow_api.application.run_service import RunService
from pineflow_api.application.turn_execution import TurnExecutionService
from pineflow_api.application.turn_intents import TurnIntentService
from pineflow_api.contracts.models import QGISAgentRequest
from pineflow_api.contracts.run_control import RunControlAction, control_action_from_resume_request
from pineflow_api.persistence.sessions import SESSION_STORE, SessionStore
from pineflow_api.routing.turn_routing import SessionRouter

EventSink = Callable[[dict[str, Any]], None]


class QGISAgentRunner:
    """Run PineFlow GIS tasks """

    def __init__(self, *, sessions: SessionStore | None = None) -> None:
        self.sessions = sessions or SESSION_STORE
        self.router = SessionRouter()
        self.runs = RunService(self.sessions)
        self.run_manager = RunManager(self.runs)
        self.intent_service = TurnIntentService()
        self.qgis_info = QGISRuntimeInfoService()
        self.agent_runtime = AgentRuntimeFactoryService(run_manager=self.run_manager)
        self.turn_execution = TurnExecutionService(
            sessions=self.sessions,
            runs=self.runs,
            runtime_factory_for_request=self.agent_runtime.runtime_factory_for_request,
            toolbox_factory=QGISToolbox,
            agent_factory=self.agent_runtime.build_agent,
            apply_qgis_environment=self.agent_runtime.apply_qgis_environment,
            run_snapshot_loader=self.sessions.get_run_snapshot,
        )
        self.run_commands = RunCommandService(
            router=self.router,
            runs=self.runs,
            run_manager=self.run_manager,
            resolve_intent=self.intent_service.resolve,
            execute_prepared=self.turn_execution.run_prepared,
            handle_error=self.turn_execution.handle_background_error,
            bootstrap_prepared=self.turn_execution.bootstrap_prepared_run,
        )

    def health(self, *, qgis: dict[str, Any] | None = None, deep: bool = False) -> dict[str, Any]:
        return self.qgis_info.health(qgis=qgis, deep=deep)

    def run(self, request: QGISAgentRequest, *, on_event: EventSink | None = None) -> dict[str, Any]:
        prepared = self.run_commands.prepare(request)
        return self.turn_execution.run_prepared(
            prepared.route,
            prepared.request,
            prepared.run,
            prepared.intent,
            on_event=on_event,
        )

    def create_background_run(self, request: QGISAgentRequest) -> dict[str, Any]:
        return self.run_commands.create_run(request)

    def resume_background_run(self, run_id: str, request: QGISAgentRequest) -> dict[str, Any]:
        return self.run_commands.apply_control_action(run_id, control_action_from_resume_request(run_id, request))

    def apply_run_action(self, run_id: str, action: RunControlAction) -> dict[str, Any]:
        return self.run_commands.apply_control_action(run_id, action)

    def search_toolbox(self, *, query: str = "", limit: int = 50, qgis: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.qgis_info.search_toolbox(query=query, limit=limit, qgis=qgis)

    def algorithm_help(self, algorithm_id: str, *, qgis: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.qgis_info.algorithm_help(algorithm_id, qgis=qgis)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.sessions.list_sessions()

    def list_session_runs(self, session_id: str) -> list[dict[str, Any]]:
        return self.sessions.list_runs(session_id)

    def list_session_events(self, session_id: str, *, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return self.sessions.list_events(session_id, after_seq=after_seq, limit=limit)

    def get_run(self, run_id: str) -> dict[str, Any]:
        return self.runs.get(run_id)

    def list_run_events(self, run_id: str, *, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        return self.runs.list_events(run_id, after_seq=after_seq, limit=limit)

    def get_run_snapshot(self, run_id: str) -> dict[str, Any]:
        return self.sessions.get_run_snapshot(run_id)

    def request_run_pause(self, run_id: str) -> dict[str, Any]:
        result = self.run_commands.apply_control_action(run_id, RunControlAction(action_type="run.pause", run_id=run_id))
        return result.get("run") or result

    def request_run_cancel(self, run_id: str) -> dict[str, Any]:
        result = self.run_commands.apply_control_action(run_id, RunControlAction(action_type="run.cancel", run_id=run_id))
        return result.get("run") or result

    def archive_session(self, session_id: str) -> bool:
        return self.sessions.archive_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        return self.sessions.delete_session(session_id)

    def get_session_memory(self, session_id: str) -> str:
        return self.sessions.get_session_memory(session_id)

    def save_session_memory(self, session_id: str, content: str) -> None:
        self.sessions.save_session_memory(session_id, content)

    def get_session_report(self, session_id: str, artifact_id: str) -> dict[str, Any]:
        return self.sessions.get_report_artifact(session_id, artifact_id)

    def list_recent_outputs(self) -> list[dict[str, Any]]:
        return self.sessions.list_recent_outputs()


