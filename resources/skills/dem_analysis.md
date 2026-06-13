---
name: dem_analysis
description: "DEM 地形分析：坡度、坡向、山体阴影。当用户需要对 DEM 做地形分析时，必须先加载此技能。"
requires_toolkits: [raster]
max_chars: 800
---

# DEM 地形分析

## 适用场景
用户对 DEM（数字高程模型）栅格图层做地形分析——坡度、坡向、山体阴影。

## 关键规则
- **三个操作共享同一个 DEM 输入**——slope、aspect、hillshade 全部从同一个 input_ref 出发。
- **不需要重投影**——栅格地形分析在 DEM 的原始 CRS 下即可运行，投影系下 z_factor=1。
- **hillshade 的 azimuth 和 altitude 有默认值**（azimuth=315, altitude=45），除非用户明确指定光照方向，否则不需要传递这两个参数。
- **先 slope/aspect，最后 hillshade**——hillshade 是展示用的，如果需要导出分析结果，优先导出 slope 或 aspect。

## 操作步骤
1. 确保已加载 DEM 栅格图层（`load_raster`）；如果还没有，先加载
2. 如需坡度：调用 `slope`，band 默认为 1
3. 如需坡向：调用 `aspect`，band 默认为 1
4. 如需山体阴影：调用 `hillshade`，使用默认 azimuth=315, altitude=45, z_factor=1
5. 如果用户同时要多个结果，逐一执行——每个工具调用一次
6. 结果导出为 `.tif` 格式
