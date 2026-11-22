# METCOM ver3.1 Python 版

多源再分析气象资料反演**天顶对流层延迟（ZTD）**与**大气可降水量（PWV）**。
本仓库是 MATLAB 版 `METCOM_ver3.1` 的全量 Python 迁移，原有 MATLAB App（.mlapp）
界面参数全部改为 **配置文件 + 命令行** 形式。

> **详细使用说明请阅读 [docs/用户使用说明书.md](docs/用户使用说明书.md)**；
> 迁移差异与验证记录见 [docs/porting_notes.md](docs/porting_notes.md)。

## 项目状态（2026-09-13）

- ✅ **功能全部完成**：格点生成 / ERA5 下载 / 五数据源反演 / 时间插值 / 评估绘图；
- ✅ **GRIB2 全部直读**：包内内嵌 eccodes 运行时（C 库 2.42.0 含 JPEG2000
  解码），三种 GRIB 源无需任何预转换；
- ✅ **真实数据端到端回归全部通过**（SOMEDATA）：五种数据源反演结果与
  MATLAB 基准逐位一致或机器精度（3.8e-11），GMET 评估 CSV 逐位一致，
  01 模块输出逐字节一致（详见下文"测试与验证"）；
- ✅ **pytest 37/37 全绿**（含内嵌运行时完整性 + JPEG 解码测试）；
- 📦 **Pure 纯净版已拆分**：无历史引用、自包含测试的独立版本位于
  [`../METCOM_ver3.1_python_Pure`](../METCOM_ver3.1_python_Pure)（本版本保留
  完整迁移史与 MATLAB 基准回归体系）。

## 功能模块对应关系

| Python 模块 | 原 MATLAB | 功能 |
|---|---|---|
| `metcom.gridgen` | `01_MakeGrdText` | 格点文件生成（DEM 裁剪/行政掩膜、正高/椭球高格点 txt） |
| `metcom.downloader` | `02_Downloader` | ERA5 再分析数据批量下载（CDS API，支持 IDM/断点续传） |
| `metcom.retrieval` | `03_DataProcessing` | 五种再分析数据（ERA5/CMA-MESO/CRA40/CRA V1.5/MERRA2）统一框架反演 |
| `metcom.timeinterp` | `04_TimeInter` | 时间维度插值（6h→1h 等） |
| `metcom.evaluation` | `05_AnaEvaluationPlot` | 精度评估（RMSE/Bias）与可视化（时序图/热力图） |

## 安装

```bash
cd METCOM_ver3.1_python
pip install -r requirements.txt          # 核心依赖
pip install matplotlib cartopy geopandas # 绘图功能（可选）
pip install -e .                          # 安装 metcom 命令
```

Python ≥ 3.9。

## GRIB 数据直读说明

三种 GRIB2 数据源（CMA-MESO / CRA40 / CRA V1.5）**默认直读，无需任何
GRIB→NC 预转换**（原 MATLAB 版因自身不能读 GRIB 才引入转换工具）。

实现方式：包内 `metcom/libs/eccodes-jpeg/` 内嵌了一套从原转换工具
（cfgribTrans）提取的完整 eccodes 运行时——eccodes C 库 2.42.0
（含 JPEG2000/Jasper 解码器）+ 纯 Python 绑定 2.37.0。首次读取 GRIB 时
`metcom.retrieval.eccodes_backend.configure()` 自动将其置于 `sys.path`
最前并注入 DLL 搜索路径，与 exe 的解码行为完全一致。

- pip 版 eccodes 轮子（vcpkg 构建）无 JPEG2000 解码器，CRA40/CMA-MESO
  必须依赖上述内嵌运行时；如被 `METCOM_ECCODES_DISABLE_VENDOR=1` 禁用，
  这两类数据将报 `FunctionalityNotEnabledError`；
- 如需 NC 中间产物，仍可用配置节 `grib2.enabled: true` 或原版
  `GribToNcConverter_cfgrib.exe` 预转换（可选回退，非必需）；
- 也可改用 conda 版 eccodes：`conda install -c conda-forge eccodes
  python-eccodes`，并用环境变量 `METCOM_ECCODES_DIR` 指向其目录。

## 快速开始

所有子命令均使用 `configs/metcom_<模块名称>.yaml` 配置文件，
命令行参数可覆盖配置文件同名项（优先级：CLI > 配置文件 > 默认值）。

### 03 核心 ZTD/PWV 反演（对应原 ERA5.mlapp 等 5 个 GUI）

```bash
# 编辑 configs/metcom_era5.yaml 后运行
metcom retrieve --source era5 --config configs/metcom_era5.yaml

# 或命令行覆盖任意参数
metcom retrieve -s era5 -c configs/metcom_era5.yaml \
    --start 2024010100 --end 2024010123 --site-file bth_0125_orthometric.txt
```

五种数据源各一个配置文件（对应原 5 个 App）：
`metcom_era5.yaml` / `metcom_cmameso.yaml` / `metcom_cra40.yaml` /
`metcom_crav15.yaml` / `metcom_merra2.yaml`。

