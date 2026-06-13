"""GIS context checks that run before executing semantic actions."""

from __future__ import annotations

import re
from typing import Any

from pineflow_agent.core.field_metadata import field_names
from pineflow_agent.core.models import ActionPlan
from pineflow_agent.rules.rule_registry import RuleEvaluationContext, RuleRegistry
from pineflow_agent.tools.semantic.semantic_tools import normalize_semantic_input, _spatial_predicates
from pineflow_agent.core.state_tree import GISStateTree, LayerRecord
from pineflow_agent.policies.crs_recommendation import (
    DEFAULT_PROJECTED_CRS,
    explicit_crs_recommendation,
    recommend_projected_crs,
)
from pineflow_agent.policies.output_policy import output_overwrite_decision
from pineflow_agent.rules.validation import RepairProposal, ValidationIssue

GEOGRAPHIC_CRS_PREFIXES = {"EPSG:4326", "EPSG:4490", "CRS:84"}

def register_preflight_rule(
    name: str,
    *actions: str,
) -> Any:
    return RuleRegistry.register(
        name=name,
        stage="preflight",
        actions=tuple(str(action or "").strip() for action in actions if str(action or "").strip()),
    )


def preflight_rules():
    return tuple(rule for rule in RuleRegistry.default().rules if rule.stage == "preflight")


def preflight_semantic_action(
    action: str,
    action_input: dict[str, Any],
    state: GISStateTree,
) -> list[ValidationIssue]:
    normalized = normalize_semantic_input(action, action_input)
    return RuleRegistry.default().issues("preflight", ActionPlan("", action, normalized), state=state)


@register_preflight_rule("output_path_overwrite")
def _preflight_output_path_overwrite(context: RuleEvaluationContext) -> list[ValidationIssue]:
    output_path = str(context.action_input.get("output_path") or context.action_input.get("output") or "").strip()
    decision = output_overwrite_decision(
        output_path,
        action=context.plan.action,
        overwrite=bool(context.action_input.get("overwrite")),
    )
    if not decision.confirmation_required:
        return []
    params = decision.params
    return [
        ValidationIssue(
            code="output_exists",
            stage="preflight",
            severity="warning",
            message_key="preflight.output_exists",
            params=params,
            repair=RepairProposal(
                kind="confirm_action",
                message_key="repair.confirm_output_overwrite",
                params=params,
                patch={"overwrite": True},
                requires_confirmation=True,
            ),
        )
    ]


@register_preflight_rule("buffer_requires_projected_crs", "buffer_layer")
def _preflight_buffer(context: RuleEvaluationContext) -> list[ValidationIssue]:
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    layer_ref = str(context.action_input.get("input_ref") or "").strip()
    layer = _resolve_layer(layer_ref, state)
    if layer is None:
        return [_unknown_layer_issue(layer_ref)]

    crs = _layer_crs(layer)
    if not crs:
        return [_unknown_crs_issue([layer], action="buffer_layer")]
    if not _is_geographic_crs(crs):
        return []

    recommendation = recommend_projected_crs(layer, task_type="buffer")
    target_crs = str(context.action_input.get("target_crs") or recommendation.target_crs or DEFAULT_PROJECTED_CRS)
    output_name = f"{layer.name}_projected"
    params = {
        "layer": layer.name,
        "crs": crs or "unknown",
        "unit": str(context.action_input.get("unit") or "meter"),
        "target_crs": target_crs,
        "crs_recommendation": recommendation.to_dict(),
    }
    repair_action = {
        "action": "reproject_layer",
        "action_input": {
            "input_ref": layer.layer_id,
            "target_crs": target_crs,
            "output_name": output_name,
        },
    }
    patch = {
        "input_ref": output_name,
        "target_crs": None,
    }
    return [
        ValidationIssue(
            code="distance_requires_projected_crs",
            stage="preflight",
            severity="error",
            message_key="preflight.buffer.geographic_crs",
            params=params,
            repair=RepairProposal(
                kind="confirm_action",
                message_key="repair.reproject_before_buffer",
                params=params,
                action=repair_action,
                patch=patch,
                requires_confirmation=True,
            ),
        )
    ]


