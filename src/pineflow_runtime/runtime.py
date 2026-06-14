"""PyQGIS runtime bootstrap and thin execution helpers."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
from urllib.parse import quote
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import QGISRuntimeError, ToolExecutionError

SUPPORTED_VECTOR_DRIVERS: dict[str, str] = {
    ".geojson": "GeoJSON",
    ".gpkg": "GPKG",
    ".shp": "ESRI Shapefile",
}


@dataclass
class QGISModules:
    Qgis: Any
    QgsApplication: Any
    QgsCoordinateReferenceSystem: Any
    QgsProject: Any
    QgsRasterLayer: Any
    QgsVectorFileWriter: Any
    QgsVectorLayer: Any
    QgsWkbTypes: Any
    QgsNativeAlgorithms: Any
    Processing: Any
    processing: Any


def driver_for_vector_path(path: str) -> str:
    ext = Path(str(path or "")).suffix.lower()
    driver = SUPPORTED_VECTOR_DRIVERS.get(ext)
    if not driver:
        allowed = ", ".join(sorted(SUPPORTED_VECTOR_DRIVERS))
        raise ToolExecutionError(f"Unsupported vector format: {ext or '<none>'}. Allowed: {allowed}")
    return driver


def csv_uri_for_path(
    input_path: str,
    *,
    encoding: str = "",
    x_field: str = "",
    y_field: str = "",
    crs_authid: str = "",
    geom_type: str = "none",
) -> str:
    path_text = str(Path(input_path).resolve()).replace(os.sep, "/")
    query_parts = ["type=csv", "detectTypes=yes", "geomType=" + quote(str(geom_type or "none"))]
    if encoding:
        query_parts.append("encoding=" + quote(str(encoding)))
    if x_field:
        query_parts.append("xField=" + quote(str(x_field)))
    if y_field:
        query_parts.append("yField=" + quote(str(y_field)))
    if crs_authid:
        query_parts.append("crs=" + quote(str(crs_authid)))
    return f"file:///{quote(path_text)}?{'&'.join(query_parts)}"


def _call_or_default(obj: Any, method_name: str, default: Any, *args: Any) -> Any:
    method = getattr(obj, method_name, None)
    if not callable(method):
        return default
    try:
        return method(*args)
    except Exception:
        return default


def _qgis_auth_db_dir() -> str:
    configured = str(os.environ.get("QGIS_AUTH_DB_DIR_PATH") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    candidates.append(Path(tempfile.gettempdir()) / "pineflow-qgis-auth")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return str(candidate)
        except OSError:
            continue
    return str(candidates[-1])


class QGISRuntime:
    """Manage one in-process PyQGIS runtime for the MCP service."""

    def __init__(self, *, prefix_path: str | None = None) -> None:
        self.prefix_path = str(prefix_path or os.environ.get("QGIS_PREFIX_PATH") or "").strip() or None
        self._app: Any | None = None
        self._modules: QGISModules | None = None

    def ensure_ready(self) -> None:
        if self._app is not None and self._modules is not None:
            return
        prefix_path = self.prefix_path or self._discover_prefix_path()
        self._prepare_import_paths(prefix_path)
        modules = self._import_qgis_modules()
        modules.QgsApplication.setPrefixPath(prefix_path, True)
        modules.QgsApplication.setAuthDatabaseDirPath(_qgis_auth_db_dir())
        app = modules.QgsApplication([], False)
        app.initQgis()
        modules.Processing.initialize()
        registry = modules.QgsApplication.processingRegistry()
        provider_ids = {provider.id() for provider in registry.providers()}
        if "native" not in provider_ids:
            registry.addProvider(modules.QgsNativeAlgorithms())
        self.prefix_path = prefix_path
        self._modules = modules
        self._app = app

    def shutdown(self) -> None:
        if self._app is not None:
            self._app.exitQgis()
        self._app = None
        self._modules = None

    def environment_report(self) -> dict[str, Any]:
        qgis_version = ""
        qgis_version_int = ""
        if self._modules is not None:
            qgis_version = str(getattr(self._modules.Qgis, "QGIS_VERSION", "") or "")
            qgis_version_int = str(getattr(self._modules.Qgis, "QGIS_VERSION_INT", "") or "")
        return {
            "initialized": bool(self._app is not None and self._modules is not None),
            "prefix_path": self.prefix_path or "",
            "qgis_version": qgis_version,
            "qgis_version_int": qgis_version_int,
            "supported_vector_formats": dict(SUPPORTED_VECTOR_DRIVERS),
        }

    def list_algorithms(self, query: str = "", *, limit: int = 50) -> list[dict[str, Any]]:
        self.ensure_ready()
        assert self._modules is not None
        query_text = str(query or "").strip().lower()
        matches: list[dict[str, Any]] = []
        for algorithm in self._modules.QgsApplication.processingRegistry().algorithms():
            alg_id = str(algorithm.id())
            display_name = str(algorithm.displayName())
            provider = algorithm.provider().id() if algorithm.provider() else ""
            haystack = f"{alg_id} {display_name} {provider} {algorithm.group()}".lower()
            if query_text and query_text not in haystack:
                continue
            matches.append(
                {
                    "id": alg_id,
                    "name": display_name,
                    "provider": provider,
                    "group": str(algorithm.group() or ""),
                }
            )
            if len(matches) >= int(limit):
                break
        return matches

    def algorithm_help(self, algorithm_id: str) -> dict[str, Any]:
        self.ensure_ready()
        assert self._modules is not None
        algorithm = self._modules.QgsApplication.processingRegistry().algorithmById(
            str(algorithm_id or "").strip()
        )
        if algorithm is None:
            raise ToolExecutionError(f"Unknown QGIS algorithm: {algorithm_id}")

        parameters = []
        for param in algorithm.parameterDefinitions():
            parameters.append(
                {
                    "name": str(param.name()),
                    "description": str(param.description()),
                    "type": str(param.type()),
                    "default": param.defaultValue(),
                    "optional": bool(param.flags() & param.FlagOptional),
                }
            )
        outputs = [
            {
                "name": str(output.name()),
                "description": str(output.description()),
                "type": str(output.type()),
            }
            for output in algorithm.outputDefinitions()
        ]
        return {
            "id": str(algorithm.id()),
            "name": str(algorithm.displayName()),
            "group": str(algorithm.group() or ""),
            "parameters": parameters,
            "outputs": outputs,
        }

    def create_crs(self, crs_authid: str) -> Any:
        self.ensure_ready()
        assert self._modules is not None
        crs = self._modules.QgsCoordinateReferenceSystem(str(crs_authid or "").strip())
        if not crs.isValid():
            raise ToolExecutionError(f"Invalid CRS: {crs_authid}")
        return crs

    def load_vector_layer(self, input_path: str) -> Any:
        self.ensure_ready()
        assert self._modules is not None
        layer_name = Path(input_path).stem or "layer"
        layer = self._modules.QgsVectorLayer(str(input_path), layer_name, "ogr")
        if not layer.isValid():
            raise ToolExecutionError(f"Failed to load vector layer: {input_path}")
        return layer

    def inspect_vector_path(self, input_path: str) -> dict[str, Any]:
        layer = self.load_vector_layer(input_path)
        return self.inspect_layer(layer, source_path=input_path)

    def load_raster_layer(self, input_path: str) -> Any:
        self.ensure_ready()
        assert self._modules is not None
        layer_name = Path(input_path).stem or "raster"
        layer = self._modules.QgsRasterLayer(str(input_path), layer_name)
        if not layer.isValid():
            raise ToolExecutionError(f"Failed to load raster layer: {input_path}")
        return layer

    def inspect_raster_path(self, input_path: str) -> dict[str, Any]:
        layer = self.load_raster_layer(input_path)
        return self.inspect_raster_layer(layer, source_path=input_path)

    def csv_to_point_layer(
        self,
        input_path: str,
        *,
        x_field: str,
        y_field: str,
        crs_authid: str = "EPSG:4326",
        encoding: str = "",
    ) -> Any:
        self.ensure_ready()
        assert self._modules is not None
        crs = self.create_crs(crs_authid or "EPSG:4326")
        uri = csv_uri_for_path(
            input_path,
            encoding=encoding,
            x_field=str(x_field),
            y_field=str(y_field),
            crs_authid=str(crs.authid() or crs_authid),
            geom_type="point",
        )
        layer_name = Path(input_path).stem or "csv_points"
        layer = self._modules.QgsVectorLayer(uri, layer_name, "delimitedtext")
        if not layer.isValid():
            raise ToolExecutionError(
                f"Failed to create point layer from CSV using x={x_field}, y={y_field}: {input_path}"
            )
        if not str(layer.crs().authid() or layer.crs().description() or "").strip():
            layer.setCrs(crs)
        return layer

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
        layer = self.csv_to_point_layer(
            input_path,
            x_field=x_field,
            y_field=y_field,
            crs_authid=crs_authid,
            encoding=encoding,
        )
        return self.write_layer(layer, output_path, driver_name="GPKG")

    def inspect_layer(self, layer: Any, *, source_path: str | None = None) -> dict[str, Any]:
        self.ensure_ready()
        assert self._modules is not None
        crs = layer.crs()
        geometry_label = self._modules.QgsWkbTypes.displayString(layer.wkbType())
        fields = [field.name() for field in layer.fields()]
        field_summaries = [
            {
                "name": str(field.name() or ""),
                "type": str(_call_or_default(field, "typeName", "") or _call_or_default(field, "type", "")),
            }
            for field in layer.fields()
        ]
        return {
            "layer_name": str(layer.name() or ""),
            "source_path": str(source_path or layer.source() or ""),
            "feature_count": int(layer.featureCount()),
            "fields": fields,
            "field_summaries": field_summaries,
            "crs": str(crs.authid() or crs.description() or ""),
            "geometry_type": geometry_label,
            "provider": str(layer.providerType() or ""),
            "storage_type": str(layer.storageType() or ""),
        }

    def inspect_raster_layer(self, layer: Any, *, source_path: str | None = None) -> dict[str, Any]:
        self.ensure_ready()
        provider = layer.dataProvider()
        crs = layer.crs()
        extent = layer.extent()
        band_count = int(_call_or_default(layer, "bandCount", _call_or_default(provider, "bandCount", 0)) or 0)
        bands: list[dict[str, Any]] = []
        for band in range(1, band_count + 1):
            bands.append(
                {
                    "band": band,
                    "data_type": str(_call_or_default(provider, "dataType", "", band)),
                    "source_data_type": str(_call_or_default(provider, "sourceDataType", "", band)),
                    "has_nodata": bool(_call_or_default(provider, "sourceHasNoDataValue", False, band)),
                    "nodata": _call_or_default(provider, "sourceNoDataValue", None, band),
                }
            )
        width = int(_call_or_default(layer, "width", 0) or 0)
        height = int(_call_or_default(layer, "height", 0) or 0)
        return {
            "layer_name": str(layer.name() or ""),
            "source_path": str(source_path or layer.source() or ""),
            "crs": str(crs.authid() or crs.description() or ""),
            "provider": str(layer.providerType() or ""),
            "width": width,
            "height": height,
            "shape": [height, width],
            "band_count": band_count,
            "bands": bands,
            "extent": {
                "xmin": _call_or_default(extent, "xMinimum", None),
                "ymin": _call_or_default(extent, "yMinimum", None),
                "xmax": _call_or_default(extent, "xMaximum", None),
                "ymax": _call_or_default(extent, "yMaximum", None),
            },
            "pixel_size": {
                "x": _call_or_default(layer, "rasterUnitsPerPixelX", None),
                "y": _call_or_default(layer, "rasterUnitsPerPixelY", None),
            },
        }

    def run_algorithm(self, algorithm_id: str, params: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        assert self._modules is not None
        try:
            result = self._modules.processing.run(str(algorithm_id), dict(params))
        except Exception as exc:  # pragma: no cover - real PyQGIS failure path
            raise ToolExecutionError(
                f"QGIS processing failed for {algorithm_id}: {exc}",
                data={"algorithm_id": algorithm_id},
            ) from exc
        if not isinstance(result, dict):
            raise ToolExecutionError(
                f"Unexpected QGIS processing result for {algorithm_id}: {type(result).__name__}"
            )
        return result

    def write_vector(self, input_path: str, output_path: str, *, driver_name: str | None = None) -> dict[str, Any]:
        layer = self.load_vector_layer(input_path)
        return self.write_layer(layer, output_path, driver_name=driver_name)

    def write_layer(self, layer: Any, output_path: str, *, driver_name: str | None = None) -> dict[str, Any]:
        self.ensure_ready()
        assert self._modules is not None
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        driver = str(driver_name or driver_for_vector_path(output_path))
        options = self._modules.QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = driver
        options.fileEncoding = "UTF-8"
        result = self._modules.QgsVectorFileWriter.writeAsVectorFormatV3(
            layer,
            str(output_path),
            self._modules.QgsProject.instance().transformContext(),
            options,
        )
        error_code = result[0] if isinstance(result, tuple) else result
        error_message = result[2] if isinstance(result, tuple) and len(result) > 2 else ""
        if error_code != self._modules.QgsVectorFileWriter.NoError:
            raise ToolExecutionError(
                f"Failed to write vector output: {output_path}. {error_message}".strip()
            )
        return self.inspect_vector_path(output_path)

    def _discover_prefix_path(self) -> str:
        for candidate in self._candidate_prefix_paths():
            if candidate.exists():
                return str(candidate)
        raise QGISRuntimeError(
            "Could not find a QGIS installation. Set QGIS_PREFIX_PATH to your local QGIS apps/qgis directory.",
            data={"searched": [str(path) for path in self._candidate_prefix_paths()]},
        )

    def _candidate_prefix_paths(self) -> list[Path]:
        candidates: list[Path] = []
        for env_name in ("QGIS_PREFIX_PATH", "QGIS_INSTALL_ROOT", "OSGEO4W_ROOT"):
            raw = str(os.environ.get(env_name) or "").strip()
            if not raw:
                continue
            env_path = Path(raw)
            if env_name == "QGIS_PREFIX_PATH":
                candidates.append(env_path)
            else:
                candidates.append(env_path / "apps" / "qgis")

        fixed_candidates = [
            Path(r"C:\OSGeo4W\apps\qgis"),
            Path(r"C:\OSGeo4W64\apps\qgis"),
        ]
        candidates.extend(fixed_candidates)

        for program_dir in filter(None, [os.environ.get("ProgramW6432"), os.environ.get("ProgramFiles")]):
            base = Path(program_dir)
            if not base.exists():
                continue
            for match in sorted(base.glob("QGIS*")):
                candidates.append(match / "apps" / "qgis")

        deduped: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(candidate)
        return deduped

    def _prepare_import_paths(self, prefix_path: str) -> None:
        prefix = Path(prefix_path)
        install_root = prefix.parents[1] if len(prefix.parents) >= 2 else prefix.parent
        path_entries = [
            install_root / "bin",
            prefix / "bin",
            install_root / "apps" / "Qt5" / "bin",
            install_root / "apps" / "Qt6" / "bin",
        ]
        python_entries = [
            prefix / "python",
            prefix / "python" / "plugins",
        ]
        for site_packages in (install_root / "apps").glob("Python*/Lib/site-packages"):
            python_entries.append(site_packages)

        current_path = os.environ.get("PATH") or ""
        for entry in path_entries:
            if entry.exists():
                entry_text = str(entry)
                if entry_text.lower() not in current_path.lower():
                    current_path = f"{entry_text}{os.pathsep}{current_path}" if current_path else entry_text
        os.environ["PATH"] = current_path

        for entry in python_entries:
            if entry.exists():
                entry_text = str(entry)
                if entry_text not in sys.path:
                    sys.path.insert(0, entry_text)

    def _import_qgis_modules(self) -> QGISModules:
        try:
            qgis_core = importlib.import_module("qgis.core")
            qgis_analysis = importlib.import_module("qgis.analysis")
            processing = importlib.import_module("processing")
            processing_core = importlib.import_module("processing.core.Processing")
        except Exception as exc:  # pragma: no cover - depends on local QGIS install
            raise QGISRuntimeError(
                "PyQGIS imports failed. Ensure QGIS Desktop is installed and QGIS_PREFIX_PATH points to apps/qgis.",
                data={"prefix_path": self.prefix_path or ""},
            ) from exc

        return QGISModules(
            Qgis=qgis_core.Qgis,
            QgsApplication=qgis_core.QgsApplication,
            QgsCoordinateReferenceSystem=qgis_core.QgsCoordinateReferenceSystem,
            QgsProject=qgis_core.QgsProject,
            QgsRasterLayer=qgis_core.QgsRasterLayer,
            QgsVectorFileWriter=qgis_core.QgsVectorFileWriter,
            QgsVectorLayer=qgis_core.QgsVectorLayer,
            QgsWkbTypes=qgis_core.QgsWkbTypes,
            QgsNativeAlgorithms=qgis_analysis.QgsNativeAlgorithms,
            Processing=processing_core.Processing,
            processing=processing,
        )
