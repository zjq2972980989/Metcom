"""业务服务层。

模块分工：
    task_service.py        任务创建 / 状态流转 / 历史记录（阶段 2）
    download_service.py    数据下载业务（阶段 3，调用 metcom.downloader）
    calculation_service.py 参数计算业务（阶段 3，调用 metcom.retrieval）
    errors.py              服务层公共异常
    validation.py          业务参数校验
    results.py             结果落盘位置与元信息
    custom_service.py      定制产品业务（阶段 5）
    product_service.py     标准产品业务（阶段 4）

服务层是唯一允许调用 engine.adapter 的层；路由层不直接碰引擎。
"""
