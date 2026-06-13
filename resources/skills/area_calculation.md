---
name: area_calculation
description: "面积计算：投影坐标系面积 vs 地理坐标系面积的差异。当用户需要计算面积时使用。"
requires_toolkits: [vector_transform, vector_analysis]
max_chars: 600
---

# 面积计算

## 适用场景
用户需要计算多边形图层中每个要素的面积。

## 关键规则
- **不要在 EPSG:4326（地理坐标系）上计算面积**。EPSG:4326 的单位是度，直接计算面积的结果没有实际物理意义。
- 面积计算前必须确保图层使用投影坐标系（单位是米）。

## 操作步骤
1. 检查当前图层 CRS
2. 如果 CRS 是地理坐标系，先用 `reproject_layer` 重投影到适当的投影坐标系：
   - 小区域 → UTM 分带（如中国东部 EPSG:32650）
   - 大区域 → EPSG:3857（近似）或等面积投影
   - 中国区域 → CGCS2000 投影（如 EPSG:4527）
3. 重投影后使用 `field_calculator` 添加面积字段：
   - 表达式使用 `$area`（QGIS 内置函数，单位平方米）
   - 如需公顷：`$area / 10000`
   - 如需平方公里：`$area / 1000000`
4. 验证输出字段值合理（正数，数量级正确）
