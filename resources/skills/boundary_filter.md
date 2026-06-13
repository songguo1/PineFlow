---
name: boundary_filter
description: "按边界筛选/裁剪：统一CRS后执行clip或extract_by_location。当用户需要按行政区域、研究区等边界筛选数据时使用。"
requires_toolkits: [vector_transform, vector_overlay]
workspace_attention:
  - candidate_layers
  - geometry_type
  - crs_consistency
  - spatial_extent
  - artifact_lineage
risk_awareness:
  - mixed_crs_overlay
  - invalid_boundary_geometry
  - extent_no_overlap
  - empty_spatial_result
strategy_guidance:
  - 只筛选位于边界内的要素时优先 extract_by_location。
  - 需要裁剪几何形状时使用 clip_layer。
  - CRS 不一致时先统一 CRS。
default_preferences:
  - 面图层名称匹配行政区/边界/范围时可作为 overlay_ref 候选。
analysis_hints:
  - 空结果优先检查 CRS、extent overlap 和边界几何有效性。
clarification_policy:
  - slot: overlay_ref
    hard_when: 多个边界候选置信度接近或没有面图层候选。
    soft_when: 唯一面边界候选与用户地名高度一致。
assumption_preferences:
  - 只筛选位于边界内的要素时优先 extract_by_location；需要裁剪几何形状时才用 clip_layer。
  - 两个图层 CRS 不一致时先统一 CRS，再做空间关系判断。
workspace_queries:
  - target: layers
    reason: 需要确认输入图层和边界图层的 geometry_type、CRS、feature_count。
    applies_to_slots: [input_ref, overlay_ref, intersect_ref]
  - target: artifacts
    reason: 复用前序缓冲区、边界修复结果或上一步筛选结果时优先从 artifact lineage 解析。
soft_clarification_hints:
  - slot: overlay_ref
    when: 当前工作区有多个面边界候选。
    strategy: 用名称、geometry_type、CRS 和 feature_count 排序；低置信时询问用户。
max_chars: 650
---

# 按边界筛选/裁剪

## 适用场景
用户需要按某个边界（行政区域、研究区等）筛选或裁剪数据。

## 操作步骤
1. 确保输入图层和目标边界图层的 CRS 一致
   - 使用 `inspect_workspace` 确认两个图层的 CRS
   - 如果 CRS 不一致，用 `reproject_layer` 统一
2. 根据需求选择工具：
   - 需要裁剪几何形状 → 使用 `clip_layer`
   - 只筛选边界内的要素（不修改几何）→ 使用 `extract_by_location`
3. 执行操作后验证：
   - 输出要素数量应 ≤ 输入要素数量
   - 如果输出为空，检查：CRS 是否一致、两个图层是否有空间重叠、边界图层是否有效
4. 导出结果