@register_preflight_rule("clip_crs_alignment", "clip_layer")
def _preflight_clip(context: RuleEvaluationContext) -> list[ValidationIssue]:
    return _preflight_overlay_pair(
        context,
        input_slot="input_ref",
        overlay_slot="overlay_ref",
        repair_patch_slot="overlay_ref",
    )


@register_preflight_rule("overlay_crs_alignment", "intersect_layer", "difference_layer")
def _preflight_overlay_analysis(context: RuleEvaluationContext) -> list[ValidationIssue]:
    return _preflight_overlay_pair(
        context,
        input_slot="input_ref",
        overlay_slot="overlay_ref",
        repair_patch_slot="overlay_ref",
    )


@register_preflight_rule("location_crs_alignment", "extract_by_location")
def _preflight_extract_by_location(context: RuleEvaluationContext) -> list[ValidationIssue]:
    return _preflight_overlay_pair(
        context,
        input_slot="input_ref",
        overlay_slot="intersect_ref",
        repair_patch_slot="intersect_ref",
    )


@register_preflight_rule("join_location_crs_alignment", "join_by_location")
def _preflight_join_by_location(context: RuleEvaluationContext) -> list[ValidationIssue]:
    issues = _preflight_overlay_pair(
        context,
        input_slot="input_ref",
        overlay_slot="join_ref",
        repair_patch_slot="join_ref",
    )
    if issues:
        return issues
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    join_layer = _resolve_layer(str(context.action_input.get("join_ref") or "").strip(), state)
    if join_layer is None:
        return [_unknown_layer_issue(str(context.action_input.get("join_ref") or "").strip())]
    missing = _missing_fields(join_layer, list(context.action_input.get("join_fields") or []))
    if missing:
        return [_unknown_field_issue(join_layer, missing)]
    predicate_issues = _spatial_predicate_geometry_issues(context, overlay_slot="join_ref")
    if predicate_issues:
        return predicate_issues
    return []


@register_preflight_rule("join_nearest_alignment", "join_by_nearest")
def _preflight_join_by_nearest(context: RuleEvaluationContext) -> list[ValidationIssue]:
    issues = _preflight_overlay_pair(
        context,
        input_slot="input_ref",
        overlay_slot="join_ref",
        repair_patch_slot="join_ref",
    )
    if issues:
        return issues
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    join_layer = _resolve_layer(str(context.action_input.get("join_ref") or "").strip(), state)
    if join_layer is None:
        return [_unknown_layer_issue(str(context.action_input.get("join_ref") or "").strip())]
    missing = _missing_fields(join_layer, list(context.action_input.get("join_fields") or []))
    if missing:
        return [_unknown_field_issue(join_layer, missing)]
    return []


@register_preflight_rule("count_points_alignment", "count_points_in_polygon")
def _preflight_count_points_in_polygon(context: RuleEvaluationContext) -> list[ValidationIssue]:
    return _preflight_overlay_pair(
        context,
        input_slot="polygon_ref",
        overlay_slot="point_ref",
        repair_patch_slot="point_ref",
    )


@register_preflight_rule("attribute_field_exists", "extract_by_attribute")
def _preflight_extract_by_attribute(context: RuleEvaluationContext) -> list[ValidationIssue]:
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    layer = _resolve_layer(str(context.action_input.get("input_ref") or "").strip(), state)
    if layer is None:
        return [_unknown_layer_issue(str(context.action_input.get("input_ref") or "").strip())]
    field = str(context.action_input.get("field") or "").strip()
    missing = _missing_fields(layer, [field])
    if missing:
        return [_unknown_field_issue(layer, missing)]
    return []


@register_preflight_rule("expression_fields_exist", "select_by_expression")
def _preflight_select_by_expression(context: RuleEvaluationContext) -> list[ValidationIssue]:
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    layer = _resolve_layer(str(context.action_input.get("input_ref") or "").strip(), state)
    if layer is None:
        return [_unknown_layer_issue(str(context.action_input.get("input_ref") or "").strip())]
    referenced = _formula_field_refs(str(context.action_input.get("expression") or "").strip())
    if referenced:
        missing = _missing_fields(layer, referenced)
        if missing:
            return [_unknown_field_issue(layer, missing)]
    return []


