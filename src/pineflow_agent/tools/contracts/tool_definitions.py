"""Central tool definitions for the GIS agent runtime.

Tool metadata (contracts, tags, groups, requirements, rules) is loaded from
YAML files under tools/contracts/defs/{base,semantic}/. Shared slot types live
in tools/contracts/defs/_slots.yaml.  See ``tools/contracts/defs/`` for the
full catalogue.
"""

from __future__ import annotations

import re

import yaml
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pineflow_agent.core.models import Observation
from pineflow_agent.tools.semantic.semantic_tools import is_semantic_action, semantic_algorithm_call

ToolExecutor = Callable[[Any, dict[str, Any]], Observation]

# ── YAML loader ──────────────────────────────────────────────────────────

DEFS_DIR = Path(__file__).resolve().parent / "defs"

def _load_yaml(rel_path: str) -> dict[str, Any]:
    with open(DEFS_DIR / rel_path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def _load_slots() -> dict[str, dict[str, Any]]:
    """Load shared slot type definitions from _slots.yaml."""
    return _load_yaml("_slots.yaml")

def _iter_tool_yamls() -> Iterable[dict[str, Any]]:
    """Iterate all tool YAML files under base/ and semantic/, yielding resolved dicts."""
    slots = _load_slots()
    for subdir in ("base", "semantic"):
        subpath = DEFS_DIR / subdir
        if not subpath.is_dir():
            continue
        for yaml_file in sorted(subpath.glob("*.yaml")):
            data = _load_yaml(f"{subdir}/{yaml_file.name}")
            _resolve_slot_refs(data, slots)
            yield data

def _resolve_slot_refs(data: dict, slots: dict) -> None:
    """Fill in property types from shared slots where the tool YAML doesn't declare them."""
    props: dict[str, Any] = dict(data.get("properties") or {})
    for slot in list(data.get("required_slots") or []):
        if slot not in props and slot in slots:
            props[slot] = dict(slots[slot])
    for slot, default in (data.get("defaults") or {}).items():
        props.setdefault(slot, dict(slots.get(slot) or {}))
        props[slot].setdefault("default", default)
    data["properties"] = props

def _build_contract(data: dict) -> dict[str, Any]:
    """Build a contract dict from YAML data (backward-compatible format)."""
    name = str(data.get("name") or "").strip()
    display = dict(data.get("display") or {})
    product_title = PRODUCT_DISPLAY_TITLES.get(name)
    if product_title:
        display.setdefault("product_title", product_title)
    display["parameter_labels"] = _parameter_labels_from_properties(
        dict(data.get("properties") or {}),
        display_labels=display.get("parameter_labels"),
    )
    contract: dict[str, Any] = {
        "description": data.get("description", ""),
        "required_slots": list(data.get("required_slots") or []),
        "properties": dict(data.get("properties") or {}),
        "display": display,
    }
    if data.get("algorithm_id"):
        contract["algorithm_id"] = data["algorithm_id"]
    for key in ("slot_roles", "processing_parameters", "output_policy"):
        if isinstance(data.get(key), dict):
            contract[key] = dict(data.get(key) or {})
    if isinstance(data.get("missing_slot_messages"), dict):
        contract["missing_slot_messages"] = dict(data.get("missing_slot_messages") or {})
    if "terminal" in data:
        contract["terminal"] = data["terminal"]
    return contract

def _category_for_tool(name: str) -> str:
    """Bootstrapping category for tools not yet in YAML (e.g. tests)."""
    if name in {"load_vector", "load_raster", "load_csv", "csv_to_points", "summarize_layer", "inspect_fields"}:
        return "data"
    if name in {"export_result", "final_answer"}:
        return "output"
    if name in {"select_toolkit", "inspect_workspace", "load_skill", "suggest_skill", "proactive_clarification"}:
        return "runtime"
    if name in {"discover_algorithms", "algorithm_help", "run_algorithm"}:
        return "qgis_generic"
    return "qgis_semantic"


# ── ToolDefinition ───────────────────────────────────────────────────────

DEFAULT_OPTIONAL_SLOTS = ("output_name", "output_path", "output")


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    contract: dict[str, Any]
    category: str
    executor: ToolExecutor | None = None
    provider: str = "builtin_qgis"
    groups: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    layer_requirements: tuple[tuple[str, str], ...] = ()
    geometry_requirements: tuple[tuple[str, str], ...] = ()
    field_requirements: tuple[tuple[str, tuple[str, ...]], ...] = ()
    semantic_rules: tuple[str, ...] = ()
    preflight_rules: tuple[str, ...] = ()
    terminal: bool = False

    def openai_schema(self) -> dict[str, Any]:
        return _tool_schema(self.name, self.contract)

    def contract_without_display(self) -> dict[str, Any]:
        return _contract_without_display(self.contract)

    def step_title(self, action_input: dict[str, Any] | None = None) -> str:
        return _format_template(self.contract.get("display", {}).get("title"), self._context(action_input), self.name).strip()

    def command_text(self, action_input: dict[str, Any] | None = None) -> str:
        return _format_template(self.contract.get("display", {}).get("command"), self._context(action_input), self.name).strip()

    def _context(self, action_input: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(action_input or {})
        payload.setdefault("name", self.name)
        algorithm_id = payload.get("algorithm_id") or self.contract.get("algorithm_id") or ""
        payload["algorithm_id"] = algorithm_id
        payload.setdefault("algorithm_title", algorithm_id or ("help" if self.name == "algorithm_help" else "QGIS algorithm"))
        payload.setdefault("algorithm_command", algorithm_id or "<missing algorithm>")
        payload.setdefault("query", payload.get("query") or "")
        payload.setdefault("input_path", payload.get("input_path") or "<missing path>")
        payload.setdefault("layer_ref", payload.get("layer_ref") or payload.get("input_ref") or "<missing layer>")
        payload.setdefault("message", payload.get("message") or "")
        return payload


# ── Public API ───────────────────────────────────────────────────────────

_tool_definitions_cache: dict[str, ToolDefinition] | None = None

def tool_definitions() -> dict[str, ToolDefinition]:
    """Return all registered ToolDefinitions, loading from YAML on first call."""
    global _tool_definitions_cache
    if _tool_definitions_cache is not None:
        return _tool_definitions_cache

    definitions: dict[str, ToolDefinition] = {}
    for data in _iter_tool_yamls():
        name = str(data.get("name") or "").strip()
        description = str(data.get("description") or f"Run GIS action {name}.")

        # -- dynamic descriptions --
        if name == "load_skill":
            try:
                from pineflow_agent.tools.registry.skill_registry import default_skill_registry
                registry = default_skill_registry()
                description = registry.load_skill_description()
                skill_names = ", ".join(registry.names())
                props = data.setdefault("properties", {})
                props.setdefault("name", {})
                props["name"]["description"] = f"Skill name to load. Available: {skill_names}."
            except Exception:
                pass

        definitions[name] = ToolDefinition(
            name=name,
            description=description,
            contract=_build_contract(data),
            category=str(data.get("category") or _category_for_tool(name)),
            executor=_executor_for_tool(name),
            provider="builtin_qgis",
            groups=tuple(data.get("groups") or ()),
            tags=tuple(data.get("tags") or ()),
            layer_requirements=tuple((str(k), str(v)) for k, v in (data.get("layer_requirements") or {}).items()),
            geometry_requirements=tuple((str(k), str(v)) for k, v in (data.get("geometry_requirements") or {}).items()),
            field_requirements=tuple(
                (str(inp), tuple(str(f) for f in fields))
                for inp, fields in (data.get("field_requirements") or {}).items()
            ),
            semantic_rules=tuple(data.get("rules", {}).get("semantic") or ()),
            preflight_rules=tuple(data.get("rules", {}).get("preflight") or ()),
            terminal=bool(data.get("terminal", False)),
        )

    _tool_definitions_cache = definitions
    return definitions


def tool_definition_for_action(action: str) -> ToolDefinition | None:
    return tool_definitions().get(str(action or "").strip())


def display_title_for_action(action: str) -> str:
    """Return the product-facing display title for an action.

    YAML display titles are still used for tool contracts and prompt/debug text.
    This helper is the single product-facing fallback used by transcript,
    reports, and narration code.
    """

    key = str(action or "").strip()
    definition = tool_definition_for_action(key)
    if definition is not None:
        display = dict(definition.contract.get("display") or {})
        title = str(display.get("product_title") or definition.step_title({}) or "").strip()
        if title:
            return title
    if key in PRODUCT_DISPLAY_TITLES:
        return PRODUCT_DISPLAY_TITLES[key]
    if key.startswith("native:"):
        return key.split(":", 1)[-1].replace("_", " ")
    return key.replace("_", " ") or "GIS operation"


def canonical_action_for_intent(text: str, *, context: dict[str, Any] | None = None) -> str:
    del context
    key = str(text or "").strip()
    if not key:
        return ""

    definitions = tool_definitions()
    if key in definitions:
        return key

    normalized = _normalized_intent_text(key)
    alias = _action_for_normalized_text(normalized, definitions)
    if alias:
        return alias

    return ""


def algorithm_id_for_action(action: str) -> str:
    """Return the canonical algorithm id recorded for reports and audit text."""

    key = str(action or "").strip()
    definition = tool_definition_for_action(key)
    if definition is None:
        return ""
    algorithm_id = str(definition.contract.get("algorithm_id") or "").strip()
    if algorithm_id:
        return algorithm_id
    if key in {"export_result", "final_answer"}:
        return key
    return ""


def parameter_labels_for_action(action: str) -> dict[str, str]:
    """Return product-facing parameter labels for one action."""

    definition = tool_definition_for_action(action)
    if definition is None:
        return {}
    display = dict(definition.contract.get("display") or {})
    labels = display.get("parameter_labels")
    if isinstance(labels, dict):
        return {str(key): str(value) for key, value in labels.items() if str(value).strip()}
    return _parameter_labels_from_properties(dict(definition.contract.get("properties") or {}))


def parameter_label_for_slot(slot: str) -> str:
    key = str(slot or "").strip()
    schema = _slot_schema(key)
    label = _parameter_label_from_schema(key, schema)
    if label:
        return label
    return key.replace("_", " ")


def _parameter_labels_from_properties(
    properties: dict[str, Any],
    *,
    display_labels: Any = None,
) -> dict[str, str]:
    labels = {str(key): str(value) for key, value in dict(display_labels or {}).items()} if isinstance(display_labels, dict) else {}
    for slot, schema in properties.items():
        key = str(slot or "").strip()
        if not key:
            continue
        label = _parameter_label_from_schema(key, dict(schema or {}) if isinstance(schema, dict) else {})
        if label:
            labels.setdefault(key, label)
    return labels


def _parameter_label_from_schema(slot: str, schema: dict[str, Any]) -> str:
    label = str(
        schema.get("display_label")
        or schema.get("label")
        or schema.get("title")
        or PRODUCT_PARAMETER_LABELS.get(slot)
        or ""
    ).strip()
    return label


def _action_for_normalized_text(normalized: str, definitions: dict[str, ToolDefinition]) -> str:
    if not normalized:
        return ""
    for name, definition in definitions.items():
        candidates = (
            name,
            display_title_for_action(name),
            str((definition.contract.get("display") or {}).get("title") or ""),
            str(definition.step_title({}) or ""),
        )
        for candidate in candidates:
            if _normalized_intent_text(candidate) == normalized:
                return name
    return ""


def _normalized_intent_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip()).casefold()


def action_contracts() -> dict[str, dict[str, Any]]:
    return {name: definition.contract_without_display() for name, definition in tool_definitions().items()}


def action_contracts_for(names: list[str] | tuple[str, ...]) -> dict[str, dict[str, Any]]:
    definitions = tool_definitions()
    selected: dict[str, dict[str, Any]] = {}
    for name in names:
        clean = str(name or "").strip()
        if not clean:
            continue
        definition = definitions.get(clean)
        if definition is None:
            continue
        selected[clean] = definition.contract_without_display()
    return selected


def openai_tools() -> list[dict[str, Any]]:
    return [definition.openai_schema() for definition in tool_definitions().values()]


def tool_groups() -> dict[str, tuple[str, ...]]:
    groups: dict[str, list[str]] = {}
    for definition in tool_definitions().values():
        for group in definition.groups:
            groups.setdefault(group, []).append(definition.name)
    return {name: tuple(values) for name, values in groups.items()}


def tool_schema(name: str, contract: dict[str, Any]) -> dict[str, Any]:
    return _tool_schema(name, contract)


PRODUCT_DISPLAY_TITLES = {
    "select_toolkit": "准备工具能力",
    "inspect_workspace": "检查工作区",
    "load_skill": "加载 GIS 知识",
    "suggest_skill": "推荐 GIS 知识",
    "load_vector": "加载矢量图层",
    "load_raster": "加载栅格图层",
    "load_csv": "加载 CSV 表格",
    "summarize_layer": "图层汇总",
    "inspect_fields": "字段检查",
    "csv_to_points": "CSV 转点图层",
    "reproject_layer": "重投影图层",
    "fix_geometries": "修复几何",
    "buffer_layer": "缓冲区分析",
    "clip_layer": "裁剪分析",
    "extract_by_attribute": "按属性筛选",
    "keep_fields": "保留字段",
    "rename_field": "重命名字段",
    "select_by_expression": "按表达式筛选",
    "extract_by_location": "按位置筛选",
    "join_by_location": "空间连接",
    "field_calculator": "字段计算",
    "raster_calculator": "栅格计算",
    "zonal_statistics": "分区统计",
    "join_by_nearest": "最近邻连接",
    "count_points_in_polygon": "面内点计数",
    "dissolve_layer": "融合图层",
    "merge_layers": "合并图层",
    "intersect_layer": "相交分析",
    "difference_layer": "差异分析",
    "union_layer": "联合分析",
    "symmetrical_difference": "对称差异分析",
    "centroid_layer": "生成质心",
    "point_on_surface": "生成面内点",
    "multipart_to_singlepart": "拆分多部件",
    "simplify_geometry": "简化几何",
    "delete_duplicate_geometries": "删除重复几何",
    "snap_geometries": "几何捕捉",
    "check_validity": "检查几何有效性",
    "reproject_raster": "重投影栅格",
    "clip_raster_by_mask": "按掩膜裁剪栅格",
    "clip_raster_by_extent": "按范围裁剪栅格",
    "raster_sampling": "栅格采样",
    "rasterize_vector": "矢量转栅格",
    "polygonize_raster": "栅格转面",
    "slope": "坡度分析",
    "aspect": "坡向分析",
    "hillshade": "山体阴影",
    "contour": "等高线生成",
    "reclassify_raster": "栅格重分类",
    "terrain_ruggedness_index": "地形起伏指数",
    "topographic_position_index": "地形位置指数",
    "roughness": "地形粗糙度",
    "export_result": "导出结果",
    "native:buffer": "缓冲区分析",
    "native:clip": "裁剪分析",
    "native:fixgeometries": "几何修复",
    "native:reprojectlayer": "重投影图层",
    "native:mergevectorlayers": "合并图层",
    "native:dissolve": "融合图层",
    "native:extractbyattribute": "按属性筛选",
    "native:extractbylocation": "按位置筛选",
    "native:joinattributesbylocation": "空间连接",
    "native:intersection": "相交分析",
    "native:difference": "差异分析",
    "gdal:cliprasterbymasklayer": "按掩膜裁剪栅格",
    "gdal:rastercalculator": "栅格计算",
    "gdal:slope": "坡度分析",
    "gdal:aspect": "坡向分析",
    "gdal:hillshade": "山体阴影",
    "gdal:contour": "等高线生成",
}


PRODUCT_PARAMETER_LABELS = {
    "input_ref": "输入图层",
    "overlay_ref": "叠加图层",
    "input_refs": "输入图层",
    "intersect_ref": "相交图层",
    "join_ref": "连接图层",
    "layer_ref": "图层",
    "table_ref": "表格",
    "raster_ref": "栅格图层",
    "raster_refs": "栅格图层",
    "mask_ref": "掩膜图层",
    "point_ref": "点图层",
    "polygon_ref": "面图层",
    "output_name": "结果名称",
    "output_path": "输出文件",
    "output": "输出",
    "target_crs": "目标坐标系",
    "crs": "坐标系",
    "distance": "距离",
    "unit": "单位",
    "field": "字段",
    "fields": "字段列表",
    "field_name": "字段名",
    "operator": "运算符",
    "value": "值",
    "predicate": "空间关系",
    "formula": "公式",
    "expression": "表达式",
    "x_field": "X 字段",
    "y_field": "Y 字段",
    "segments": "分段数",
    "dissolve": "融合",
    "dissolve_field": "融合字段",
    "method": "方法",
    "discard_nonmatching": "丢弃未匹配",
    "join_fields": "连接字段",
    "prefix": "字段前缀",
    "field_type": "字段类型",
    "field_length": "字段长度",
    "field_precision": "字段精度",
    "tolerance": "容差",
    "extent": "范围",
    "band": "波段",
    "raster_band": "栅格波段",
    "statistics": "统计项",
    "width": "宽度",
    "height": "高度",
    "burn_value": "栅格值",
    "column_prefix": "列名前缀",
    "max_distance": "最大距离",
    "neighbors": "邻近数量",
    "resampling": "重采样",
    "crop_to_cutline": "按掩膜裁剪",
    "nodata": "NoData",
    "interval": "间距",
    "min_field": "最小值字段",
    "max_field": "最大值字段",
    "value_field": "输出值字段",
    "z_factor": "Z 因子",
    "scale": "比例",
    "azimuth": "方位角",
    "altitude": "高度角",
    "compute_edges": "计算边缘",
}


# ── Schema builders ──────────────────────────────────────────────────────

def _format_template(template: Any, context: dict[str, Any], fallback: str) -> str:
    text = str(template or "").strip()
    if not text:
        return fallback
    try:
        return text.format(**context)
    except Exception:
        return text


def _tool_schema(name: str, contract: dict[str, Any]) -> dict[str, Any]:
    required = [str(slot) for slot in list(contract.get("required_slots") or []) if str(slot)]
    properties = _properties_for_contract(contract)
    for slot in required:
        properties.setdefault(slot, _slot_schema(slot))
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": str(contract.get("description") or f"Run GIS action {name}."),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": True,
            },
        },
    }