配置键与原 GUI 字段一一对应（以 ERA5 为例）：

| 配置键 | 原 GUI 字段 | 说明 |
|---|---|---|
| `data_path` | Data Path | NC 数据根目录（含 yyyyMMdd 日文件夹） |
| `output_path` | Output Path | 反演结果输出目录 |
| `output_format` | OutPut Type | txt / mat / dat |
| `site_file` | Stations List | 站点列表（name lat lon h，正高） |
| `start_time` / `end_time` | Start/End time | YYYYMMDD 或 YYYYMMDDHH |
| `spatial_resolution` | Spatial resolution | （纬度, 经度），度 |
| `time_resolution` | Time resolution | 1h / 3h / 6h |
| `parallel` | Parallel pool | 站点级并行（joblib） |
| `egm_file` | （隐式 addpath） | EGM2008 改正文件，默认自动定位 |

输出：`{output_path}/{年积日}/ztd_{YYYYMMDDHH}_sit_{数据源}.{txt|dat|mat}`，
12 列表头格式与 MATLAB 版逐字节一致。

### 01 格点生成 / 02 下载 / 04 插值 / 05 评估绘图

```bash
metcom gridgen    --config configs/metcom_gridgen.yaml      # DEM 裁剪 + 格点 txt
metcom download   --config configs/metcom_downloader.yaml   # ERA5 下载（需 CDS 密钥）
metcom timeinterp --config configs/metcom_timeinterp.yaml   # 6h → 1h 时间插值
metcom evaluate   --config configs/metcom_evaluation.yaml   # RMSE/Bias 评估
metcom plot       --config configs/metcom_plot.yaml         # 时序图 / 热力图
```

评估模式 `eval_mode: timeseries|bystation`（对应原 compareByTime_Grid /
compareByGrid）；绘图类型 `plot_type: sequence|heatmap_rmse|heatmap_value`。

## 测试与验证

```bash
python -m pytest tests/ -q
```

迁移正确性已通过**单元测试 + 真实数据（SOMEDATA）端到端回归**双重验证：

**真实数据端到端回归**（Python 反演输出 vs MATLAB 基准输出）：

| 数据源 | 读取方式 | 范围 | 结果 |
|---|---|---|---|
| ERA5 | NC 直读 | 2024-01-01~02 × 24 时次 × 1443 站 | 最大相对误差 3.8e-11（机器精度） |
| MERRA2 | NC 直读 | 2024-01-01~02 × 4 时次/日 × 1443 站 | 逐位一致（误差 0） |
| CRA40 | **GRIB2 直读** | 2024-01-01~02 × 4 时次/日 × 1443 站 | 逐位一致（误差 0） |
| CMA-MESO | **GRIB2 直读** | 2025-06-01 × 8 时次 × 1443 站 | 逐位一致（误差 0） |
| CRA V1.5 | **GRIB2 直读** | 2024-01-01 × 24 时次 × 1443 站 | 逐位一致（误差 0） |
| 评估 vs GMET | ERA5/MERRA2/CMA-MESO/CRAV15（timeseries + bystation） | — | 与 MATLAB 评估 CSV 逐位一致 |

**单元/回归测试**：37 项 pytest 全绿——01 格点生成与 MATLAB 输出**逐字节一致**、
掩膜 DEM NaN 位置与数值完全一致、04 插值回归（跨天扩展 + 样条独立抽查）、
05 A-vs-A 自评估、物理内核逐公式独立数值验证、内嵌 eccodes 运行时完整性
与 JPEG 直读解码测试。

GRIB2 数据说明：三种 GRIB2 数据源默认直读（内嵌 eccodes 运行时，见上文
"GRIB 数据直读说明"），历史回归中 CRA40/CMA-MESO 亦曾以原版 exe 预转换
NC 的方式验证通过，两条路径结果逐位一致。

## 目录结构

```
METCOM_ver3.1_python/
├── metcom/                  # 包代码
│   ├── gridgen/ downloader/ retrieval/ timeinterp/ evaluation/
│   ├── utils/               # io / fmt（统一固定宽格式化）/ egm2008 / geodesy
│   ├── sources.py           # 五种数据源适配器配置
│   ├── cli.py / config.py
│   └── libs/eccodes-jpeg/   # 内嵌 eccodes 运行时（勿改动）
├── configs/                 # 10 个 metcom_*.yaml 配置模板（含原 GUI 字段对照注释）
├── tests/                   # pytest 单元 + 回归测试
├── benchmarks/              # 基准输出（回归验证黄金数据）
├── regression/              # 真实数据回归的配置/对比脚本/输出（可复跑）
└── docs/                    # 用户使用说明书 + porting_notes.md（差异记录）
```

## 版本

- METCOM Python 3.1.0（对应 MATLAB METCOM ver3.1，demo03 优化版）
- 原版作者：ZHANG Yuhao；Python 迁移：ZCode