@register_preflight_rule("field_calculator_input_exists", "field_calculator")
def _preflight_field_calculator(context: RuleEvaluationContext) -> list[ValidationIssue]:
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    layer = _resolve_layer(str(context.action_input.get("input_ref") or "").strip(), state)
    if layer is None:
        return [_unknown_layer_issue(str(context.action_input.get("input_ref") or "").strip())]
    referenced = _formula_field_refs(str(context.action_input.get("formula") or "").strip())
    if referenced:
        missing = _missing_fields(layer, referenced)
        if missing:
            return [_unknown_field_issue(layer, missing)]
    return []


@register_preflight_rule("extract_location_predicate_geometry", "extract_by_location")
def _preflight_extract_by_location_geometry(context: RuleEvaluationContext) -> list[ValidationIssue]:
    return _spatial_predicate_geometry_issues(context, overlay_slot="intersect_ref")


@register_preflight_rule("summarize_layer_exists", "summarize_layer", "inspect_fields")
def _preflight_summarize_layer(context: RuleEvaluationContext) -> list[ValidationIssue]:
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    layer_ref = str(context.action_input.get("layer_ref") or context.action_input.get("input_ref") or "").strip()
    if _resolve_layer(layer_ref, state) is None:
        return [_unknown_layer_issue(layer_ref)]
    return []


@register_preflight_rule("csv_coordinate_fields_exist", "csv_to_points")
def _preflight_csv_to_points(context: RuleEvaluationContext) -> list[ValidationIssue]:
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    layer = _resolve_layer(str(context.action_input.get("input_ref") or "").strip(), state)
    if layer is None:
        return [_unknown_layer_issue(str(context.action_input.get("input_ref") or "").strip())]
    if layer.kind != "table":
        params = {"layer": layer.name}
        return [
            ValidationIssue(
                code="not_csv_table",
                stage="preflight",
                severity="error",
                message_key="preflight.not_csv_table",
                params=params,
                repair=RepairProposal(kind="ask_user", message_key="preflight.not_csv_table", params=params),
            )
        ]
    if bool((layer.metadata or {}).get("suspected_encoding_issue")):
        params = {
            "layer": layer.name,
            "encoding": str((layer.metadata or {}).get("encoding") or "<unknown>"),
            "fields": ", ".join(list((layer.metadata or {}).get("suspicious_fields") or [])) or "<none>",
        }
        message = (
            f"Loaded CSV table {layer.name} appears to have garbled field names. "
            f"Detected encoding: {params['encoding']}. Suspicious fields: {params['fields']}. "
            "Confirm the CSV encoding before running point creation or field-based GIS operations."
        )
        return [
            ValidationIssue(
                code="csv_encoding_issue",
                stage="preflight",
                severity="error",
                message_key=message,
                params=params,
                repair=RepairProposal(kind="ask_user", message_key=message, params=params),
            )
        ]
    missing = _missing_fields(layer, [context.action_input.get("x_field"), context.action_input.get("y_field")])
    if missing:
        return [_unknown_field_issue(layer, missing)]
    return []


def _resolve_layer(layer_ref: str, state: GISStateTree) -> LayerRecord | None:
    try:
        return state.resolve(layer_ref)
    except KeyError:
        return None


def _layer_crs(layer: LayerRecord) -> str:
    return str((layer.metadata or {}).get("crs") or "").strip()


def _is_geographic_crs(crs: str) -> bool:
    text = str(crs or "").strip().upper()
    if text in GEOGRAPHIC_CRS_PREFIXES:
        return True
    return "WGS 84" in text or "GEOGRAPHIC" in text or "LONGITUDE" in text


def _unknown_layer_issue(layer_ref: str) -> ValidationIssue:
    params = {"layer": layer_ref or "<empty>"}
    return ValidationIssue(
        code="unknown_layer",
        stage="preflight",
        severity="error",
        message_key="preflight.unknown_layer",
        params=params,
        repair=RepairProposal(kind="ask_user", message_key="preflight.unknown_layer", params=params),
    )


