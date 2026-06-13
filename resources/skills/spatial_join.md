---
name: spatial_join
description: "空间连接：连接方向选择、字段冲突处理、一对多膨胀检查。当用户需要将两个图层的属性按空间关系合并时使用。"
requires_toolkits: [vector_analysis]
max_chars: 650
---

# 空间连接

## 适用场景
用户需要将 A 图层的属性附加到 B 图层的要素上，基于空间关系（如点在多边形内、线与面相交）。

## 关键规则
- 连接前确保两个图层 CRS 一致
- 注意字段重名：连接后可能会有同名字段（如两个图层都有 `name` 字段）
- 一对多连接会导致输出要素数量膨胀

## 操作步骤
1. 使用 `inspect_workspace` 确认两个图层的 CRS、要素数量
2. CRS 不一致时用 `reproject_layer` 统一
3. 根据空间关系选择工具：
   - 点在多边形内 → `join_by_location` with predicate="within" 或 "intersects"
   - 最近要素 → `join_by_nearest`
4. 指定需要保留的连接字段（避免同名字段冲突）
5. 验证输出要素数量：
   - 一对多关系可能导致 feature_count > 输入图层
   - 如果输出为 0，检查 CRS 和空间重叠
