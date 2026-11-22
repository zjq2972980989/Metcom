# MetCom Web V1.0

基于多源气象再分析资料的对流层参数精细反演平台 —— Web 前端 + FastAPI 服务层 + 现有 Python MetCom 计算引擎。

当前进度：**阶段 4（标准产品）**。任务中心（阶段 2）、在线计算的数据下载 / 参数计算 /
定制 / 时间插值（阶段 3）与标准产品查询-预览-下载（阶段 4）均已可用。

标准产品模块复用同学交付的 `metcom_product` 包（原样集成，未改写读取逻辑），
Web 侧只补：仓库根配置、跨平台索引路径、数据源标识统一与接口契约。

## 目录结构

```
web/
├── backend/              FastAPI 服务层
│   ├── main.py           应用装配：CORS、路由挂载、错误处理、启动自检
│   ├── config.py         配置（METCOM_WEB_* 环境变量可覆盖）
│   ├── routers/          路由层（health / products / download / calculation / custom / tasks）
│   ├── services/         业务服务层
│   │   ├── task_service.py     任务中心唯一业务入口（创建 / 查询 / 状态流转）
│   │   ├── task_store.py       本地任务记录读写（JSON + 进程内锁 + 原子替换）
│   │   └── product_service.py  标准产品查询 / 详情 / 预览 / 下载（阶段 4）
│   ├── tasks/            后台任务执行层
│   │   ├── runner.py        任务生命周期：PENDING → RUNNING → SUCCESS / FAILED
│   │   └── mock_tasks.py    模拟任务处理函数（阶段 2，不调用 MetCom）
│   ├── engine/adapter.py 与现有 MetCom 引擎之间的唯一边界
│   ├── product/paths.py  标准产品仓库定位层（路径归一化，阶段 4）
│   ├── metcom_product/   同学交付的标准产品读取包（原样集成，阶段 4）
│   ├── models/schemas.py 统一数据契约
│   └── requirements-web.txt
├── frontend/             Vue 3 + Vite
│   └── src/{router,layouts,components,views,api,composables,assets,stores}
├── data/                 运行期数据（自动创建，已被 .gitignore 忽略）
│   ├── tasks/            任务记录与结果：tasks.json + results/
│   └── products/         标准产品仓库：standard/（正式，空）+ demo/（演示数据）
├── start.bat             一键启动前后端（实际调用下面两个脚本）
└── README.md
```

启动脚本说明：`start.bat` 只负责检查环境并用 `start /D` 打开两个窗口，
真正的启动命令在 `backend/start-backend.bat`（uvicorn + UTF-8 控制台）与
`frontend/start-frontend.bat`（npm run dev）。

注意：**这三个 .bat 必须保持纯 ASCII**。cmd.exe 按 OEM 代码页（中文系统为 936）
解析批处理文件，UTF-8 的中文注释会把命令行切碎，导致启动失败（曾经踩过）。
所以批处理里不写中文，中文日志由 `PYTHONUTF8=1` + `chcp 65001` 在窗口内保证正常显示。
```

现有 Python MetCom 引擎保持在仓库根目录 `metcom/`，Web 不复制、不修改它，只通过
`backend/engine/adapter.py` 调用（对应关系：数据下载→downloader、格网→gridgen、
反演→retrieval、时间处理→timeinterp、评价→evaluation、数据源→sources.py）。

## 启动

```bat
web\start.bat
```

- 后端：http://127.0.0.1:8000/api/docs （Swagger 文档）
- 前端：http://127.0.0.1:5173

手动启动：

```bat
cd web\backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

