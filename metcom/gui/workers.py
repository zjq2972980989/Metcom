"""后台任务执行层：把业务函数放到工作线程里跑，并实时回传日志。

设计要点
--------
* 业务模块（01~05）内部使用 ``print`` / ``logging`` 输出，界面层不改动它们；
  这里用 ``contextlib.redirect_stdout`` 加一个根日志 ``Handler`` 把输出按行
  抓取，转成 Qt 信号送回主线程的控制台。
* 同一时刻只允许一个任务（:class:`TaskController`），避免多个后台线程同时
  写日志、抢占 CPU 与文件句柄。
* 取消是协作式中断：取消标志置位后，下一次日志写入抛出 :class:`TaskCancelled`，
  从而打断 print 密集的循环；纯计算循环会在下一个日志点退出。
* 进度不要求业务代码上报，而是从日志文本里解析 ``[i/N]`` 片段（02 下载模块
  原生使用该格式）；解析不到时界面退化为不确定进度。
"""

from __future__ import annotations

import contextlib
import io
import logging
import re
import threading
import time
import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal

__all__ = ["TaskCancelled", "Task", "TaskController"]

_PROGRESS_RE = re.compile(r"\[(\d+)\s*/\s*(\d+)\]")


class TaskCancelled(Exception):
    """用户点击取消后，在后台线程内部抛出以中断任务。"""


class _SignalStream(io.TextIOBase):
    """把 print/write 的文本按行转成回调调用。"""

    def __init__(self, emit: Callable[[str], None],
                 cancelled: Callable[[], bool]) -> None:
        super().__init__()
        self._emit = emit
        self._cancelled = cancelled
        self._buf = ""

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._emit(line)
        if len(self._buf) > 8192:
            self._emit(self._buf)
            self._buf = ""
        if self._cancelled():
            raise TaskCancelled()
        return len(text)

    def flush(self) -> None:
        if self._buf:
            self._emit(self._buf)
            self._buf = ""

    def writable(self) -> bool:
        return True


class _SignalLogHandler(logging.Handler):
    """把 logging 记录转成控制台文本（带等级前缀，与 console.classify 对齐）。"""

    def __init__(self, emit: Callable[[str], None]) -> None:
        super().__init__()
        self._emit = emit

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = record.getMessage()
            if record.exc_info:
                detail = "".join(traceback.format_exception(*record.exc_info))
                text = f"{text}\n{detail}"
            if record.levelno >= logging.ERROR:
                text = f"错误: {text}"
            elif record.levelno >= logging.WARNING:
                text = f"警告: {text}"
            self._emit(text)
        except Exception:
            pass


