"""FastAPI entrypoint for the independent PineFlow service."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from pineflow_api import __version__
from pineflow_api.contracts.models import QGISAgentRequest
from pineflow_api.contracts.run_control import RunControlAction
from pineflow_api.application.run_commands import RunCommandError, RunNotFoundError
from pineflow_api.application.service import QGISAgentRunner
from pineflow_api.config import DEFAULT_QGIS_LAUNCHER, DEFAULT_QGIS_PREFIX_PATH


def create_app(*, runner: QGISAgentRunner | None = None) -> FastAPI:
    app = FastAPI(title="PineFlow API", version=__version__)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5174",
            "http://127.0.0.1:5174",
            "http://localhost:4174",
            "http://127.0.0.1:4174",
            "tauri://localhost",
            "https://tauri.localhost",
        ],
        allow_origin_regex=r"^(http://localhost:\d+|http://127\.0\.0\.1:\d+|tauri://localhost|https://tauri\.localhost)$",
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    active_runner = runner or QGISAgentRunner()

    @app.get("/qgis/health")
    def health(
        deep: bool = Query(default=False),
        launcher: str = Query(default=DEFAULT_QGIS_LAUNCHER),
        prefix_path: str = Query(default=DEFAULT_QGIS_PREFIX_PATH),
    ) -> dict[str, Any]:
        qgis = {"launcher": launcher, "prefix_path": prefix_path}
        return active_runner.health(qgis=qgis, deep=deep)

    @app.post("/qgis/runs")
    def create_run(request: QGISAgentRequest) -> dict[str, Any]:
        return active_runner.create_background_run(request)

    @app.post("/qgis/runs/{run_id}/resume")
    def resume_run(run_id: str, request: QGISAgentRequest) -> dict[str, Any]:
        try:
            return active_runner.resume_background_run(run_id, request)
        except RunNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Run does not exist.")
        except RunCommandError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.post("/qgis/runs/{run_id}/actions")
    def apply_run_action(run_id: str, action: RunControlAction) -> dict[str, Any]:
        try:
            return active_runner.apply_run_action(run_id, action)
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="Run does not exist.")
        except (RunCommandError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get("/qgis/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        session = active_runner.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="QGIS agent session does not exist.")
        return session

    @app.get("/qgis/sessions/{session_id}/runs")
    def list_session_runs(session_id: str) -> dict[str, Any]:
        session = active_runner.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="QGIS agent session does not exist.")
        runs = active_runner.list_session_runs(session_id)
        return {"session_id": session_id, "runs": runs, "total": len(runs)}

    @app.get("/qgis/sessions/{session_id}/events")
    def list_session_events(
        session_id: str,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> dict[str, Any]:
        session = active_runner.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="QGIS agent session does not exist.")
        events = active_runner.list_session_events(session_id, after_seq=after_seq, limit=limit)
        next_seq = int(events[-1]["seq"]) if events else int(after_seq or 0)
        return {"session_id": session_id, "events": events, "total": len(events), "next_seq": next_seq}

    @app.get("/qgis/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = active_runner.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run does not exist.")
        return run

    @app.get("/qgis/runs/{run_id}/events")
    def list_run_events(
        run_id: str,
        after_seq: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> dict[str, Any]:
        run = active_runner.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run does not exist.")
        events = active_runner.list_run_events(run_id, after_seq=after_seq, limit=limit)
        next_seq = int(events[-1]["seq"]) if events else int(after_seq or 0)
        return {
            "run_id": run_id,
            "session_id": run.get("session_id") or "",
            "events": events,
            "total": len(events),
            "next_seq": next_seq,
        }

    @app.get("/qgis/runs/{run_id}/snapshot")
    def get_run_snapshot(run_id: str) -> dict[str, Any]:
        run = active_runner.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run does not exist.")
        snapshot = active_runner.get_run_snapshot(run_id)
        if not snapshot:
            raise HTTPException(status_code=404, detail="Run snapshot does not exist.")
        return snapshot

    @app.post("/qgis/runs/{run_id}/pause")
    def pause_run(run_id: str) -> dict[str, Any]:
        try:
            run = active_runner.request_run_pause(run_id)
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="No active run found.")
        return {"run_id": run_id, "session_id": run.get("session_id") or "", "ok": True, "run": run, "next_run_id": run_id}

    @app.post("/qgis/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        try:
            run = active_runner.request_run_cancel(run_id)
        except RunNotFoundError:
            raise HTTPException(status_code=404, detail="No active run found.")
        return {"run_id": run_id, "session_id": run.get("session_id") or "", "ok": True, "run": run, "next_run_id": run_id}

    @app.get("/qgis/sessions")
    def list_sessions() -> dict[str, Any]:
        sessions = active_runner.list_sessions()
        return {"sessions": sessions, "total": len(sessions)}

    @app.get("/qgis/sessions/{session_id}/memory")
    def get_session_memory(session_id: str) -> dict[str, Any]:
        session = active_runner.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="QGIS agent session does not exist.")
        content = active_runner.get_session_memory(session_id)
        return {"session_id": session_id, "content": content}

    @app.put("/qgis/sessions/{session_id}/memory")
    async def save_session_memory(session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        session = active_runner.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="QGIS agent session does not exist.")
        content = str(body.get("content") or "")
        active_runner.save_session_memory(session_id, content)
        return {"session_id": session_id, "ok": True}

    @app.get("/qgis/sessions/{session_id}/reports/{artifact_id}")
    def get_session_report(session_id: str, artifact_id: str) -> dict[str, Any]:
        session = active_runner.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="QGIS agent session does not exist.")
        report = active_runner.get_session_report(session_id, artifact_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report does not exist.")
        return {"session_id": session_id, "report": report}

    @app.post("/qgis/sessions/{session_id}/archive")
    def archive_session(session_id: str) -> dict[str, Any]:
        ok = active_runner.archive_session(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found or already archived.")
        return {"session_id": session_id, "ok": True}

    @app.delete("/qgis/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        ok = active_runner.delete_session(session_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Session not found or already deleted.")
        return {"session_id": session_id, "ok": True, "mode": "trash"}

    @app.get("/qgis/outputs")
    def list_recent_outputs() -> dict[str, Any]:
        return {"outputs": active_runner.list_recent_outputs()}

    @app.get("/qgis/toolbox/search")
    def search_toolbox(
        q: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=200),
        launcher: str = Query(default=DEFAULT_QGIS_LAUNCHER),
        prefix_path: str = Query(default=DEFAULT_QGIS_PREFIX_PATH),
    ) -> dict[str, Any]:
        return active_runner.search_toolbox(
            query=q,
            limit=limit,
            qgis={"launcher": launcher, "prefix_path": prefix_path},
        )

    @app.get("/qgis/toolbox/help/{algorithm_id}")
    def algorithm_help(
        algorithm_id: str,
        launcher: str = Query(default=DEFAULT_QGIS_LAUNCHER),
        prefix_path: str = Query(default=DEFAULT_QGIS_PREFIX_PATH),
    ) -> dict[str, Any]:
        return active_runner.algorithm_help(
            algorithm_id,
            qgis={"launcher": launcher, "prefix_path": prefix_path},
        )

    return app


app = create_app()