cd web\frontend
npm run dev
```

前端开发服务器把 `/api` 代理到 `127.0.0.1:8000`，因此前端代码里只写相对路径。

## 已完成的接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务健康检查（左侧导航底部的连通指示） |
| GET | `/api/meta` | 数据源 / 参数 / 任务枚举（下拉选项不在前端写死） |
| GET | `/api/engine` | 计算引擎可用性与版本 |
| POST | `/api/tasks` | 创建任务（阶段 2） |
| GET | `/api/tasks` | 任务列表，支持 `?page=&page_size=&type=`（阶段 2） |
| GET | `/api/tasks/{task_id}` | 任务详情 / 状态轮询（阶段 2） |
| GET | `/api/tasks/{task_id}/result` | 任务结果 / 错误（阶段 2） |
| GET | `/api/products` | 标准产品目录，支持 `?source=&region=&resolution=&date=&include_demo=`（阶段 4） |
| GET | `/api/products/sources` | 标准产品数据源 / 区域 / 分辨率 / 日期 / 时次（只列真实存在的数据，阶段 4） |
| GET | `/api/products/{source}/{region}/{resolution}/{date}` | 某日详情（时次 + 文件，阶段 4） |
| GET | `/api/products/{source}/{region}/{resolution}/{date}/{hour}/data` | 产品数据预览，`?limit=` 控制行数（阶段 4） |
| GET | `/api/products/{source}/{region}/{resolution}/{date}/{hour}/download` | 产品文件下载（阶段 4） |
| GET | `/api/products/{source}/{region}/{resolution}/{date}/archive` | 产品日期级 ZIP 下载（阶段 4） |

其余接口按规划已注册，统一返回 `501` 并在 detail 中标注实现阶段：

`POST /api/download`、`POST /api/calculation`、`GET /api/calculation/{task_id}`（阶段 3）、
`POST /api/custom`、`GET /api/custom/{task_id}`（阶段 5）、`GET /api/tasks/{task_id}/download`（阶段 3）。

## 标准产品（阶段 4）

产品是**预先算好的成品**，Web 只做「查询 → 预览 → 下载」，不重算、不生成：

下载有两级：单时次返回原始 ``MetCom_*.Hxx`` 文本；日期级 ZIP 把该日期
全部时次按仓库原相对目录原样打包。ZIP 只是传输容器，不是新的数据格式；
每份文件仍保留 14 行 Header、固定参数顺序和 UTF-8 编码。

```
Vue StandardProducts.vue → api/products.js → HTTP
    → routers/products.py → services/product_service.py
    → backend/product/paths.py → metcom_product.reader（同学交付的包）