def _preflight_overlay_pair(
    context: RuleEvaluationContext,
    *,
    input_slot: str,
    overlay_slot: str,
    repair_patch_slot: str,
) -> list[ValidationIssue]:
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    input_ref = str(context.action_input.get(input_slot) or "").strip()
    overlay_ref = str(context.action_input.get(overlay_slot) or "").strip()
    input_layer = _resolve_layer(input_ref, state)
    overlay_layer = _resolve_layer(overlay_ref, state)
    if input_layer is None:
        return [_unknown_layer_issue(input_ref)]
    if overlay_layer is None:
        return [_unknown_layer_issue(overlay_ref)]

    input_crs = _layer_crs(input_layer)
    overlay_crs = _layer_crs(overlay_layer)
    unknown_layers = [layer for layer, crs in ((input_layer, input_crs), (overlay_layer, overlay_crs)) if not crs]
    if unknown_layers:
        return [_unknown_crs_issue(unknown_layers, action=context.plan.action, input_layer=input_layer, overlay_layer=overlay_layer)]
    if input_crs == overlay_crs:
        return []

    output_name = f"{overlay_layer.name}_to_{input_layer.name}_crs"
    recommendation = explicit_crs_recommendation(
        target_crs=input_crs,
        confidence="high",
        reason=(
            f"Spatial overlay requires both layers to share one CRS. "
            f"Using the primary input CRS {input_crs} keeps downstream geometry operations aligned."
        ),
        source="input_layer_crs",
        alternatives=[
            {
                "target_crs": overlay_crs,
                "reason": "You could also reproject the primary input layer instead, but keeping the main input unchanged is the safer default.",
                "source": "overlay_layer_crs",
            }
        ],
        requires_confirmation=True,
    )
    params = {
        "input_layer": input_layer.name,
        "overlay_layer": overlay_layer.name,
        "input_crs": input_crs,
        "overlay_crs": overlay_crs,
        "target_crs": input_crs,
        "crs_recommendation": recommendation.to_dict(),
    }
    repair_action = {
        "action": "reproject_layer",
        "action_input": {
            "input_ref": overlay_layer.layer_id,
            "target_crs": input_crs,
            "output_name": output_name,
        },
    }
    patch = {repair_patch_slot: output_name}
    return [
        ValidationIssue(
            code="overlay_crs_mismatch",
            stage="preflight",
            severity="warning",
            message_key="preflight.clip.crs_mismatch",
            params=params,
            repair=RepairProposal(
                kind="confirm_action",
                message_key="repair.reproject_overlay_for_clip",
                params=params,
                action=repair_action,
                patch=patch,
                requires_confirmation=True,
            ),
        )
    ]


def _layer_fields(layer: LayerRecord) -> list[str]:
    return field_names(dict(layer.metadata or {}))


def _missing_fields(layer: LayerRecord, fields: list[Any]) -> list[str]:
    available = {field.lower() for field in _layer_fields(layer)}
    missing = []
    for field in fields:
        text = str(field or "").strip()
        if text and text.lower() not in available:
            missing.append(text)
    return missing


def _formula_field_refs(formula: str) -> list[str]:
    if not formula:
        return []
    refs: list[str] = []
    for match in re.finditer(r'"([^"]+)"', formula):
        name = str(match.group(1) or "").replace('""', '"').strip()
        if name:
            refs.append(name)
    return list(dict.fromkeys(refs))


def _geometry_family(layer: LayerRecord) -> str:
    geometry = str((layer.metadata or {}).get("geometry_type") or "").strip().lower()
    if "point" in geometry:
        return "point"
    if "line" in geometry:
        return "line"
    if "polygon" in geometry:
        return "polygon"
    return ""


def _single_spatial_predicate_name(value: Any) -> str:
    try:
        predicates = _spatial_predicates(value)
    except ValueError:
        return ""
    if len(predicates) != 1:
        return ""
    reverse = {
        0: "intersects",
        1: "contains",
        2: "disjoint",
        3: "equals",
        4: "touches",
        5: "overlaps",
        6: "within",
        7: "crosses",
    }
    return reverse.get(predicates[0], "")


def _suggested_predicates_for_geometry(predicate: str, *, input_family: str, overlay_family: str) -> list[str]:
    if predicate == "contains":
        if input_family == "point" and overlay_family in {"line", "polygon"}:
            return ["within", "intersects"]
        return ["intersects"]
    if predicate == "within":
        if overlay_family == "point" and input_family in {"line", "polygon"}:
            return ["contains", "intersects"]
        return ["intersects"]
    if predicate == "overlaps":
        if input_family == "point" and overlay_family == "polygon":
            return ["within", "intersects"]
        if input_family == "polygon" and overlay_family == "point":
            return ["contains", "intersects"]
        return ["intersects"]
    if predicate == "crosses":
        return ["intersects", "touches"]
    return ["intersects"]


