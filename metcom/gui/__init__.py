"""METCOM 图形界面（PySide6）。

原 MATLAB 版为 5 个 App Designer GUI（.mlapp），Python 迁移版先做成了
"配置文件 + 命令行"。本包在其之上补回桌面图形界面，并整合为
一站式可视化工作流：

  metcom.gui.theme       设计令牌（配色/字体/间距）与 QSS 生成
  metcom.gui.icons       内置矢量图标（运行时渲染，任意 DPI 清晰）
  metcom.gui.components  可复用控件（卡片/统计块/流程条/日志台/表单）
  metcom.gui.workers     后台任务执行（QThread + 日志捕获 + 进度回调）
  metcom.gui.pages       功能页面（首页 / 01~05 / 设置）
  metcom.gui.window      主窗口（侧边导航 + 页面堆栈 + 状态栏）

启动方式::

    python -m metcom.gui            # 模块入口
    metcom-gui                      # 安装后的控制台入口
"""

from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    """GUI 入口（延迟导入，避免仅使用 CLI 时加载 Qt）。"""
    from metcom.gui.app import run

    return run()
