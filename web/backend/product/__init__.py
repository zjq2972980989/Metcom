"""标准产品访问层（阶段 4）。

真正的解析逻辑在 ``metcom_product``（同学交付的包，原样集成，不改写）；
本目录只放 Web 侧适配代码：

* ``paths.py`` 仓库定位 + 跨平台索引路径归一化 + 数据源标识统一
* ``services/product_service.py`` 组装响应（查询 / 详情 / 预览 / 下载）
* ``routers/products.py`` HTTP 契约

Web 不重复实现标准产品的生成逻辑（生成仍在 metcom_product 的
converter / publish / rebuild_index 里，由维护者用命令行运行）。
"""