def _predicate_geometry_params(
    predicate: str,
    *,
    input_layer: LayerRecord,
    overlay_layer: LayerRecord,
    input_family: str,
    overlay_family: str,
) -> dict[str, Any]:
    return {
        "predicate": predicate,
        "input_layer": input_layer.name,
        "overlay_layer": overlay_layer.name,
        "input_geometry": input_family or "unknown",
        "overlay_geometry": overlay_family or "unknown",
        "suggested_predicates": _suggested_predicates_for_geometry(
            predicate,
            input_family=input_family,
            overlay_family=overlay_family,
        ),
    }


def _spatial_predicate_geometry_issue(
    predicate: str,
    *,
    input_layer: LayerRecord,
    overlay_layer: LayerRecord,
    input_family: str,
    overlay_family: str,
) -> ValidationIssue:
    params = _predicate_geometry_params(
        predicate,
        input_layer=input_layer,
        overlay_layer=overlay_layer,
        input_family=input_family,
        overlay_family=overlay_family,
    )
    return ValidationIssue(
        code="spatial_predicate_geometry_mismatch",
        stage="preflight",
        severity="error",
        message_key="preflight.spatial_predicate_geometry_mismatch",
        params=params,
        repair=RepairProposal(
            kind="ask_user",
            message_key="preflight.spatial_predicate_geometry_mismatch",
            params=params,
        ),
    )


def _spatial_predicate_geometry_issues(
    context: RuleEvaluationContext,
    *,
    overlay_slot: str,
) -> list[ValidationIssue]:
    state = context.state
    if not isinstance(state, GISStateTree):
        return []
    predicate = _single_spatial_predicate_name(context.action_input.get("predicate"))
    if not predicate:
        return []
    input_layer = _resolve_layer(str(context.action_input.get("input_ref") or "").strip(), state)
    overlay_layer = _resolve_layer(str(context.action_input.get(overlay_slot) or "").strip(), state)
    if input_layer is None or overlay_layer is None:
        return []
    input_family = _geometry_family(input_layer)
    overlay_family = _geometry_family(overlay_layer)
    if not input_family or not overlay_family:
        return []

    if predicate == "contains" and input_family == "point" and overlay_family in {"line", "polygon"}:
        return [
            _spatial_predicate_geometry_issue(
                predicate,
                input_layer=input_layer,
                overlay_layer=overlay_layer,
                input_family=input_family,
                overlay_family=overlay_family,
            )
        ]
    if predicate == "within" and overlay_family == "point" and input_family in {"line", "polygon"}:
        return [
            _spatial_predicate_geometry_issue(
                predicate,
                input_layer=input_layer,
                overlay_layer=overlay_layer,
                input_family=input_family,
                overlay_family=overlay_family,
            )
        ]
    if predicate == "overlaps" and input_family != overlay_family:
        return [
            _spatial_predicate_geometry_issue(
                predicate,
                input_layer=input_layer,
                overlay_layer=overlay_layer,
                input_family=input_family,
                overlay_family=overlay_family,
            )
        ]
    if predicate == "crosses" and {input_family, overlay_family} in ({"point"}, {"polygon"}):
        return [
            _spatial_predicate_geometry_issue(
                predicate,
                input_layer=input_layer,
                overlay_layer=overlay_layer,
                input_family=input_family,
                overlay_family=overlay_family,
            )
        ]
    return []


def _unknown_field_issue(layer: LayerRecord, missing_fields: list[str]) -> ValidationIssue:
    params = {
        "layer": layer.name,
        "fields": missing_fields,
        "available_fields": _layer_fields(layer),
    }
    return ValidationIssue(
        code="unknown_field",
        stage="preflight",
        severity="error",
        message_key="preflight.unknown_field",
        params=params,
        repair=RepairProposal(kind="ask_user", message_key="preflight.unknown_field", params=params),
    )


