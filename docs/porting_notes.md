# Python 迁移差异与修复记录（porting notes）

本文档记录 METCOM ver3.1（MATLAB）→ Python 迁移过程中所有有意的行为差异、
bug 修复与需要真实数据进一步验证的事项。

## 1. 交互形式

原 5 个 MATLAB App Designer GUI（.mlapp）全部替换为
**配置文件（`configs/metcom_<模块名称>.yaml`）+ 命令行（`metcom <子命令>`）**。
配置键与 GUI 字段一一对应（见 README 对照表）。
参数优先级：命令行 > 配置文件 > 代码默认值（默认值取自原 GUI 初始值）。

## 2. 修复的原代码 bug（行为差异点）

| # | 原代码位置 | 问题 | Python 处理 |
|---|---|---|---|
| 1 | `fun_calculation_site.m`（全 5 模块） | 站点高度高于最高气压层时 `ell_g(k)` 越界崩溃（k 循环未 break） | 显式抛出 `ValueError` 并给出明确信息 |
| 2 | `fun_Merra2_site.m` NaN 修剪 | `rmmissing` 假设 NaN 连续位于数组头部；不满足时输出 NaN | 忠实复刻 + 修剪后仍含 NaN 时打印警告 |
| 3 | `compareByTime_Grid.py` / `compareByGrid.py` | `read_dynamic_file` 捕获一切异常后静默返回空表 | 打印警告后再返回空表 |
| 4 | 两评估脚本 `index_files` | 同目录混入多数据源文件时按时间键静默覆盖 | 检测到不同数据源同名时刻时警告 |
| 5 | `compareByGrid.py` | 空数据 `pd.concat` 直接崩溃 | 显式抛出"无有效匹配数据" |
| 6 | `Download_ERA5_IDM_multi_pureInfo.py` | 两处裸 `except:`（吞 KeyboardInterrupt 等） | 改为捕获具体异常 |
| 7 | 下载脚本 | `MIN_FILE_SIZE = 220MB` 针对特定变量集硬编码 | 配置项 `min_file_size_mb` |
| 8 | `01_MakeGrdText` 两脚本 | 相对路径前缀写 `00_MakeGrdText` 与实际目录 `01_` 不符；`main.m` 绝对路径指向另一工程 | 全部路径配置化（`metcom_gridgen.yaml`），支持相对工作目录解析 |
| 9 | `grib2Trans.m`（CMAMESO 版） | 引用的 exe 名 `cfgrib_Trans2NC.exe` 与目录中实际 exe 不符；GUI 中转换调用被注释 | 用 cfgrib 内联转换（`metcom.retrieval.grib2nc`），配置项 `grib2.enabled` 控制 |

## 3. 有意保留的原代码细节（为对齐数值，不得"修正"）

- PWV 积分除以 `g = 9.80655`，而外推分支静力常数用 `9.80665`（原码即不一致）；
- ZHD 每层都加同一个 Saastamoinen 顶层修正，ZWD 不加；
- EGM2008 大地水准面差距为**最近邻查表**（非插值）；
- 位势高→椭球高为 WGS84 一阶近似 `h ≈ Zg(1 + Zg/N(φ))`（非严格大地高）；
- 站点文件中的正高直接作为 `hell` 参与"椭球高"层序比较（原版如此，物理上
  因为层高已减 EGM2008 N 转为正高而自洽）；
- MERRA2 的 `PS`（地表气压）读取后从未使用，Python 版不再读取（行为等价）；
- MERRA2/CRAV15 等的气压层顺序信任文件（readme 注释与代码不一致处均以代码为准：
  CRAV15 固定取 37 层；CMAMESO 气压依赖转换后 NC 的 `isobaricInhPa` 单位为 hPa）；
- 时间插值 `original_interval` 参数不参与计算（原版仅文档作用）。

## 4. 实现方式差异（数值等价，非逐字节逐位）

- `interp1('spline','extrap')` → `scipy CubicSpline(not-a-knot, extrapolate)`，
  MATLAB 'spline' 即 not-a-knot，二者一致；
- `interp2` / `griddedInterpolant('linear','none')` →
  `scipy RegularGridInterpolator(bounds_error=False, fill_value=NaN)`；
- `inpolygon`（含边界点语义）→ `shapely.covered_by`（含边界）；
  已用行政掩膜基准验证：NaN 位置与数值完全一致；
