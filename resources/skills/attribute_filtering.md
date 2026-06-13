---
name: attribute_filtering
description: "属性筛选与字段整理：在 extract_by_attribute、select_by_expression、keep_fields、field_calculator 之间选择合适工具，并处理字段引用、NULL、复杂表达式和结果字段瘦身。当用户需要按字段筛选、按表达式筛选、保留部分字段或整理属性表时使用。"
requires_toolkits: [attribute_data, vector_analysis]
workspace_attention:
  - fields
  - field_summaries
  - sample_values
  - feature_count
risk_awareness:
  - unknown_field
  - expression_no_match
  - field_removed_before_downstream_step
strategy_guidance:
  - 简单字段比较优先 extract_by_attribute。
  - 复杂表达式、函数或多条件逻辑优先 select_by_expression。
  - 只要求保留少数字段时优先 keep_fields。
  - 只需要改字段名时优先 rename_field，不要用 field_calculator 复制字段。
default_preferences:
  - 字段名明确且唯一时可直接采用，不必追问。
analysis_hints:
  - 属性筛选后检查 feature_count，空结果应回看字段名、字段类型和值域。
clarification_policy:
  - slot: field
    hard_when: 没有字段候选或多个候选置信度接近。
    soft_when: 唯一字段候选与用户描述高度一致。
  - slot: expression
    hard_when: 用户没有提供可落地的字段、操作符或取值。
assumption_preferences:
  - 简单字段比较优先 extract_by_attribute；复杂表达式、函数或多条件逻辑优先 select_by_expression。
  - 只要求保留少数字段时优先 keep_fields，不要用字段计算器绕路。
workspace_queries:
  - target: fields
    reason: 筛选或字段整理前先确认输入图层字段名和字段类型。
    applies_to_slots: [input_ref, field, fields, expression]
  - target: layer_summary
    reason: 执行后用 feature_count 判断筛选结果是否异常为空或数量不合理。
soft_clarification_hints:
  - slot: field
    when: 多个字段名都可能匹配用户描述。
    strategy: 优先 inspect_workspace 获取候选字段；高置信时采用并在摘要中记录假设，低置信时进入 pending。
  - slot: expression
    when: 用户给出自然语言条件但字段和值不完整。
    strategy: 不编造表达式；先问缺失字段或取值。
max_chars: 700
---

# 属性筛选与字段整理

## 关键规则
- 简单比较条件优先用 `extract_by_attribute`，例如 `=`、`!=`、`contains`、`is_null`
- 复杂条件优先用 `select_by_expression`，例如 `CASE WHEN`、多个字段组合、函数调用
- 只想压缩属性表时用 `keep_fields`
- 只改字段名时用 `rename_field`
- 需要新增或更新字段时用 `field_calculator`
- 字段名在 QGIS 表达式中用双引号，如 `"type"`；字符串常量用单引号，如 `'park'`
- 遇到 NULL 时优先考虑 `coalesce()`

## 操作顺序
1. 先用 `inspect_workspace` 看字段列表
2. 根据需求选工具：
   - 简单属性比较 -> `extract_by_attribute`
   - 复杂表达式过滤 -> `select_by_expression`
   - 只保留少数字段 -> `keep_fields`
   - 字段重命名 -> `rename_field`
   - 生成新字段或更新字段 -> `field_calculator`
3. 执行后检查：
   - 输出要素数量是否符合预期
   - 引用字段是否都存在
   - 如果做了 `keep_fields`，确认后续步骤仍保留所需字段