class Task(QThread):
    """在后台线程执行 ``fn(*args, **kwargs)``，实时转发输出。

    信号
    ----
    log_line(str)      每一行输出（stdout / stderr / logging 汇总）
    progress(int,str)  从日志 i/N 解析出的百分比与 i/N 文本
    done(object)       正常返回，携带业务函数返回值
    failed(str,str)    异常，携带简明信息与完整 traceback
    cancelled(str)     被用户取消
    """

    log_line = Signal(str)
    progress = Signal(int, str)
    done = Signal(object)
    failed = Signal(str, str)
    cancelled = Signal(str)

    def __init__(self, name: str, fn: Callable[..., Any], args: tuple = (),
                 kwargs: dict | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.name = name
        self._fn = fn
        self._args = tuple(args)
        self._kwargs = dict(kwargs or {})
        self._cancel = threading.Event()
        self._t0 = 0.0

    def cancel(self) -> None:
        """请求取消（协作式，下一次日志写入时生效）。"""
        self._cancel.set()

    def is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def elapsed(self) -> float:
        return (time.time() - self._t0) if self._t0 else 0.0

    def _on_line(self, text: str) -> None:
        self.log_line.emit(text)
        match = _PROGRESS_RE.search(text)
        if match:
            current, total = int(match.group(1)), int(match.group(2))
            if total > 0:
                self.progress.emit(min(100, int(current * 100 / total)),
                                   f"{current}/{total}")

    def run(self) -> None:
        self._t0 = time.time()
        stream = _SignalStream(self._on_line, self._cancel.is_set)
        handler = _SignalLogHandler(self._on_line)
        root = logging.getLogger()
        prev_level = root.level
        root.addHandler(handler)
        if prev_level > logging.INFO or prev_level == logging.NOTSET:
            root.setLevel(logging.INFO)
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                result = self._fn(*self._args, **self._kwargs)
        except TaskCancelled:
            stream.flush()
            self.log_line.emit("任务已被取消，已停止后续处理")
            self.cancelled.emit(self.name)
        except Exception as exc:
            stream.flush()
            detail = traceback.format_exc()
            self.log_line.emit(f"错误: {type(exc).__name__}: {exc}")
            for line in detail.rstrip().splitlines():
                self.log_line.emit(line)
            self.failed.emit(f"{type(exc).__name__}: {exc}", detail)
        else:
            stream.flush()
            self.done.emit(result)
        finally:
            root.removeHandler(handler)
            root.setLevel(prev_level)


class TaskController(QObject):
    """单任务调度器：把 :class:`Task` 接到控制台、进度条与状态提示。

    页面只需 ``ctx.controller.submit(名称, 函数, 参数..., on_done=回调)``，
    不必关心线程、日志重定向与异常处理。
    """

    started = Signal(str)
    succeeded = Signal(str, object)
    failed = Signal(str, str)
    cancelled = Signal(str)
    finished = Signal(str)
    log_line = Signal(str)
    progress = Signal(int, str)
    busy_changed = Signal(bool, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._task: Task | None = None
        self._on_done: Callable | None = None
        self._on_fail: Callable | None = None
        self._name = ""
        self._result: Any = None
        self.last_seconds: float = 0.0

    def is_busy(self) -> bool:
        return self._task is not None and self._task.isRunning()

    def current_name(self) -> str:
        return self._name if self.is_busy() else ""

    def last_result(self) -> Any:
        return self._result

    def emit_log(self, text: str) -> None:
        """供界面层自行写一行日志。"""
        self.log_line.emit(text)

    def submit(self, name: str, fn: Callable[..., Any], *args,
               on_done: Callable[[Any], None] | None = None,
               on_fail: Callable[[str, str], None] | None = None,
               **kwargs) -> bool:
        """提交任务；已有任务在跑时返回 False（不排队，避免语义含糊）。"""
        if self.is_busy():
            self.emit_log(f"警告: 任务「{self._name}」正在运行，请等待完成或先取消")
            return False

        task = Task(name, fn, args, kwargs, parent=self)
        task.log_line.connect(self.log_line)
        task.progress.connect(self.progress)
        task.done.connect(self._handle_done)
        task.failed.connect(self._handle_failed)
        task.cancelled.connect(self._handle_cancelled)
        task.finished.connect(self._handle_finished)

        self._task = task
        self._on_done = on_done
        self._on_fail = on_fail
        self._name = name
        self._result = None
        self.started.emit(name)
        self.busy_changed.emit(True, name)
        task.start()
        return True

    def cancel(self) -> None:
        if self._task is not None and self._task.isRunning():
            self.emit_log("提示: 已发送取消请求，将在当前处理步骤结束后停止…")
            self._task.cancel()

    def _handle_done(self, result: Any) -> None:
        self._result = result
        callback = self._on_done
        self._on_done = None
        self.succeeded.emit(self._name, result)
        if callback is not None:
            callback(result)

    def _handle_failed(self, message: str, detail: str) -> None:
        callback = self._on_fail
        self._on_done = None
        self._on_fail = None
        self.failed.emit(self._name, message)
        if callback is not None:
            callback(message, detail)

    def _handle_cancelled(self, name: str) -> None:
        self._on_done = None
        self._on_fail = None
        self.cancelled.emit(name)

    def _handle_finished(self) -> None:
        if self._task is not None:
            self.last_seconds = self._task.elapsed()
        name = self._name
        self.busy_changed.emit(False, name)
        self.finished.emit(name)
