"""METCOM Python 版 —— 多源再分析气象资料反演 ZTD/PWV。

迁移自 MATLAB METCOM ver3.1，功能模块对应关系：
  metcom.gridgen     <- 01_MakeGrdText   格点文件生成
  metcom.downloader  <- 02_Downloader    再分析数据下载
  metcom.retrieval   <- 03_DataProcessing 核心 ZTD/PWV 反演（五种数据源统一框架）
  metcom.timeinterp  <- 04_TimeInter     时间维度插值
  metcom.evaluation  <- 05_AnaEvaluationPlot  精度评估与可视化
"""

__version__ = "3.1.0"