- MATLAB 列主序逻辑索引 → numpy `ravel(order='F')`（01 模块站点顺序逐字节回归通过）；
- EGM2008 `.mat`（v7.3/HDF5）→ h5py 读取并转置（MATLAB (n_lon, n_lat) 视角）；
- GRIB2→NC 由外部 exe → cfgrib 内联（输出变量名对齐 gh/t/r/q + isobaricInhPa）；
- NC 读取按**维度名**自动重排为 (lon, lat, lev)（兼容不同 CDS API 维度顺序，
  对原预期布局行为等价）；
- 多变量分文件数据（CRA40/CRAV15/CMAMESO）原按四列表排序序号配对，
  Python 改按时间戳配对（列表对齐时等价，长度不齐时更稳健并跳过告警）；
- 输出 NaN 格式化为右对齐 `NaN`（与 MATLAB fprintf `%20.8f` 一致）。

## 5. 验证状态（2026-09-13 更新：基于 SOMEDATA 真实数据的端到端回归）

### 5.1 真实数据回归结果（Python 输出 vs MATLAB 基准输出）

| 数据源 | 数据 | 回归范围 | 结果 |
|---|---|---|---|
| ERA5 | SOMEDATA data_ERA5（新 CDS API 格式 NC） | 20240101-02 × 24 时次 × 1443 站 | ✅ 48/48 文件，最大相对误差 3.8e-11 |
| MERRA2 | SOMEDATA data_Merra2 | 20240101-02 × 4 时次/日 × 1443 站 | ✅ 8/8 文件，误差 0（逐位一致） |
| CRA40 | SOMEDATA data_CRA40（GRIB2，exe 预转换 NC） | 20240101-02 × 4 时次/日 × 1443 站 | ✅ 8/8 文件，误差 0（逐位一致） |
| CMA-MESO | SOMEDATA data_CMAMESO（GRIB2，exe 预转换 NC） | 20250601 × 8 时次 × 1443 站 | ✅ 8/8 文件，误差 0（逐位一致） |
| CRA V1.5 | SOMEDATA data_CRAV15（GRIB2，Python cfgrib 直读） | 20240101 × 24 时次 × 1443 站 | ✅ 24/24 文件，误差 0（逐位一致） |
| 评估（timeseries） | ERA5/MERRA2/CMA-MESO/CRAV15 反演 vs GMET | 48/8/8/24 时次 | ✅ 与 MATLAB 评估 CSV 逐位一致 |
| 评估（bystation） | Python ERA5 反演 vs GMET | 1443 站 | ✅ 与 MATLAB 评估 CSV 逐位一致 |

未覆盖：MERRA2 的 2025-06 基准输出、CMA-MESO 的 20250602 基准输出
（SOMEDATA 无对应输入数据）。回归配置与对比脚本保留在 `regression/` 目录。

### 5.2 真实数据回归中发现并修复的问题

| # | 问题 | 修复 |
|---|---|---|
| 1 | **netCDF 缺测值泄漏**：真实 MERRA2 文件用 `_FillValue`(1e15) 填充地下层，`np.asarray(masked_array)` 剥掉掩膜导致填充值参与计算（PWV 爆到 1e13 量级）。MATLAB `ncread` 会把 FillValue 转 NaN 再由 `rmmissing` 修剪 | 读取层统一 `filled(np.nan)`（`common.to_float64`），修后 MERRA2 与 MATLAB 逐位一致 |
| 2 | **逐站点重复全球场除法**：`fun_site` 每站对全球 0.25°/0.1° 场做一次 9.80665 除法（与 MATLAB 相同，是原版"1312 站很慢"的主因） | `TempData.get_geo_height` 按时次缓存（IEEE 语义等价），24 时次耗时从 ~3 分钟/时次降至 ~5 秒/时次 |
| 3 | **CLI 数据源推断**：`retrieve -c metcom_cra40.yaml` 不带 `--source` 时默认 era5，静默扫不到文件 | 从配置文件名 `metcom_<源>.yaml` 推断数据源；0 产出时显式警告 |
| 4 | **GRIB2 多层类型**：真实 CRA40/CMA-MESO/CRV15 的 GRIB2 含 surface/tropopause 等多种层类型，cfgrib 直接打开报错 | 按 `typeOfLevel=isobaricInhPa` 过滤（与原 exe 行为一致），并禁用 .idx 索引文件副作用 |
| 5 | **各变量气压层数不一致**：CRA40 GPH/TEM 47 层、RHU 41 层（30→10 hPa 跳层）、SHU 37 层 | 统一截取前 N 层并在该范围内校验（与 MATLAB `count=[1,1,N]` 行为一致） |
| 6 | **exe 产出 NC 格式**：原版 GribToNcConverter_cfgrib.exe 输出为纯 HDF5（h5netcdf 写出），netCDF4 库拒读（MATLAB 可读） | `_read_var_nc` 增加 xarray/h5netcdf 回退 |
| 7 | **eccodes JPEG 支持**：pip 版 eccodes 未编译 JPEG2000 解码，无法读 CRA40/CMA-MESO 的 GRIB2 数值段（CRAV15 可读） | 已通过内嵌 vendor 运行时彻底解决（见 5.3 节） |