def _properties_for_contract(contract: dict[str, Any]) -> dict[str, Any]:
    explicit = contract.get("properties")
    if isinstance(explicit, dict):
        return {str(key): dict(value) if isinstance(value, dict) else {"description": str(value)} for key, value in explicit.items()}

    properties: dict[str, Any] = {}
    for slot in list(contract.get("required_slots") or []):
        properties[str(slot)] = _slot_schema(str(slot))
    defaults = dict(contract.get("defaults") or {})
    for slot, value in defaults.items():
        properties.setdefault(str(slot), _slot_schema(str(slot), default=value))
    for slot in DEFAULT_OPTIONAL_SLOTS:
        properties.setdefault(slot, _slot_schema(slot))
    return properties


def _slot_schema(slot: str, default: Any | None = None) -> dict[str, Any]:
    # Try shared slot types from YAML first
    try:
        slots = _load_slots()
        if slot in slots:
            schema = dict(slots[slot])
            if default is not None:
                schema["default"] = default
            return schema
    except Exception:
        pass
    schema: dict[str, Any] = {"description": f"Parameter {slot}."}
    if default is not None:
        schema["default"] = default
    return schema


def _contract_without_display(contract: dict[str, Any]) -> dict[str, Any]:
    payload = dict(contract)
    payload.pop("display", None)
    payload.pop("terminal", None)
    return payload


