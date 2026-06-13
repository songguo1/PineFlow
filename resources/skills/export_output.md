---
name: export_output
description: "GIS结果导出：选择 .gpkg/.shp/.geojson 等格式，判断什么时候需要显式 output_path，什么时候确认覆盖风险，并使用 export_result 稳定导出最终结果。当用户要导出、保存、落盘 GIS 结果时使用。"
requires_toolkits: [data_io]
workspace_attention:
  - final_artifacts
  - latest_outputs
  - output_path
  - overwrite_risk
risk_awareness:
  - output_overwrite
  - shapefile_field_name_limit
  - ambiguous_final_layer
strategy_guidance:
  - 显式导出时使用 export_result，而不是把每个中间步骤都写成用户路径。
  - 优先导出 final artifact；没有 final artifact 时选择最相关的 latest_result。
default_preferences:
  - 未指定格式时优先 GeoPackage (.gpkg)。
analysis_hints:
  - 导出后确认输出 artifact role=final，并记录源图层、路径、CRS、feature_count。
clarification_policy:
  - slot: output_path
    hard_when: 用户明确要求导出但没有完整目标路径。
  - slot: overwrite
    hard_when: 目标文件已存在。
assumption_preferences:
  - 用户只说导出但没指定格式时，优先建议 .gpkg。
  - 普通中间处理结果不追问路径，只有显式导出才要求 output_path。
workspace_queries:
  - target: artifacts
    reason: 导出前先确认最相关的 final/intermediate artifact 和 source layer。
    applies_to_slots: [layer_ref, output_path]
  - target: outputs
    reason: 导出后检查最终文件、feature_count、CRS 和 geometry_type。
soft_clarification_hints:
  - slot: output_path
    when: 用户明确要求导出但没有给完整路径。
    strategy: 进入 pending 询问路径；不要把 .gpkg 当完整路径。
  - slot: overwrite
    when: 目标文件已存在。
    strategy: 进入 confirmation，不能静默覆盖。
max_chars: 650
---

# GIS 结果导出

## 关键规则
- 普通中间处理结果默认用临时输出，不要为了每一步都追问路径
- 用户明确要求导出、保存到文件、指定格式时，再使用 `export_result`
- 默认优先 `gpkg`；只有用户明确要求或兼容性需要时再用 `shp`
- 已有同名输出时，先确认覆盖风险

## 格式选择
- `gpkg`：首选，字段名和编码限制少，适合日常 GIS 结果
- `geojson`：适合交换和 Web 场景，但字段类型和体积上不如 `gpkg`
- `shp`：兼容老系统，但字段名、编码和文件组织限制较多

## 操作顺序
1. 确认要导出的最终图层
2. 如果用户没给路径但明确要求导出，主动追问完整 `output_path`
3. 使用 `export_result`
4. 导出后确认：
   - 文件格式是否符合要求
   - 要素数量是否与源图层一致
   - 如果导出为 `shp`，注意路径和字段限制