def _unknown_crs_issue(
    layers: list[LayerRecord],
    *,
    action: str,
    input_layer: LayerRecord | None = None,
    overlay_layer: LayerRecord | None = None,
) -> ValidationIssue:
    layer_names = [layer.name for layer in layers if layer.name]
    params = {
        "action": action,
        "layers": layer_names,
        "input_layer": input_layer.name if input_layer else "",
        "overlay_layer": overlay_layer.name if overlay_layer else "",
    }
    message = (
        "Cannot safely run spatial analysis because one or more input layers have unknown CRS: "
        f"{', '.join(layer_names) or '<unknown>'}. Confirm or define the CRS before continuing."
    )
    return ValidationIssue(
        code="unknown_crs",
        stage="preflight",
        severity="error",
        message_key=message,
        params=params,
        repair=RepairProposal(kind="ask_user", message_key=message, params=params),
    )


@register_preflight_rule(
    "raster_inputs",
    "reproject_raster",
    "clip_raster_by_mask",
    "clip_raster_by_extent",
    "raster_calculator",
    "zonal_statistics",
    "raster_sampling",
    "rasterize_vector",
    "polygonize_raster",
    "slope",
    "aspect",
    "hillshade",
    "contour",
    "reclassify_raster",
    "terrain_ruggedness_index",
    "topographic_position_index",
    "roughness",
)
def _preflight_raster_inputs(context: RuleEvaluationContext) -> list[ValidationIssue]:
    state = context.state
    if not isinstance(state, GISStateTree):
        return []

    action = context.plan.action

    issues: list[ValidationIssue] = []

    # Check primary raster input exists
    input_ref = str(context.action_input.get("input_ref") or "").strip()
    if input_ref:
        layer = _resolve_layer(input_ref, state)
        if layer is None:
            return [_unknown_layer_issue(input_ref)]
        if layer.kind != "raster" and action in _RASTER_INPUT_ACTIONS:
            return [_layer_kind_mismatch_issue(input_ref, "raster", layer.kind)]

    # Check secondary raster ref (zonal_statistics, raster_sampling)
    raster_ref = str(context.action_input.get("raster_ref") or "").strip()
    if raster_ref:
        rlayer = _resolve_layer(raster_ref, state)
        if rlayer is None:
            return [_unknown_layer_issue(raster_ref)]
        if rlayer.kind != "raster":
            return [_layer_kind_mismatch_issue(raster_ref, "raster", rlayer.kind)]

    # Check multiple raster refs (raster_calculator)
    raster_refs = list(context.action_input.get("raster_refs") or [])
    for ref in raster_refs:
        ref_text = str(ref or "").strip()
        if not ref_text:
            continue
        rlayer = _resolve_layer(ref_text, state)
        if rlayer is None:
            return [_unknown_layer_issue(ref_text)]
        if rlayer.kind != "raster":
            return [_layer_kind_mismatch_issue(ref_text, "raster", rlayer.kind)]

    # CRS check: operations with two rasters should have matching CRS
    if action == "raster_calculator" and len(raster_refs) >= 2:
        layers = [
            layer
            for ref in raster_refs
            for layer in [_resolve_layer(str(ref or "").strip(), state)]
            if layer is not None
        ]
        blocking, warnings = _multi_raster_alignment_issues(layers, action)
        if blocking:
            return blocking
        issues.extend(warnings)
        issues.extend(_raster_nodata_warnings(layers, action))

    if action in {"zonal_statistics", "raster_sampling"} and raster_ref:
        layer = _resolve_layer(raster_ref, state)
        if layer is not None:
            issues.extend(_raster_nodata_warnings([layer], action))

    if action == "reproject_raster" and input_ref:
        layer = _resolve_layer(input_ref, state)
        if layer is not None:
            warning = _resampling_recommendation_warning(layer, context)
            if warning is not None:
                issues.append(warning)

    return issues


_RASTER_INPUT_ACTIONS = {
    "reproject_raster",
    "clip_raster_by_mask",
    "clip_raster_by_extent",
    "polygonize_raster",
    "slope",
    "aspect",
    "hillshade",
    "contour",
    "reclassify_raster",
    "terrain_ruggedness_index",
    "topographic_position_index",
    "roughness",
}


def _layer_kind_mismatch_issue(layer_ref: str, expected: str, actual: str) -> ValidationIssue:
    params = {"layer": layer_ref, "kind": actual, "expected_kind": expected}
    return ValidationIssue(
        code="layer_kind_mismatch",
        stage="preflight",
        severity="error",
        message_key=f"Layer {layer_ref} is {actual}, but this tool expects {expected}.",
        params=params,
        repair=RepairProposal(kind="ask_user", message_key=f"Layer {layer_ref} is {actual}, expected {expected}.", params=params),
    )


