# 演示标准产品（demo）——不是正式产品

本目录是交付方提供的**演示数据**：

- 区域 BTH（113–120°E / 36–43°N），空间分辨率 0.125°
- 覆盖 5 个数据源（ERA5 / MERRA2 / CRA40 / CRAV15 / CMAMESO）、4 天、多时次
- 结构与正式仓库完全一致：`index.json` + `<源>/<区>/<分辨率>/<年>/<年积日>/MetCom_*.H*`

用途：验证 Web 侧"查询 → 预览 Header/数据 → 下载"链路。

必须遵守：

- 界面上必须标注为**演示数据**，不得冒充正式产品；
- 正式规格（ERA5 / China / 1×1 / 6 h）等数据源到位后放进
  `web/data/products/standard`，接口用 `is_demo` 字段区分两者；
- `index.json` 由 `metcom_product.rebuild_index --root web/data/products/demo` 生成，
  不要手工编辑。
