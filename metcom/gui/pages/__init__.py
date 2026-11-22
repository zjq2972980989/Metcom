"""METCOM 界面页面集合。

每个页面一个文件，统一继承 :class:`metcom.gui.pages.base.BasePage`：

  home.py        工作台（概览、五步工作流、一键流水线、运行历史）
  gridgen.py     01 格点生成
  download.py    02 再分析数据下载
  retrieve.py    03 核心 ZTD/PWV 反演
  timeinterp.py  04 时间维度插值
  evaluate.py    05 精度评估（含结果表预览）
  visualize.py   05 可视化绘图（含图片预览）
  settings.py    环境自检与设置
"""

from __future__ import annotations

from metcom.gui.pages.base import BasePage

__all__ = ["BasePage", "build_pages"]


def build_pages(ctx) -> dict:
    """构造全部页面（延迟导入，避免启动时加载绘图/评估依赖）。"""
    from metcom.gui.pages.download import DownloadPage
    from metcom.gui.pages.evaluate import EvaluatePage
    from metcom.gui.pages.gridgen import GridgenPage
    from metcom.gui.pages.home import HomePage
    from metcom.gui.pages.retrieve import RetrievePage
    from metcom.gui.pages.settings import SettingsPage
    from metcom.gui.pages.timeinterp import TimeInterpPage
    from metcom.gui.pages.visualize import VisualizePage

    pages = {
        "home": HomePage(ctx),
        "gridgen": GridgenPage(ctx),
        "download": DownloadPage(ctx),
        "retrieve": RetrievePage(ctx),
        "timeinterp": TimeInterpPage(ctx),
        "evaluation": EvaluatePage(ctx),
        "visualize": VisualizePage(ctx),
        "settings": SettingsPage(ctx),
    }
    ctx.pages = pages
    return pages
