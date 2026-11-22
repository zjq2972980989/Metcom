"""后台任务执行层（阶段 2）。

分工固定为::

    runner.py      任务生命周期：PENDING → RUNNING → SUCCESS / FAILED
    handlers.py    任务类型 → 具体处理函数（阶段 3 起 DOWNLOAD / CALCULATION 走真实引擎）
    mock_tasks.py  模拟实现（阶段 3 起只剩 CUSTOM，阶段 5 再替换）

阶段三按这里的分工替换业务实现，runner 与任务中心（路由 / 服务 / 前端）都不需要重新设计。
"""
