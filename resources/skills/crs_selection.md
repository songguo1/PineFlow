---
name: crs_selection
description: "CRS选择指南：UTM分带规则、中国常用投影、Web Mercator适用场景。当用户需要选择或变更图层坐标系时使用。"
requires_toolkits: [vector_transform]
max_chars: 650
---

# CRS 选择指南

## 适用场景
用户需要选择或改变图层坐标系。

## 常见 CRS 参考

| CRS | 用途 | 单位 |
|-----|------|------|
| EPSG:4326 | WGS84 地理坐标系（经纬度） | 度 |
| EPSG:3857 | Web Mercator（在线地图常用） | 伪米（随纬度失真） |
| EPSG:32650 | UTM Zone 50N（中国东部） | 米 |
| EPSG:4527 | CGCS2000 3-degree zone 40（中国） | 米 |
| EPSG:4490 | CGCS2000 地理坐标系 | 度 |

## 操作步骤
1. 确定当前图层的 CRS（`inspect_workspace`）
2. 需要米制操作（缓冲、面积）→ 选择投影坐标系（单位是米）
3. 需要导出在线地图 → EPSG:3857
4. 需要精确测量 → 选择合适 UTM 分带或当地投影
5. 如果不知道应该用什么投影：
   - 小区域 → UTM（按经度选带：EPSG:326xx 为北纬, EPSG:327xx 为南纬）
   - 大区域 → EPSG:3857（近似）或等面积投影
6. 使用 `reproject_layer` 执行转换
