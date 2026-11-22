"""业务服务层公共异常。

路由层与任务执行器都按这里的类型处理错误：

    InvalidRequestError  请求参数不合法（路由层统一转 422，不创建任务）
    TaskExecutionError   任务执行失败（runner 收敛为 FAILED，message 面向用户）
"""

from __future__ import annotations


class InvalidRequestError(ValueError):
    """请求参数不合法。message 面向用户，路由层返回 422。"""

    status_code = 422


class TaskExecutionError(RuntimeError):
    """任务执行失败。message 面向用户，不携带 Python traceback。"""


__all__ = ["InvalidRequestError", "TaskExecutionError"]
