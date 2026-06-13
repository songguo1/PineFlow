---
name: raster_basics
description: "栅格基础操作：像元大小选择、波段处理、NoData 处理。当用户需要处理栅格数据时使用。"
requires_toolkits: [raster]
max_chars: 600
---

# 栅格基础操作

## 适用场景
用户需要进行栅格数据的加载、裁剪、统计或转换操作。

## 关键规则
- 栅格操作前确保 `raster` ToolKit 已激活（`select_toolkit(["raster"])`）
- 注意 NoData 值的处理：空值区域可能被统计为 0 而被忽略
- 像元大小影响精度/性能：像元越小越精确但文件越大

## 操作步骤
1. 使用 `load_raster` 加载栅格文件
2. 裁剪栅格：
   - 按边界矢量 → `clip_raster_by_mask`
   - 按矩形范围 → `clip_raster_by_extent`
3. 分区统计：
   - 使用 `zonal_statistics` 按多边形区域统计栅格值
   - 常用统计方法：mean（均值）、sum（总和）、min/max（极值）、stddev（标准差）
4. 栅格计算：使用 `raster_calculator` 执行代数运算
5. 栅格转矢量：`polygonize_raster` 将栅格转为多边形
6. 矢量转栅格：`rasterize_vector` 将矢量转为栅格
