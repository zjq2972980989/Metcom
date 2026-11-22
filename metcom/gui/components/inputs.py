"""表单控件：标准输入工厂 + 表单行 + 路径选择器。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSizePolicy, QSpinBox, QVBoxLayout,
                               QWidget)

from metcom.gui import icons, theme


# ------------------------------------------------------------ 控件工厂 --
def line_edit(value: str = "", placeholder: str = "") -> QLineEdit:
    edit = QLineEdit(str(value))
    if placeholder:
        edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    return edit


def combo(items: list[str], current: str | None = None) -> QComboBox:
    box = QComboBox()
    box.addItems([str(i) for i in items])
    if current is not None and str(current) in [str(i) for i in items]:
        box.setCurrentText(str(current))
    box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return box


def spin(minimum: int, maximum: int, value: int, step: int = 1,
         suffix: str = "") -> QSpinBox:
    box = QSpinBox()
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    return box


def dspin(minimum: float, maximum: float, value: float, decimals: int = 4,
          step: float = 0.01, suffix: str = "") -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setDecimals(decimals)
    box.setRange(minimum, maximum)
    box.setSingleStep(step)
    box.setValue(value)
    if suffix:
        box.setSuffix(suffix)
    return box


def checkbox(text: str, checked: bool = False) -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(checked)
    return box


# ------------------------------------------------------------ 路径选择 --
class PathField(QWidget):
    """路径输入框 + 浏览按钮（支持文件 / 目录 / 保存三种模式）。"""

    def __init__(self, mode: str = "file", value: str = "",
                 placeholder: str = "尚未选择", file_filter: str = "所有文件 (*)",
                 caption: str = "选择文件", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = mode
        self._filter = file_filter
        self._caption = caption

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._edit = line_edit(value, placeholder)
        layout.addWidget(self._edit, 1)
        self._button = QPushButton("浏览")
        self._button.setObjectName("GhostButton")
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.clicked.connect(self._browse)
        layout.addWidget(self._button, 0)

    def _browse(self) -> None:
        start = self._edit.text().strip()
        if start and not Path(start).exists():
            start = str(Path(start).parent)
        if self._mode == "dir":
            chosen = QFileDialog.getExistingDirectory(self, self._caption, start)
        elif self._mode == "save":
            chosen, _ = QFileDialog.getSaveFileName(self, self._caption, start,
                                                    self._filter)
        else:
            chosen, _ = QFileDialog.getOpenFileName(self, self._caption, start,
                                                    self._filter)
        if chosen:
            self._edit.setText(str(Path(chosen)))

    def value(self) -> str:
        return self._edit.text().strip()

    def set_value(self, value: str) -> None:
        self._edit.setText("" if value is None else str(value))

    def edit(self) -> QLineEdit:
        return self._edit


# -------------------------------------------------------------- 表单行 --
class FormRow(QWidget):
    """表单行：左标签 + 右控件（可选下方浅色提示）。"""

    def __init__(self, label: str, widget: QWidget, hint: str = "",
                 label_width: int = 104, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(12)
        name = QLabel(label)
        name.setObjectName("MetricLabel")
        name.setFixedWidth(label_width)
        name.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
        self._widget = widget
        row.addWidget(widget, 1)
        root.addLayout(row)

        if hint:
            tip = QLabel(hint)
            tip.setObjectName("Hint")
            tip.setWordWrap(True)
            tip.setContentsMargins(label_width + 12, 0, 0, 0)
            root.addWidget(tip)

    def widget(self) -> QWidget:
        return self._widget


def hint(text: str) -> QLabel:
    """浅色小字提示。"""
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(True)
    return label


def icon_button(icon_name: str, tooltip: str = "", size: int = 18) -> QPushButton:
    """只显示图标的圆形按钮。"""
    button = QPushButton()
    button.setObjectName("IconButton")
    button.setIcon(icons.icon(icon_name, None, size))
    button.setFixedSize(34, 34)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    if tooltip:
        button.setToolTip(tooltip)
    return button


__all__ = ["line_edit", "combo", "spin", "dspin", "checkbox", "PathField",
           "FormRow", "hint", "icon_button"]
