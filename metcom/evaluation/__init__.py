"""05 精度评估与可视化（05_AnaEvaluationPlot 的迁移整合版）。

io.py      —— index_files / read_dynamic_file 共享实现（去重自原两评估脚本）
compare.py —— compareByTime_Grid / compareByGrid 的统一迁移
plots.py   —— 3 个绘图脚本的统一迁移（sequence / heatmap_rmse / heatmap_value）
"""

from metcom.evaluation.runner import run_evaluation, run_plot

__all__ = ["run_evaluation", "run_plot"]