def _multi_raster_crs_mismatch(layers: list[LayerRecord | None], action: str, crs_list: list[str]) -> ValidationIssue:
    names = [l.name if l else "?" for l in layers]
    params = {"action": action, "layers": names, "crs_a": crs_list[0], "crs_b": crs_list[1]}
    message = f"Raster layers {names[0]} (CRS: {crs_list[0]}) and {names[1]} (CRS: {crs_list[1]}) have different CRS. Raster calculator expects matching CRS."
    return ValidationIssue(
        code="raster_crs_mismatch",
        stage="preflight",
        severity="error",
        message_key=message,
        params=params,
        repair=RepairProposal(kind="ask_user", message_key=message, params=params),
    )


def _multi_raster_alignment_issues(
    layers: list[LayerRecord],
    action: str,
) -> tuple[list[ValidationIssue], list[ValidationIssue]]:
    blocking: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    for index, left in enumerate(layers):
        for right in layers[index + 1:]:
            left_crs = _layer_crs(left)
            right_crs = _layer_crs(right)
            if left_crs and right_crs and left_crs != right_crs:
                blocking.append(_multi_raster_crs_mismatch([left, right], action, [left_crs, right_crs]))
                continue

            left_extent = _layer_extent(left)
            right_extent = _layer_extent(right)
            if left_extent and right_extent:
                if not _extents_overlap(left_extent, right_extent):
                    blocking.append(_raster_extent_issue(left, right, action))
                    continue
                if not _same_extent(left_extent, right_extent):
                    warnings.append(_raster_extent_warning(left, right, action))

            left_pixel = _pixel_size(left)
            right_pixel = _pixel_size(right)
            if left_pixel and right_pixel and not _same_pixel_size(left_pixel, right_pixel):
                warnings.append(_raster_pixel_size_warning(left, right, action, left_pixel, right_pixel))
    return blocking, _dedupe_issues(warnings)


def _raster_extent_issue(left: LayerRecord, right: LayerRecord, action: str) -> ValidationIssue:
    params = {"action": action, "layers": [left.name, right.name]}
    message = (
        f"Raster layers {left.name} and {right.name} do not overlap. "
        "Raster calculator would produce an empty or invalid aligned result."
    )
    return ValidationIssue(
        code="raster_extent_no_overlap",
        stage="preflight",
        severity="error",
        message_key=message,
        params=params,
        repair=RepairProposal(kind="ask_user", message_key=message, params=params),
    )


def _raster_extent_warning(left: LayerRecord, right: LayerRecord, action: str) -> ValidationIssue:
    params = {"action": action, "layers": [left.name, right.name]}
    message = (
        f"Raster layers {left.name} and {right.name} overlap but have different extents. "
        "Cells outside the shared area may become NoData."
    )
    return ValidationIssue(
        code="raster_extent_partial_overlap",
        stage="preflight",
        severity="warning",
        message_key=message,
        params=params,
    )


def _raster_pixel_size_warning(
    left: LayerRecord,
    right: LayerRecord,
    action: str,
    left_pixel: tuple[float, float],
    right_pixel: tuple[float, float],
) -> ValidationIssue:
    params = {
        "action": action,
        "layers": [left.name, right.name],
        "pixel_size_a": list(left_pixel),
        "pixel_size_b": list(right_pixel),
    }
    message = (
        f"Raster layers {left.name} and {right.name} use different pixel sizes "
        f"({left_pixel[0]} x {left_pixel[1]} vs {right_pixel[0]} x {right_pixel[1]}). "
        "QGIS may resample one raster during calculation."
    )
    return ValidationIssue(
        code="raster_pixel_size_mismatch",
        stage="preflight",
        severity="warning",
        message_key=message,
        params=params,
    )


def _raster_nodata_warnings(layers: list[LayerRecord], action: str) -> list[ValidationIssue]:
    warnings: list[ValidationIssue] = []
    for layer in layers:
        nodata = _nodata_value(layer)
        if nodata is None:
            continue
        params = {"action": action, "layer": layer.name, "nodata": nodata}
        message = (
            f"Raster layer {layer.name} has NoData value {nodata}. "
            "Raster calculations and sampling may propagate NoData into the output."
        )
        warnings.append(
            ValidationIssue(
                code="raster_nodata_propagation",
                stage="preflight",
                severity="warning",
                message_key=message,
                params=params,
            )
        )
    return warnings


