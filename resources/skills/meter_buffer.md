---
name: meter_buffer
description: "米制距离缓冲：确保在投影坐标系下执行缓冲。当用户要求以米或公里为单位做缓冲区时，必须先加载此技能。"
requires_toolkits: [vector_transform, vector_analysis]
workspace_attention:
  - input_crs
  - layer_extent
  - geometry_type
  - projected_crs_recommendation
risk_awareness:
  - geographic_crs_metric_buffer
  - low_confidence_crs_fallback
  - invalid_or_empty_input_layer
strategy_guidance:
  - 米制缓冲前优先确认输入 CRS；地理坐标系先重投影再缓冲。
  - 小范围分析优先本地投影 CRS；无法判断时 EPSG:3857 只能作为低置信 fallback。
default_preferences:
  - dissolve 默认 false，除非用户明确要求合并缓冲区。
analysis_hints:
  - 缓冲后检查输出几何应为 Polygon/MultiPolygon，feature_count 不应异常为 0。
clarification_policy:
  - slot: distance
    hard_when: 用户表达缓冲意图但没有提供距离。
  - slot: target_crs
    soft_when: CRS 推荐高置信。
    hard_when: CRS 推荐低置信且结果用于精确测量。
assumption_preferences:
  - 输入图层是地理坐标系时，优先根据 extent 推荐本地投影 CRS，再缓冲。
  - 无法判断本地投影时可使用低置信 EPSG:3857，并把假设写入风险说明。
workspace_queries:
  - target: crs
    reason: 米制缓冲前必须知道输入图层 CRS 和 extent。
    applies_to_slots: [input_ref, distance, target_crs]
  - target: layer_summary
    reason: 缓冲后检查输出 geometry_type 和 feature_count。
soft_clarification_hints:
  - slot: distance
    when: 用户表达了缓冲意图但没有给距离。
    strategy: 缺距离属于 hard missing slot，必须进入 pending。
  - slot: target_crs
    when: CRS 推荐置信度低或 extent 不足。
    strategy: 可先询问用户确认投影选择；高置信推荐可继续并记录假设。
max_chars: 700
---

# 米制距离缓冲

## 适用场景
用户要求以米或千米为单位对图层做缓冲区。

## 关键规则
- **不要在 EPSG:4326（地理坐标系/经纬度）上直接做米制缓冲**。EPSG:4326 的单位是度，不是米，结果会严重错误。
- 缓冲前必须确保图层使用投影坐标系（单位是米）。

## 操作步骤
1. 检查当前图层的 CRS
2. 如果 CRS 是地理坐标系（如 EPSG:4326），先用 `reproject_layer` 重投影：
   - 小范围区域 → 使用对应的 UTM 分带（如 EPSG:32650 对应 6 度带 50）
   - 全球或大范围 → 使用 EPSG:3857（Web Mercator）
   - 中国区域 → 可使用 EPSG:3857 或对应的 CGCS2000 投影
3. 重投影成功后，使用 `buffer_layer` 执行缓冲
4. 距离参数使用正数，单位设为 "meter" 或 "kilometer"
5. 如果需要导出，结果可以重投影回原始 CRS
