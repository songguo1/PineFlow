"""QGIS runtime proxy that executes only concrete GIS operations in a QGIS Python subprocess."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from threading import Lock
from typing import Any

from pineflow_runtime.errors import QGISRuntimeError, ToolExecutionError, ToolValidationError

from pineflow_api.application.qgis_launcher import launcher_command


class SubprocessQGISRuntime:
    """QGISRuntime-compatible proxy that delegates PyQGIS work to a worker process."""

    def __init__(self, *, launcher: str, prefix_path: str | None = None) -> None:
        self.launcher = str(launcher or "").strip()
        self.prefix_path = str(prefix_path or "").strip()
        self._process: subprocess.Popen[str] | None = None
        self._stderr_tail: list[str] = []
        self._lock = Lock()
        if not self.launcher or not Path(self.launcher).exists():
            raise QGISRuntimeError(
                "QGIS launcher is required for subprocess runtime execution.",
                data={"launcher": self.launcher or "<empty>"},
            )

    def ensure_ready(self) -> None:
        self._call("environment_report")

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown_locked()

    def environment_report(self) -> dict[str, Any]:
        return self._call("environment_report")

    def list_algorithms(self, query: str = "", *, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._call("list_algorithms", query=query, limit=limit) or [])

    def algorithm_help(self, algorithm_id: str) -> dict[str, Any]:
        return dict(self._call("algorithm_help", algorithm_id=algorithm_id) or {})

    def inspect_vector_path(self, input_path: str) -> dict[str, Any]:
        return dict(self._call("inspect_vector_path", input_path=input_path) or {})

    def inspect_raster_path(self, input_path: str) -> dict[str, Any]:
        return dict(self._call("inspect_raster_path", input_path=input_path) or {})

    def csv_to_points(
        self,
        input_path: str,
        *,
        x_field: str,
        y_field: str,
        crs_authid: str = "EPSG:4326",
        encoding: str = "",
        output_path: str,
    ) -> dict[str, Any]:
        return dict(
            self._call(
                "csv_to_points",
                input_path=input_path,
                x_field=x_field,
                y_field=y_field,
                crs_authid=crs_authid,
                encoding=encoding,
                output_path=output_path,
            )
            or {}
        )

    def run_algorithm(self, algorithm_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return dict(self._call("run_algorithm", algorithm_id=algorithm_id, params=dict(params or {})) or {})

    def write_vector(self, input_path: str, output_path: str, *, driver_name: str | None = None) -> dict[str, Any]:
        return dict(
            self._call(
                "write_vector",
                input_path=input_path,
                output_path=output_path,
                driver_name=driver_name,
            )
            or {}
        )

    def _call(self, operation: str, **arguments: Any) -> Any:
        payload = {
            "mode": "runtime_rpc",
            "operation": str(operation or "").strip(),
            "arguments": arguments,
            "qgis": {"prefix_path": self.prefix_path},
        }
        with self._lock:
            return self._call_locked(payload)

    def _call_locked(self, payload: dict[str, Any]) -> Any:
        process = self._ensure_process_locked()
        assert process.stdin is not None
        assert process.stdout is not None
        try:
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()
            value = self._read_json_response(process, payload)
        except (BrokenPipeError, OSError) as exc:
            self._shutdown_locked()
            raise QGISRuntimeError(
                "QGIS runtime worker stopped before returning a response.",
                data={"operation": payload.get("operation") or ""},
            ) from exc
        if not isinstance(value, dict):
            raise QGISRuntimeError(
                "QGIS runtime worker did not return a JSON object.",
                data={"operation": payload.get("operation") or ""},
            )
        if value.get("ok") is True:
            return value.get("result")
        message = str(value.get("error") or "QGIS runtime worker failed.")
        data = dict(value.get("data") or {})
        error_type = str(value.get("error_type") or "")
        if error_type == "ToolValidationError":
            raise ToolValidationError(message, data=data)
        if error_type == "ToolExecutionError":
            raise ToolExecutionError(message, data=data)
        raise QGISRuntimeError(message, data=data)

    def _read_json_response(self, process: subprocess.Popen[str], payload: dict[str, Any]) -> Any:
        assert process.stdout is not None
        for _ in range(50):
            output = str(process.stdout.readline() or "").strip()
            if not output:
                return_code = process.poll()
                stderr = self._worker_stderr_tail()
                self._shutdown_locked()
                raise QGISRuntimeError(
                    stderr or f"QGIS runtime worker returned empty output with code {return_code}.",
                    data={"operation": payload.get("operation") or "", "return_code": return_code},
                )
            try:
                return json.loads(output)
            except json.JSONDecodeError:
                # Some QGIS launchers/providers print startup diagnostics to stdout.
                # Keep the worker protocol resilient by treating non-JSON lines as diagnostics.
                self._stderr_tail.append(output)
                del self._stderr_tail[:-20]
                continue
        stderr = self._worker_stderr_tail()
        raise QGISRuntimeError(
            stderr or "QGIS runtime worker returned too many non-JSON lines.",
            data={"operation": payload.get("operation") or ""},
        )

    def _ensure_process_locked(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._process = subprocess.Popen(
            launcher_command(self.launcher, self._worker_script(), "--runtime-rpc-loop"),
            cwd=self._source_root(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self._worker_env(),
        )
        self._stderr_tail = []
        stderr = getattr(self._process, "stderr", None)
        if stderr is not None:
            threading.Thread(
                target=self._collect_stderr,
                args=(stderr,),
                daemon=True,
            ).start()
        return self._process

    def _shutdown_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                process.stdin.write(
                    json.dumps({"mode": "runtime_rpc", "operation": "shutdown", "arguments": {}}, ensure_ascii=False)
                    + "\n"
                )
                process.stdin.flush()
                if process.stdout is not None:
                    process.stdout.readline()
                process.wait(timeout=5)
                return
        except Exception:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _worker_env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.prefix_path:
            env["QGIS_PREFIX_PATH"] = self.prefix_path
        if not str(env.get("QGIS_AUTH_DB_DIR_PATH") or "").strip():
            env["QGIS_AUTH_DB_DIR_PATH"] = self._default_auth_db_dir()
        src_path = self._source_root()
        python_path = str(env.get("PYTHONPATH") or "")
        entries = [item for item in python_path.split(os.pathsep) if item]
        if src_path not in entries:
            env["PYTHONPATH"] = os.pathsep.join([src_path, *entries])
        return env

    @classmethod
    def _default_auth_db_dir(cls) -> str:
        candidates = [
            Path(tempfile.gettempdir()) / "pineflow-qgis-auth",
            Path(cls._source_root()).parent / ".pineflow" / "qgis-auth",
        ]
        for candidate in candidates:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
                return str(candidate)
            except OSError:
                continue
        return str(candidates[0])

    @staticmethod
    def _source_root() -> str:
        return str(Path(__file__).resolve().parents[2])

    @staticmethod
    def _worker_script() -> str:
        return str(Path(__file__).resolve().parents[1] / "entrypoints" / "worker.py")

    def _collect_stderr(self, stream: Any) -> None:
        for line in stream:
            text = str(line or "").strip()
            if not text:
                continue
            self._stderr_tail.append(text)
            del self._stderr_tail[:-20]

    def _worker_stderr_tail(self) -> str:
        if not self._stderr_tail:
            return ""
        return "\n".join(self._stderr_tail[-20:])
