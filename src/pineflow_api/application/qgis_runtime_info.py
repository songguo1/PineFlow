"""QGIS runtime inspection helpers for health and toolbox metadata endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pineflow_agent.core.json_safety import make_json_safe
from pineflow_runtime.runtime import QGISRuntime

from pineflow_api.application.qgis_runtime_proxy import SubprocessQGISRuntime


class QGISRuntimeInfoService:
    """Reads QGIS availability and algorithm metadata without owning run execution."""

    def health(self, *, qgis: dict[str, Any] | None = None, deep: bool = False) -> dict[str, Any]:
        qgis_config = dict(qgis or {})
        launcher = str(qgis_config.get("launcher") or "").strip()
        prefix_path = str(qgis_config.get("prefix_path") or "").strip()
        report: dict[str, Any] = {
            "status": "ok",
            "launcher": launcher,
            "launcher_exists": Path(launcher).exists(),
            "prefix_path": prefix_path,
            "prefix_path_exists": Path(prefix_path).exists(),
            "deep_check": bool(deep),
        }
        if not deep:
            return report

        if launcher and Path(launcher).exists():
            runtime = SubprocessQGISRuntime(launcher=launcher, prefix_path=prefix_path or None)
            try:
                environment = runtime.environment_report()
                algorithms = runtime.list_algorithms("native:buffer", limit=5)
                report.update(environment)
                report["pyqgis"] = "ok"
                report["native_buffer_available"] = any(
                    item.get("id") == "native:buffer" for item in algorithms
                )
                report["health_execution"] = "launcher"
                return report
            except Exception as exc:  # pragma: no cover - depends on local QGIS install
                report["status"] = "error"
                report["pyqgis"] = "error"
                report["error"] = str(exc)
                return report
            finally:
                runtime.shutdown()

        runtime = QGISRuntime(prefix_path=prefix_path or None)
        try:
            runtime.ensure_ready()
            report["pyqgis"] = "ok"
            report["native_buffer_available"] = any(
                item.get("id") == "native:buffer" for item in runtime.list_algorithms("native:buffer", limit=5)
            )
            report["health_execution"] = "in_process"
        except Exception as exc:  # pragma: no cover - depends on local QGIS install
            report["status"] = "error"
            report["pyqgis"] = "error"
            report["error"] = str(exc)
        finally:
            runtime.shutdown()
        return report

    def search_toolbox(self, *, query: str = "", limit: int = 50, qgis: dict[str, Any] | None = None) -> dict[str, Any]:
        launcher = str((qgis or {}).get("launcher") or "").strip()
        prefix_path = str((qgis or {}).get("prefix_path") or "").strip()
        if launcher and Path(launcher).exists():
            runtime = SubprocessQGISRuntime(launcher=launcher, prefix_path=prefix_path or None)
            try:
                algorithms = runtime.list_algorithms(query, limit=limit)
                return make_json_safe({"algorithms": algorithms, "count": len(algorithms)})
            finally:
                runtime.shutdown()
        runtime = QGISRuntime(prefix_path=prefix_path or None)
        algorithms = runtime.list_algorithms(query, limit=limit)
        return {"algorithms": make_json_safe(algorithms), "count": len(algorithms)}

    def algorithm_help(self, algorithm_id: str, *, qgis: dict[str, Any] | None = None) -> dict[str, Any]:
        launcher = str((qgis or {}).get("launcher") or "").strip()
        prefix_path = str((qgis or {}).get("prefix_path") or "").strip()
        if launcher and Path(launcher).exists():
            runtime = SubprocessQGISRuntime(launcher=launcher, prefix_path=prefix_path or None)
            try:
                return make_json_safe(runtime.algorithm_help(algorithm_id))
            finally:
                runtime.shutdown()
        runtime = QGISRuntime(prefix_path=prefix_path or None)
        return make_json_safe(runtime.algorithm_help(algorithm_id))
