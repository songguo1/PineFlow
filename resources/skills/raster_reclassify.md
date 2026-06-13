---
name: raster_reclassify
description: "栅格重分类：使用查找表对栅格值重新分类。当用户需要对栅格做重分类或分级时，必须先加载此技能。"
requires_toolkits: [raster, vector_analysis]
max_chars: 600
---

# 栅格重分类

## 适用场景
用户需要使用一个查找表（矢量图层）对栅格进行分类或重映射。

## 关键规则
- **查找表必须是矢量图层**——不能直接用数字列表。
- **查找表需要特定字段结构**：最小值字段 (`min_field`)、最大值字段 (`max_field`)、输出值字段 (`value_field`)。
- 如果用户说"把高程分成三类：<500 低、500-1000 中、>1000 高"，需要先创建一个查找表图层，再用 `reclassify_raster`。
- **主输入是栅格** (`input_ref`)，**查找表是矢量** (`table_ref`)——不要搞反。

## 操作步骤
1. 确认 DEM/栅格图层已加载
2. 确认查找表图层存在且有正确的字段（如果没有，引导用户提供 CSV 或手动创建）
3. 调用 `reclassify_raster`：`input_ref`=栅格, `table_ref`=查找表, `raster_band`=1
4. 结果是一个新的栅格图层
