---
name: field_calculator
description: "字段计算器：表达式语法、NULL 处理、类型转换。当用户需要计算新字段或修改现有字段值时使用。"
requires_toolkits: [vector_analysis]
max_chars: 600
---

# 字段计算器

## 适用场景
用户需要添加新字段或基于表达式计算字段值。

## 关键规则
- QGIS 字段计算器使用其内置表达式语言，不是 Python
- NULL 值在算术运算中会导致结果为 NULL（需要 `coalesce()` 处理）
- 字段类型必须与赋值兼容（如不能用字符串给数值字段赋值）

## 操作步骤
1. 确认目标图层的字段列表（用 `inspect_workspace`）
2. 调用 `field_calculator` 时指定：
   - `input_ref`：目标图层
   - `field_name`：新字段名
   - `field_type`：`integer` / `float` / `string` / `date`
   - `formula`：QGIS 表达式
3. 常用表达式：
   - 计算面积：`$area`（平方米）
   - 计算长度：`$length`（米）
   - 处理 NULL：`coalesce("field", 0)`
   - 条件赋值：`CASE WHEN "type" = 'A' THEN 1 ELSE 0 END`
4. 验证字段已添加且值在合理范围内
