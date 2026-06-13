---
name: csv_to_points
description: "CSV表格转点矢量图层：识别经纬度列、创建点、校验CRS。当用户提供了CSV或表格数据需要转成点图层时使用。"
requires_toolkits: [data_io]
workspace_attention:
  - csv_fields
  - coordinate_field_candidates
  - row_count
  - output_feature_count
risk_awareness:
  - missing_coordinate_fields
  - swapped_x_y_fields
  - invalid_coordinate_values
strategy_guidance:
  - 先加载 CSV，再根据字段名和样例判断 x_field/y_field。
  - 经纬度点图层默认 EPSG:4326；后续米制分析再重投影。
default_preferences:
  - lon/lng/longitude/x 可作为 x_field 高置信候选。
  - lat/latitude/y 可作为 y_field 高置信候选。
analysis_hints:
  - 转点后对比 CSV row_count 和输出 feature_count，明显减少时检查坐标缺失或解析失败。
clarification_policy:
  - slot: x_field
    hard_when: 无法稳定区分经度字段。
    soft_when: 唯一 lon/lng/longitude/x 候选存在。
  - slot: y_field
    hard_when: 无法稳定区分纬度字段。
    soft_when: 唯一 lat/latitude/y 候选存在。
assumption_preferences:
  - 经纬度字段明确时可默认 CRS 为 EPSG:4326。
  - 后续涉及米制距离时，CSV 转点后应先考虑重投影到合适投影坐标系。
workspace_queries:
  - target: fields
    reason: CSV 转点前必须确认 x_field/y_field 是否存在。
    applies_to_slots: [input_ref, x_field, y_field]
  - target: layer_summary
    reason: 转点后对比 row_count 与 feature_count，判断坐标缺失或解析失败。
soft_clarification_hints:
  - slot: x_field
    when: 存在 lon/lng/longitude/x 等多个候选。
    strategy: 优先用字段候选和样例判断；无法稳定区分时询问用户。
  - slot: y_field
    when: 存在 lat/latitude/y 等多个候选。
    strategy: 优先用字段候选和样例判断；无法稳定区分时询问用户。
max_chars: 600
---

# CSV 转点图层

## 适用场景
用户提供了 CSV 表格，需要根据经纬度坐标创建点矢量图层。

## 操作步骤
1. 使用 `load_csv` 加载 CSV 文件到当前工作空间
2. 检查 CSV 字段，识别经纬度列（常见列名：lat/lon/latitude/longitude/纬度/经度/x/y）
3. 使用 `csv_to_points` 工具，指定 x_field 和 y_field
4. 默认 CRS 为 EPSG:4326（WGS84 地理坐标系）
5. 如果后续操作需要米制距离（如缓冲），先用 `reproject_layer` 重投影到合适的投影坐标系（如 EPSG:3857 或 UTM 分带）
6. 验证输出要素数量与 CSV 行数是否一致
