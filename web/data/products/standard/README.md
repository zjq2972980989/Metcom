# 正式标准产品目录（当前为空）

这里存放**正式规格**的标准产品仓库根，结构与演示数据完全一致：

```
standard/
├── index.json                      ← 产品索引（由 metcom_product.rebuild_index 生成）
└── <数据源>/<区域>/<分辨率>/<年>/<年积日>/MetCom_<源>_<区>_<分辨率>_<YYYYMMDD>.H<HH>
```

约定：

- 正式规格（ERA5 / China / 1x1 / 6 h）当前**尚无数据**，等数据源到齐后用
  `metcom_product.publish --root web/data/products/standard` 上新；
- 演示数据在 `web/data/products/demo`，由 `/api/products` 的 `is_demo` 标记区分，
  绝不会被当作正式产品展示；
- 本目录为空时 `/api/products` 只返回演示数据（或没有数据时返回空列表），
  不报错、也不造数据。