# ── Executors ────────────────────────────────────────────────────────────

def _executor_for_tool(name: str) -> ToolExecutor:
    if name == "discover_algorithms":
        return _execute_discover_algorithms
    if name == "algorithm_help":
        return _execute_algorithm_help
    if name == "load_vector":
        return _execute_load_vector
    if name == "load_raster":
        return _execute_load_raster
    if name == "load_csv":
        return _execute_load_csv
    if name == "summarize_layer":
        return _execute_summarize_layer
    if name == "inspect_fields":
        return _execute_inspect_fields
    if name == "run_algorithm":
        return _execute_run_algorithm
    if name == "csv_to_points":
        return _execute_csv_to_points
    if name == "batch_reproject_layers":
        return _execute_batch_reproject_layers
    if name == "export_result":
        return _execute_export_result
    if is_semantic_action(name):
        return _execute_semantic_action
    return _execute_unknown


def _execute_discover_algorithms(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    query = str(action_input.get("query") or "")
    limit = int(action_input.get("limit") or 30)
    algorithms = toolbox.discover_algorithms(query, limit=limit)
    return Observation(status="success", message=f"Found {len(algorithms)} algorithms.", data={"algorithms": algorithms})


def _execute_algorithm_help(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    help_payload = toolbox.algorithm_help(str(action_input.get("algorithm_id") or ""))
    return Observation(status="success", message=f"Loaded algorithm help for {help_payload.get('id')}.", data={"algorithm": help_payload})


def _execute_load_vector(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    return toolbox.load_vector(str(action_input.get("input_path") or ""), name=str(action_input.get("name") or ""))


def _execute_load_raster(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    return toolbox.load_raster(str(action_input.get("input_path") or ""), name=str(action_input.get("name") or ""))


def _execute_load_csv(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    return toolbox.load_csv(str(action_input.get("input_path") or ""), name=str(action_input.get("name") or ""))


def _execute_summarize_layer(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    return toolbox.summarize_layer(
        str(action_input.get("layer_ref") or action_input.get("input_ref") or ""),
        detail_level=str(action_input.get("detail_level") or "summary"),
    )


def _execute_inspect_fields(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    return toolbox.summarize_layer(
        str(action_input.get("layer_ref") or action_input.get("input_ref") or ""),
        detail_level="fields",
    )


def _execute_run_algorithm(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    return toolbox.run_algorithm(
        str(action_input.get("algorithm_id") or ""),
        dict(action_input.get("parameters") or {}),
        output_name=str(action_input.get("output_name") or ""),
    )


def _execute_csv_to_points(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    return toolbox.csv_to_points(
        str(action_input.get("input_ref") or ""),
        x_field=str(action_input.get("x_field") or ""),
        y_field=str(action_input.get("y_field") or ""),
        crs=str(action_input.get("crs") or "EPSG:4326"),
        output_name=str(action_input.get("output_name") or ""),
        output_path=_explicit_output_path(action_input),
    )


def _execute_batch_reproject_layers(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    input_refs = action_input.get("input_refs")
    if isinstance(input_refs, list):
        refs = [str(item) for item in input_refs if str(item).strip()]
    else:
        refs = [str(input_refs or "").strip()]
    return toolbox.batch_reproject_layers(
        refs,
        target_crs=str(action_input.get("target_crs") or ""),
        output_name=str(action_input.get("output_name") or ""),
    )


def _execute_export_result(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    return toolbox.export_result(str(action_input.get("layer_ref") or ""), str(action_input.get("output_path") or ""))


def _execute_semantic_action(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    action = str(action_input.pop("__action") or "")
    algorithm_id, parameters, output_name = semantic_algorithm_call(action, action_input)
    return toolbox.run_algorithm(algorithm_id, parameters, output_name=output_name)


def _execute_unknown(toolbox: Any, action_input: dict[str, Any]) -> Observation:
    del toolbox
    return Observation(status="error", message="Unknown registered tool executor.", data={"action_input": action_input})


def _explicit_output_path(action_input: dict[str, Any]) -> str:
    value = str(action_input.get("output_path") or action_input.get("output") or "").strip()
    if value.upper() == "TEMPORARY_OUTPUT":
        return ""
    return value
