"""Subprocess worker that executes concrete PyQGIS runtime operations only."""

from __future__ import annotations

import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable

_SRC_ROOT = Path(__file__).resolve().parents[2]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from pineflow_runtime.errors import QGISRuntimeError, ToolExecutionError, ToolValidationError
from pineflow_runtime.runtime import QGISRuntime


def main() -> None:
    if "--runtime-rpc-loop" in sys.argv:
        _run_loop()
        return
    payload = _read_payload()
    prefix_path = str(dict(payload.get("qgis") or {}).get("prefix_path") or "").strip()
    if prefix_path:
        os.environ["QGIS_PREFIX_PATH"] = prefix_path
    runtime = QGISRuntime(prefix_path=prefix_path or None)
    try:
        result = _dispatch_silenced(runtime, payload)
        _write({"ok": True, "result": result})
    except (QGISRuntimeError, ToolExecutionError, ToolValidationError) as exc:
        _write(
            {
                "ok": False,
                "error": exc.message,
                "error_type": exc.__class__.__name__,
                "data": dict(exc.data or {}),
            }
        )
        sys.exit(1)
    except Exception as exc:
        _write(
            {
                "ok": False,
                "error": str(exc),
                "error_type": exc.__class__.__name__,
                "data": {},
            }
        )
        sys.exit(1)
    finally:
        runtime.shutdown()


def _run_loop() -> None:
    prefix_path = str(os.environ.get("QGIS_PREFIX_PATH") or "").strip()
    runtime = QGISRuntime(prefix_path=prefix_path or None)
    try:
        for line in sys.stdin:
            raw = str(line or "").lstrip("\ufeff").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise RuntimeError("Worker payload must be a JSON object.")
                if str(payload.get("operation") or "").strip() == "shutdown":
                    _write_line({"ok": True, "result": {"shutdown": True}})
                    break
                result = _dispatch_silenced(runtime, payload)
                _write_line({"ok": True, "result": result})
            except (QGISRuntimeError, ToolExecutionError, ToolValidationError) as exc:
                _write_line(
                    {
                        "ok": False,
                        "error": exc.message,
                        "error_type": exc.__class__.__name__,
                        "data": dict(exc.data or {}),
                    }
                )
            except Exception as exc:
                _write_line(
                    {
                        "ok": False,
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                        "data": {},
                    }
                )
    finally:
        runtime.shutdown()


def _dispatch_silenced(runtime: QGISRuntime, payload: dict[str, Any]) -> Any:
    # Keep the JSON protocol on stdout clean if PyQGIS or providers print progress.
    with redirect_stdout(sys.stderr):
        return _dispatch(runtime, payload)


def _dispatch(runtime: QGISRuntime, payload: dict[str, Any]) -> Any:
    if str(payload.get("mode") or "").strip() != "runtime_rpc":
        raise RuntimeError("Unsupported worker payload mode.")
    operation = str(payload.get("operation") or "").strip()
    arguments = dict(payload.get("arguments") or {})
    handlers: dict[str, Callable[[QGISRuntime, dict[str, Any]], Any]] = {
        "environment_report": lambda rt, args: rt.environment_report(),
        "list_algorithms": lambda rt, args: rt.list_algorithms(str(args.get("query") or ""), limit=int(args.get("limit") or 50)),
        "algorithm_help": lambda rt, args: rt.algorithm_help(str(args.get("algorithm_id") or "")),
        "inspect_vector_path": lambda rt, args: rt.inspect_vector_path(str(args.get("input_path") or "")),
        "inspect_raster_path": lambda rt, args: rt.inspect_raster_path(str(args.get("input_path") or "")),
        "csv_to_points": lambda rt, args: rt.csv_to_points(
            str(args.get("input_path") or ""),
            x_field=str(args.get("x_field") or ""),
            y_field=str(args.get("y_field") or ""),
            crs_authid=str(args.get("crs_authid") or "EPSG:4326"),
            encoding=str(args.get("encoding") or ""),
            output_path=str(args.get("output_path") or ""),
        ),
        "run_algorithm": lambda rt, args: rt.run_algorithm(
            str(args.get("algorithm_id") or ""),
            dict(args.get("params") or {}),
        ),
        "write_vector": lambda rt, args: rt.write_vector(
            str(args.get("input_path") or ""),
            str(args.get("output_path") or ""),
            driver_name=str(args.get("driver_name") or "") or None,
        ),
    }
    handler = handlers.get(operation)
    if handler is None:
        raise RuntimeError(f"Unsupported runtime operation: {operation or '<empty>'}")
    return handler(runtime, arguments)


def _read_payload() -> dict[str, Any]:
    raw = (sys.stdin.read() or "").lstrip("\ufeff").strip()
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise RuntimeError("Worker payload must be a JSON object.")
    return value


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()


def _write_line(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