def _resampling_recommendation_warning(layer: LayerRecord, context: RuleEvaluationContext) -> ValidationIssue | None:
    if "resampling" in dict(context.plan.action_input or {}):
        return None
    recommendation = _recommended_resampling(layer)
    if recommendation is None:
        return None
    params = {
        "action": context.plan.action,
        "layer": layer.name,
        "data_type": str((layer.metadata or {}).get("data_type") or ""),
        "recommended_resampling": recommendation,
    }
    message = (
        f"Raster layer {layer.name} appears to be {recommendation['data_semantics']} data. "
        f"Recommended reprojection resampling is {recommendation['method']}."
    )
    return ValidationIssue(
        code="raster_resampling_recommendation",
        stage="preflight",
        severity="warning",
        message_key=message,
        params=params,
    )


def _recommended_resampling(layer: LayerRecord) -> dict[str, Any] | None:
    metadata = dict(layer.metadata or {})
    data_type = str(metadata.get("data_type") or metadata.get("dtype") or "").lower()
    if not data_type:
        bands = metadata.get("bands")
        if isinstance(bands, list) and bands and isinstance(bands[0], dict):
            data_type = str(bands[0].get("data_type") or bands[0].get("source_data_type") or "").lower()
    if not data_type:
        return None
    if any(token in data_type for token in ("float", "double", "real")):
        return {"method": "bilinear", "value": 1, "data_semantics": "continuous"}
    if any(token in data_type for token in ("byte", "int", "uint")):
        return {"method": "nearest", "value": 0, "data_semantics": "categorical or discrete"}
    return None


def _pixel_size(layer: LayerRecord) -> tuple[float, float] | None:
    value = (layer.metadata or {}).get("pixel_size") or (layer.metadata or {}).get("resolution")
    if isinstance(value, dict):
        x = _to_float(value.get("x") or value.get("x_size") or value.get("width"))
        y = _to_float(value.get("y") or value.get("y_size") or value.get("height"))
    elif isinstance(value, (list, tuple)) and len(value) >= 2:
        x = _to_float(value[0])
        y = _to_float(value[1])
    else:
        x = _to_float(value)
        y = x
    if x is None or y is None:
        return None
    return (abs(x), abs(y))


def _layer_extent(layer: LayerRecord) -> tuple[float, float, float, float] | None:
    extent = (layer.metadata or {}).get("extent")
    if isinstance(extent, dict):
        xmin = _to_float(_first_present(extent, ("xmin", "x_min", "minx")))
        ymin = _to_float(_first_present(extent, ("ymin", "y_min", "miny")))
        xmax = _to_float(_first_present(extent, ("xmax", "x_max", "maxx")))
        ymax = _to_float(_first_present(extent, ("ymax", "y_max", "maxy")))
    elif isinstance(extent, (list, tuple)) and len(extent) >= 4:
        xmin = _to_float(extent[0])
        ymin = _to_float(extent[1])
        xmax = _to_float(extent[2])
        ymax = _to_float(extent[3])
    else:
        return None
    if None in (xmin, ymin, xmax, ymax):
        return None
    return (min(xmin, xmax), min(ymin, ymax), max(xmin, xmax), max(ymin, ymax))


def _extents_overlap(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    return left[0] < right[2] and right[0] < left[2] and left[1] < right[3] and right[1] < left[3]


def _same_extent(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> bool:
    return all(abs(a - b) <= 1e-9 for a, b in zip(left, right))


def _same_pixel_size(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return abs(left[0] - right[0]) <= 1e-9 and abs(left[1] - right[1]) <= 1e-9


def _nodata_value(layer: LayerRecord) -> Any:
    metadata = dict(layer.metadata or {})
    nodata = metadata.get("nodata")
    if nodata is None:
        bands = metadata.get("bands")
        if isinstance(bands, list):
            values = [band.get("nodata") for band in bands if isinstance(band, dict) and band.get("nodata") is not None]
            if values:
                return values
    return nodata


def _first_present(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe_issues(issues: list[ValidationIssue]) -> list[ValidationIssue]:
    seen: set[tuple[str, str]] = set()
    result: list[ValidationIssue] = []
    for issue in issues:
        key = (issue.code, issue.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result