### 5.3 GRIB 直读重构（2026-09-13，第二轮）

原 MATLAB 不能读 GRIB，故 cfgribTrans 项目（`00_DevelopmentTest/cfgribTrans`，
开发环境不在本机）提供 GRIB→NC 转换工具并打包为
`GribToNcConverter_cfgrib.exe`。纯 Python 版重构后 **三种 GRIB2 数据源
默认直读，转换环节不再必需**。

逆向结论（cfgribTrans 源码 + exe/zip 解包）：
- exe 的 GRIB 解码栈 = **eccodes C 库 2.42.0（conda-forge 构建，JPEG2000
  走 Jasper 后端）+ 纯 Python 绑定 eccodes/gribapi 2.37.0（无 pyd，cffi
  ABI 模式）+ cfgrib 0.9.15.0**（与 pip 版本一致）+ eccodes_memfs（内嵌
  定义文件）；构建 conda 环境已不存在，但产物分发 zip（onedir）内含全部
  明文文件，可直接提取；
- pip 版 eccodes 轮子（vcpkg 构建）无 JPEG2000 解码器（其 eccodes.dll 不
  导入 jasper/openjpeg），故直读 CRA40/CMA-MESO 报
  `FunctionalityNotEnabledError`；
- pip 绑定 2.40.1 的 `_eccodes.pyd` 经 LOAD_WITH_ALTERED_SEARCH_PATH 优先
  绑定轮子自带 DLL，仅做 ctypes 预加载/PATH 注入无法替换，且双库并存会
  触发原生崩溃——必须整套替换。

落地方式：
1. 从分发 zip 提取 `eccodes/`（2.37.0 纯 Python 绑定）+ 10 个 DLL
   （eccodes/memfs/jasper/aec/libpng16/zlib/jpeg8 + MSVC 运行时）至
   `metcom/libs/eccodes-jpeg/`；
2. vendor 副本的 `gribapi/bindings.py` 打容错补丁：免除 `_eccodes.pyd`
   校验（conda 绑定本无 pyd）、容忍新版 findlibs（需 py≥3.10）缺失；
3. `metcom/retrieval/eccodes_backend.py`：`configure()` 在首次 import
   eccodes/cfgrib 前把 vendor 目录插入 `sys.path[0]` 并前置 `PATH`
   （幂等；`METCOM_ECCODES_DIR` 可指定外部目录，
   `METCOM_ECCODES_DISABLE_VENDOR=1` 可禁用）；
4. 读取器/转换器在 import cfgrib 前调用 `configure()`；JPEG 解码失败时
   输出三条解决途径的明确指引。

验证（SOMEDATA 真实数据）：
- CRA40 **GRIB2 直读** 8/8 文件逐位一致；CMA-MESO **GRIB2 直读** 8/8 逐位
  一致——与 exe-NC 路径、MATLAB 基准完全一致（JPEG2000 为无损标准解码）；
- CRA V1.5 直读不受 vendor 注入影响；
- 35 项 pytest 全绿。
- 读取器对 `.nc` 与 `.grib2` 混合目录仍按时间戳自动配对；exe 预转换与
  `grib2.enabled` 配置节保留为可选回退。

### 5.4 原有单元/回归测试

35 项 pytest 全绿：物理内核逐公式独立验证、合成场等价性、01 模块逐字节回归、
04 插值回归、05 评估/绘图测试。

## 6. 已知限制

- 下载器默认时间范围为示例值，使用前必须配置 CDS API 密钥
  （`CDSAPI/.cdsapirc_test` 模板，key 留空待填）；
- `cartopy.mpl.patch.geos_to_path` 为 cartopy 内部 API
  （0.23.0 验证可用），未来版本若移除需改用 geopandas 绘制掩膜；
- `.mat` 输出由 `scipy.io.savemat` 生成（format 5），字段名与原版一致；
- 并行（joblib）以站点为单位，TempData 会随任务广播到子进程，
  全球尺度数据（ERA5 0.25° 37 层）内存占用与原 MATLAB parfor 相当，默认关闭。
