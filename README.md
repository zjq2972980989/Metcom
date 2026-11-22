# MetCom

多源再分析气象资料反演**天顶对流层延迟（ZTD）**与**大气可降水量（PWV）**。
本仓库包含 MATLAB 版 `METCOM_ver3.1` 的 Python 迁移、Vue 3 + Vite Web 前端、FastAPI
服务层、标准产品格式化输出与 ZIP 交付逻辑。原 MATLAB App（.mlapp）的界面参数已
迁移为 **配置文件 + 命令行 + Web 平台** 三种使用方式。

> **详细使用说明请阅读 [docs/用户使用说明书.md](docs/用户使用说明书.md)**；
> 迁移差异与验证记录见 [docs/porting_notes.md](docs/porting_notes.md)。
> Web 平台说明见 [web/README.md](web/README.md)，标准产品检查报告见
> [docs/数据标准产品输出检查报告.md](docs/数据标准产品输出检查报告.md)。

## 项目状态（2026-09-13）

- ✅ **功能全部完成**：格点生成 / ERA5 下载 / 五数据源反演 / 时间插值 / 评估绘图；
- ✅ **GRIB2 全部直读**：包内内嵌 eccodes 运行时（C 库 2.42.0 含 JPEG2000
  解码），三种 GRIB 源无需任何预转换；
- ✅ **真实数据端到端回归全部通过**（SOMEDATA）：五种数据源反演结果与
  MATLAB 基准逐位一致或机器精度（3.8e-11），GMET 评估 CSV 逐位一致，
  01 模块输出逐字节一致（详见下文"测试与验证"）；
- ✅ **核心与 Web 测试全绿**：核心 139 passed / 12 skipped，Web 后端 383 passed；
  含内嵌运行时完整性、JPEG 解码、标准产品与交付规则测试；
- 🌐 **Web 平台已集成**：Vue 3 + Vite 前端、FastAPI 后端、任务中心、在线计算与
  标准产品查询/预览/下载均已接入当前仓库。
- 📦 **可复现资产已分层**：计算依赖、演示标准产品和回归输出随项目保存；原始再分析
  数据、任务结果和缓存仍保留在本地运行目录，不进入 Git。

## 功能模块对应关系

| Python 模块 | 原 MATLAB | 功能 |
|---|---|---|
| `metcom.gridgen` | `01_MakeGrdText` | 格点文件生成（DEM 裁剪/行政掩膜、正高/椭球高格点 txt） |
| `metcom.downloader` | `02_Downloader` | ERA5 再分析数据批量下载（CDS API，支持 IDM/断点续传） |
| `metcom.retrieval` | `03_DataProcessing` | 五种再分析数据（ERA5/CMA-MESO/CRA40/CRA V1.5/MERRA2）统一框架反演 |
| `metcom.timeinterp` | `04_TimeInter` | 时间维度插值（6h→1h 等） |
| `metcom.evaluation` | `05_AnaEvaluationPlot` | 精度评估（RMSE/Bias）与可视化（时序图/热力图） |

## 安装

### 环境要求

- Python ≥ 3.9（Python 3.13 已验证）
- Node.js `^20.19.0 || >=22.12.0`（Vite 8 要求）
- Windows 10/11 推荐；Linux/macOS 理论可用（IDM 下载除外）

### 获取源码与安装核心引擎

```bash
git clone https://github.com/zjq2972980989/Metcom.git
cd Metcom
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]" -c requirements-lock.txt
```

需要绘图或桌面 GUI 时安装可选 extra：

```bash
python -m pip install -e ".[plot,gui]" -c requirements-lock.txt
```

### 安装 Web 后端与前端

```bash
python -m pip install -r web/backend/requirements-web.txt -c web/backend/requirements-web-lock.txt
cd web/frontend
npm ci
```

后端环境变量模板位于 `web/backend/.env.example`。复制为 `web/backend/.env`
后按本机路径修改；真实的 `.env`、`.cdsapirc`、Earthdata 密码和 Token 不得提交。

### 启动 Web 平台

Windows 可直接运行 `web\start.bat`。如果 `npm` 不在 PATH，先设置
`METCOM_NODE_DIR` 指向 Node.js 安装目录。

手动启动方式：

```bash
cd web/backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

cd web/frontend
npm run dev
```

- Web 平台：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:8000/api/docs

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
# 核心算法与回归测试
python -m pytest tests/ -q

# Web 后端测试（在子目录运行，避免根目录 main.py 同名冲突）
cd web/backend
python -m pytest tests/ -q

# 前端生产构建
cd ../frontend
npm run build
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

**单元/回归测试**：核心 139 项通过、12 项条件跳过，Web 后端 383 项通过——
01 格点生成与 MATLAB 输出**逐字节一致**、
掩膜 DEM NaN 位置与数值完全一致、04 插值回归（跨天扩展 + 样条独立抽查）、
05 A-vs-A 自评估、物理内核逐公式独立数值验证、内嵌 eccodes 运行时完整性
与 JPEG 直读解码测试。

GRIB2 数据说明：三种 GRIB2 数据源默认直读（内嵌 eccodes 运行时，见上文
"GRIB 数据直读说明"），历史回归中 CRA40/CMA-MESO 亦曾以原版 exe 预转换
NC 的方式验证通过，两条路径结果逐位一致。

## 目录结构

```
MetCom/
├── metcom/                  # 包代码
│   ├── gridgen/ downloader/ retrieval/ timeinterp/ evaluation/
│   ├── utils/               # io / fmt（统一固定宽格式化）/ egm2008 / geodesy
│   ├── sources.py           # 五种数据源适配器配置
│   ├── cli.py / config.py
│   └── libs/eccodes-jpeg/   # 内嵌 eccodes 运行时（勿改动）
├── web/
│   ├── backend/             # FastAPI、任务、在线计算、标准产品接口
│   ├── frontend/            # Vue 3 + Vite 前端
│   └── data/
│       ├── dependent/       # 计算必需的 EGM2008 与站点样例（入库）
│       └── products/        # demo（入库）+ standard（仅保留说明）
├── configs/                 # 10 个 metcom_*.yaml 配置模板（含原 GUI 字段对照注释）
├── tests/                   # pytest 单元 + 回归测试
├── benchmarks/              # 基准输出（回归验证黄金数据）
├── regression/              # 真实数据回归的配置/对比脚本/输出（可复跑）
└── docs/                    # 用户使用说明书 + porting_notes.md（差异记录）
```

下列内容按设计不进入 Git：`web/data/raw/`、`web/data/tasks/`、`web/data/cache/`、
`web/frontend/node_modules/`、`web/frontend/dist/`、日志、临时文件和真实凭据。
它们可由下载、构建或运行过程重新生成。

本仓库保存的是 Python 迁移实现与迁移记录；原 MATLAB 比赛工程未包含在当前仓库中，
其算法对应关系和有意保留的数值细节记录在 `docs/porting_notes.md`。

## 版本

- METCOM Python 3.1.0（对应 MATLAB METCOM ver3.1，demo03 优化版）
- 原版作者：ZHANG Yuhao；Python 迁移：ZCode