```

- 读取逻辑来自同学交付的 `metcom_product`（原样复制到 `web/backend/metcom_product/`，
  未改写 `reader.py` / `standard.py` / `validator.py`）；Web 侧只补了三件事：
  仓库根配置（`METCOM_WEB_METCOM_STANDARD_*_DIR` 可覆盖）、
  跨平台索引路径解析（索引里 `ERA5\BTH\...` 与 `ERA5/BTH/...` 都能读）、
  数据源标识统一（`era5` 与 `ERA5` 等价）。
- 只注册一个 FastAPI 服务（`web/backend/main.py`）。同学交付包里的独立
  `run_api.py` 与 `metcom_product/api/` 子包**未集成**，否则会与现有服务抢占
  `127.0.0.1:8000`、与 `/api/products*` 路由冲突。

### 产品仓库

| 目录 | 内容 | 说明 |
| --- | --- | --- |
| `web/data/products/standard` | 正式规格产品（当前为空） | ERA5 / China / 1×1 / 6 h 等，数据源到位后由 `metcom_product.publish` 上新 |
| `web/data/products/demo` | 演示数据（BTH / 0.125°） | 交付方提供的样例，仅用于验证链路 |

- **演示数据随项目分发**：`web/data/products/demo/`（112 个产品 + `index.json` + README）
  已入库，克隆仓库后无需再向交付方索取样例数据。
- 入库范围由两条规则精确界定，其余运行期数据一律不入库：
  - `web/.gitignore`：`data/*` 忽略全部运行期数据，只把 `data/products/` 放开为可遍历；
  - `web/data/products/.gitignore`：`*` 忽略 `products/` 下所有内容，只放行 `demo/`。
  因此 `standard/`（正式产品）与后续新增的正式仓库根默认不入库，
  新增正式数据不需要再改规则。
  （注意：git 无法穿过被忽略的父目录再放行子文件，所以不能写成
  `data/` + `!data/products/demo/**` 这种形式。）
- 正式产品目录 `web/data/products/standard` 为空时，接口只返回演示数据，
  不造数据、不报错。
- 索引路径统一用正斜杠；如从旧版交付物继承到反斜杠索引也无需处理，读取层会自动兼容。
- 响应里每条记录都带 `is_demo`，前端对演示数据加琥珀色「演示数据」标注，
  **不允许把演示数据当正式产品展示**。
- 下拉选项（数据源 / 区域 / 分辨率 / 日期 / 时次）全部由
  `GET /api/products/sources` 与 `/api/products` 的真实数据算出，没有数据的
  日期或时次不会出现在界面上；产品不存在时返回 404（而不是 500 或空数据）。
## 任务中心（阶段 2）

任务中心是所有异步任务的唯一入口，调用链固定为：

```
Vue TaskCenter.vue → api/tasks.js → HTTP
    → routers/tasks.py → services/task_service.py → tasks/runner.py → mock_tasks.py
    → data/tasks/tasks.json
```

- 任务状态只有四个：`PENDING`（等待中）/ `RUNNING`（处理中）/ `SUCCESS`（已完成）/ `FAILED`（失败）。
- 任务类型只有三个：`DOWNLOAD`（数据下载）/ `CALCULATION`（参数计算）/ `CUSTOM`（定制产品）。
- 路由不直接读写 JSON：所有任务都经过 `task_service`，`download` / `calculation` / `custom`
  三个业务入口在阶段三接入时也必须走同一个 service。
- V1.0 不使用数据库、Redis、Celery、消息队列：任务记录落在 `web/data/tasks/tasks.json`，
  结果元信息与结果文件落在 `web/data/tasks/results/`，后台执行用 FastAPI `BackgroundTasks`。
- 并发：`task_store.py` 用进程内 `threading.RLock` 串行化「读 → 改 → 写」，落盘用
  `临时文件 + os.replace` 原子替换，避免并发请求写坏 tasks.json（只覆盖单进程部署）。
- 错误：任务 ID 不存在返回 `404 {"detail": "任务不存在"}`；任务失败时状态为 `FAILED`，
  `error` 只保存简洁说明，完整 traceback 只写日志，不返回给用户。
- 日志：`metcom.web.tasks` 记录 `Task created / started / completed / failed`。

### 阶段三如何接真实业务

把 `backend/tasks/mock_tasks.py` 的 `HANDLERS` 换成真实的 download / calculation / custom
handler 即可（键与函数签名保持不变），`runner.py` 与任务中心前端都不用改。
`params.simulate_failure=true` 与 `params.dataset` 只是模拟阶段的开发测试参数，
真实 handler 落地后不再使用。

### 任务中心前端

页面路径 `/tasks`（左侧导航「任务中心」）。任务列表每 2 秒轮询一次，
仅在存在 `PENDING` / `RUNNING` 任务时开启，全部结束后自动停止，离开页面即停止；
「数据源」列读的是 `task.params.dataset`（没有则显示 `—`），显示名来自 `/api/meta`；
页面顶部的「创建测试任务」是**开发测试**入口，不调用真实计算。

## 页面与导航

左侧 Sidebar（`components/AppSidebar.vue`）：MetCom 标识在顶部，下面纵向排列
首页 / 在线计算（数据下载、参数计算）/ 标准产品 / 定制产品 / 任务中心 / 关于平台，底部是 `/api/health` 连通指示。

顶部不再有横向菜单，只保留页面顶部中间的搜索框与右上角的通知 / 用户区域（`views/Home.vue` 里的 `.hero-topbar`）。

首页结构固定为：Hero（地球影像 + 深蓝蒙版 + 三个入口按钮：在线计算 / 标准产品 / 定制产品）→ 核心能力 → 多源数据 + 对流层参数 → 页脚。不放新闻、统计、复杂图表与业务功能。

## 标识与视觉规范

标识（正式版本，不再自绘图形）

- `frontend/src/assets/logo-metcom.png` —— 深蓝几何字母，M 内含气流曲线，C 内含橙金弧线。
  这是项目正式 Logo：页面里只做等比缩放，不重绘、不拉伸、不用字体另写一遍 "MetCom"。
- `frontend/src/assets/logo-metcom-light.png` —— 同一份标识的浅色（反白）版本：
  只用像素映射把深蓝换成白色，橙金弧线与几何形状完全保持原样。用于深色底（首页 Hero）。
- 左侧 Sidebar 标识高度固定 32px；≤640px 收成 30px 的 M 字形；Hero 中的浅色标识 64px（≤980px 56px、≤640px 46px）。
  宽度一律自适应，保持 900 × 190 的原始宽高比。
- `frontend/src/assets/logo-metcom-mark.png` —— 取自 favicon 的 M 字形（256 × 256），
  只在 ≤640px 的图标栏里替代完整标识。
- 浏览器图标：`frontend/public/favicon.png`，取自标识中的 M 字形。
- 旧的自绘图形（`BrandMark.vue`、`web/brand/`、`public/favicon.svg`）与旧的横向导航 `AppHeader.vue` 已删除，不要再引用。

配色

- 颜色全部取自标识：深蓝 `--navy: #18283b`、橙金 `--amber: #f4a12a`。
- 页面底色 `--bg: #eef0f4`（明确的浅灰，让白色卡片浮起来），内容面 `--surface: #ffffff`，
  靠 1px 描边与留白分层，而不是靠颜色块。
- 全站只有一套亮色主题，不做深色主题，不写 `prefers-color-scheme` 分支。

首页与视觉约束

- Hero 是沉浸式图片区：`frontend/src/assets/hero-earth.jpg`（真实地球/大气影像）铺满整块，
  上面叠加深蓝透明渐变蒙版，左暗右亮；内容直接压在图片上并左对齐。
  结构固定为 `.hero > .hero-media + .hero-overlay + .hero-content-wrap`，
  **不允许再出现白色卡片、玻璃拟态、发光或粒子**。
- Hero 内自上而下只有四项：浅色标识 → 两行白色主标题 → 两行说明 → 三个按钮
  （开始在线计算 / 浏览标准产品 / 开始定制产品）。不放小标签、数据源列表、参数列表与统计数字。
- Hero 高度：桌面 680px、≤980px 640px、≤640px 660px；蒙版与背景定位见 `styles.css` 的
  `.hero-overlay` / `.hero-media`。
- 禁止出现：渐变辉光、霓虹蓝、粒子、六边形纹理、3D 地球、巨大图标、复杂玻璃拟态、装饰线堆叠。
- 内容最大宽度 1280px，区块之间保持大留白；卡片一律宽而舒展，宁少勿多。
- 新增组件请只用 `styles.css` 里的变量取色，不要写死颜色值。

左侧导航（`.app-sidebar`）

- 固定定位、整屏高度，宽度 `--sidebar-width`：桌面 200px、≤980px 176px、≤640px 64px（图标栏）。
  右侧内容用 `.app-body { margin-left }` 让位；`.hero` 用等值负 margin 抵消，因此 Hero 盒子尺寸与背景参数保持原样。
- 灰蓝玻璃：`rgba(38, 56, 86, 0.64)` + `backdrop-filter: blur(16px) saturate(1.12)`，
  `border-right: 1px solid rgba(255, 255, 255, 0.14)`。saturate 略高于 1，让 Hero 的蓝透上来，
  形成低饱和的灰蓝而不是纯灰或纯蓝。alpha 是唯一的“通透度旋钮”：调低更透、调高更沉稳；
  调低到 0.58 以下时，压在浅灰内容区上的白字会开始发虚。
  只有 Sidebar 自己可以半透明，**不允许**给 `.hero`、`body`、`.page` 加蒙版 / 滤镜 / `::before`。
- 选中态是 `rgba(255, 255, 255, 0.15)` 的玻璃块（hover 0.09），不使用鲜艳蓝。
- 导航文字 `rgba(255, 255, 255, 0.88)`，带一层极轻的 `text-shadow: 0 1px 2px rgba(12, 22, 38, 0.3)`，
  保证压在较浅玻璃上时字缘仍然清晰。

核心能力模块卡（`.cap-card`）

- 固定 2 × 2 四张，不增删、不做横向滚动、不做流程图。
- 卡片 20px 圆角、`1px solid var(--sky-line)`、白底、极轻阴影，内边距 30 / 32px；
  悬停上移 3px，描边转成 `--sky-line-hover` 浅蓝，装饰同步轻微位移，220ms。
- 结构固定为：`01 / 02 / 03 / 04` 编号（11.5px 等宽字）→ 标题（21px，600）+ 一句话说明（13.5px）
  → 底部标签 chip（`--sky-50` 底、`--sky-100` 描边、7px 圆角）。
- 每张卡片右侧有一块约 8% 透明度的科研线稿（`components/CapDecor.vue`：数据仓 / 拟合曲线 /
  数据产品 / 空间网格）。只做背景纹理、不参与交互；新增卡片按同一视框
  （220 × 170、1.4 描边、currentColor、无填充）加一个分支即可。

## 约定

- 调用链固定为 `router → service → engine.adapter → metcom`，路由与 service 不直接 import 引擎。
- 六个输出参数（PWV / WVD / Tm / ZTD / ZHD / ZWD）与五个数据源（ERA5 / MERRA-2 / CRA40 / CRA V1.5 / CMA-MESO）命名三套产品体系统一。
- 错误体统一为 `{"detail": ..., "path": ...}`。
- 阶段 1 不引入 UI 组件库、状态管理库与数据库；任务状态存储方案在阶段 2 决定。
