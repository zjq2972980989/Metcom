"""日志控制台：把子进程/模块的 print 输出以等级着色滚动呈现。

等级不靠调用方标注，而是由 :func:`classify` 从文本关键字推断——这样才能
直接复用现有模块里既有的 print 文案（"警告: ..."、"完成: ..."），
不需要为了界面去改业务代码。
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from metcom.gui import theme

WARN_KEYS = ("警告", "warning", "warn", "跳过", "不完整", "未找到", "缺失")
ERROR_KEYS = ("错误", "error", "traceback", "失败", "exception", "failed",
              "filenotfound", "valueerror", "keyerror")
OK_KEYS = ("完成", "成功", "一致", "通过", "done", "完成:", "✅", "已保存")
CMD_KEYS = ("===", ">>>", "$ ", "metcom ", "程序开始", "program has been")
MUTED_KEYS = ("提示", "hint", "--")


def classify(text: str) -> str:
    """按关键字推断一行日志的等级。

    Returns
    -------
    "error" | "warn" | "ok" | "cmd" | "muted" | "info"
    """
    low = text.lower()
    if any(k in low for k in ERROR_KEYS):
        return "error"
    if any(k in low for k in WARN_KEYS):
        return "warn"
    if any(k in low for k in OK_KEYS):
        return "ok"
    if any(k in text for k in CMD_KEYS) or text.lstrip().startswith("==="):
        return "cmd"
    if any(k in low for k in MUTED_KEYS):
        return "muted"
    return "info"


class LogConsole(QPlainTextEdit):
    """只读日志台（限制最大行数，避免长任务吃内存）。"""

    def __init__(self, parent: QWidget | None = None, max_lines: int = 4000) -> None:
        super().__init__(parent)
        self.setObjectName("Console")
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setFont(theme.mono_font(9))
        self._formats: dict[str, QTextCharFormat] = {}
        self.refresh_theme()

    # -- 主题 --
    def refresh_theme(self) -> None:
        """主题切换后重建各级文本格式。"""
        pal = theme.palette()
        mapping = {
            "info": pal.console_text,
            "ok": pal.success,
            "warn": pal.warning,
            "error": pal.danger,
            "cmd": pal.accent,
            "muted": pal.text_faint,
        }
        self._formats = {}
        base = theme.mono_font(9)
        for level, color in mapping.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            fmt.setFont(base)
            if level == "error":
                fmt.setFontWeight(QFont.Weight.DemiBold)
            self._formats[level] = fmt

    # -- 写入 --
    def append_line(self, text: str, level: str | None = None) -> None:
        text = text.rstrip("\n")
        if not text.strip():
            return
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text + "\n", self._formats.get(level or classify(text),
                                                         self._formats["info"]))
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def append_output(self, text: str) -> None:
        """写入一段可能多行的原始输出。"""
        for line in text.splitlines():
            self.append_line(line)

    def append_banner(self, text: str) -> None:
        """写入醒目分隔横幅（任务开始/结束）。"""
        self.append_line("")
        self.append_line(text, "cmd")

    def clear_log(self) -> None:
        self.clear()


__all__ = ["LogConsole", "classify"]
