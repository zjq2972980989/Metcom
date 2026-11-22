"""Web 层统一数据契约。

阶段一把枚举与响应模型定死，后续阶段只扩展字段、不改已有命名，
这样前端三套产品体系（在线计算 / 标准产品 / 定制产品）可以共用同一套参数名。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class TaskStatus(str, Enum):
    """任务生命周期（文档第十四节）。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class TaskType(str, Enum):
    """任务类型，对应任务中心的四个筛选页签。"""

    DOWNLOAD = "DOWNLOAD"
    CALCULATION = "CALCULATION"
    CUSTOM = "CUSTOM"
    TIMEINTERP = "TIMEINTERP"


class OutputParameter(str, Enum):
    """八个输出参数，三套产品体系共用同一命名（取值与计算引擎结果列一致）。

    ``Ps`` / ``vapor_pressure`` 与其余六个参数完全同源：都由引擎
    ``metcom/retrieval/site_retrieval.py`` 随反演一并算出并写入结果文件，
    Web 只做列选择，不在 Web 侧重算。
    """

    PWV = "PWV"
    WVD = "WVD"
    TM = "Tm"
    ZTD = "ZTD"
    ZHD = "ZHD"
    ZWD = "ZWD"
    PS = "Ps"
    VAPOR_PRESSURE = "vapor_pressure"


class CustomMode(str, Enum):
    """定制产品的两种计算模式。"""

    GRID = "grid"
    STATION = "station"


class SourceInfo(BaseModel):
    """单个再分析数据源的对外描述（用户只需要看到这些）。"""

    key: str
    name: str
    time_resolution: str
    spatial_resolution: str
    levels: int
    description: str = ""
    # 阶段 3 起：由后端按"真实可运行"判定，前端据此禁用不可用项
    available: bool = False
    calculate_available: bool = False
    download_available: bool = False
    # 核心引擎是否已具备真实下载器（代码能力）；与"当前机器是否具备下载条件"
    # 的 download_available 区分开：有下载器但缺凭据时 downloader_available=True、
    # download_available=False。
    downloader_available: bool = False
    note: str = ""
    # 阶段一：多变量 GRIB 数据源（CRA40 / CRA V1.5 / CMA-MESO）的真实数据完备性。
    # 只有至少存在一个变量文件组完整的时次，calculate_available 才会为 True；
    # 其余数据源沿用"有数据文件即可"的既有语义（不改变 ERA5 / MERRA-2 行为）。
    has_complete_data: bool = False
    complete_time_count: int = 0
    incomplete_time_count: int = 0
    available_time_range: list[str] | None = None
    diagnostics: list[str] = Field(default_factory=list)


class ParameterInfo(BaseModel):
    """单个输出参数的对外描述。"""

    key: str
    name: str
    unit: str
    description: str = ""


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str
    phase: str
    time: datetime


class EngineInfo(BaseModel):
    """计算引擎探测结果（让前端能显示"引擎是否就绪"）。"""

    available: bool
    package: str
    version: str | None = None
    path: str | None = None
    detail: str = ""


class MetaResponse(BaseModel):
    """前端启动时拉取的静态元信息。"""

    sources: list[SourceInfo]
    parameters: list[ParameterInfo]
    task_status: list[TaskStatus]
    task_types: list[TaskType]
    custom_modes: list[CustomMode]


# --------------------------------------------------------------------------- #
# 在线计算（阶段 3）：数据下载 / 参数计算请求
# --------------------------------------------------------------------------- #


class DownloadRequest(BaseModel):
    """POST /api/download 请求体（时间用 ISO 8601，前端不需要知道引擎的时间格式）。

    已支持两条**真实**下载链路，由 dataset 决定：

    - ``era5``：已验证的 ERA5 pressure-level 下载链路（37 个气压层、4 个变量
      geopotential / relative_humidity / specific_humidity / temperature、NetCDF
      格式、不使用 grid）。
    - ``merra2``：MERRA-2 inst6_3d_ana_Np（NASA GES DISC，00/06/12/18 UTC，
      一天一文件，官方 NetCDF-4/``.nc4``）；需要当前机器已配置 NASA Earthdata
      凭据，且不使用下面三个 ERA5 专属字段。

    下面的字段让请求体可以显式表达 ERA5 配置，省略时取该数据源的已验证默认值，
    因此与前端现有的 dataset + 时间三字段请求完全兼容；传入与数据源不符的配置时
    后端会在创建任务前报 422，不会静默改成别的下载内容。

    输出目录不在这里：结果一律由后台写入该任务的独立结果目录
    ``data/tasks/results/<task_id>/download/``，不接受用户指定的路径。
    """

    dataset: str = Field(min_length=1, description="数据源 key，例如 era5")
    start_time: datetime
    end_time: datetime
    pressure_levels: list[str] | None = Field(
        default=None, description="气压层（hPa）；第一版仅支持已验证的 37 层配置"
    )
    variables: list[str] | None = Field(
        default=None, description="下载变量；第一版仅支持已验证的 4 变量配置"
    )
    output_format: str | None = Field(
        default=None,
        description="输出格式；ERA5 仅支持 nc，MERRA-2 固定为官方 nc4（省略时按数据源默认）",
    )


class CalculationSite(BaseModel):
    """自定义测站（与引擎站点文件的四列一一对应：name lat lon height）。

    这里只做类型声明，取值范围由 ``services.validation.validate_sites`` 统一校验，
    以保证错误提示风格与其它业务校验一致（422 + 中文 detail）。
    高程为**正高**，与引擎站点文件约定相同。
    """

    name: str = Field(description="站点名称，不能含空白字符")
    lat: float = Field(description="纬度 / 度（-90 ~ 90）")
    lon: float = Field(description="经度 / 度（-180 ~ 180）")
    height: float = Field(description="正高 / m")


class CalculationRequest(BaseModel):
    """POST /api/calculation 请求体。

    省略 ``sites``（或显式传 null）时沿用平台默认站点文件（sta_30sits.txt），
    与历史请求完全一致；传入非空列表时由后台为该任务生成专属站点文件。
    显式传入空列表视为非法请求（422）。

    ``output_format`` 省略时使用平台默认格式（txt）；取值范围与引擎真实的
    写入器一一对应，由 ``services.validation.validate_output_format`` 校验。
    """

    dataset: str = Field(min_length=1, description="数据源 key，例如 era5")
    parameters: list[OutputParameter] = Field(default_factory=list)
    start_time: datetime
    end_time: datetime
    sites: list[CalculationSite] | None = Field(
        default=None,
        description="自定义测站列表；省略或为 null 时使用平台默认站点文件",
    )
    output_format: str | None = Field(
        default=None,
        description="结果文件格式：txt | dat | mat；省略时使用平台默认格式",
    )


class TimeInterpRequest(BaseModel):
    """POST /api/timeinterp 请求体：对已完成的参数计算结果做时间维度插值。

    输入只接受**平台自己已经算出并登记在任务记录里**的结果任务
    （``source_task_id``），不接受任何前端传来的路径；服务端按任务 ID 解析
    并在数据目录范围内定位文件，避免任意路径访问。

    与原生 ``metcom.timeinterp.runner.run_timeinterp`` 的契约**逐条对齐**
    （以下都是当前源码事实，不是设计愿望）：

    - 原生只按 ``target_interval`` 在当天已有观测时次之间生成目标时刻网格
      （``np.arange``），不接受"任意一个目标时刻"，因此这里也没有 timestamp
      字段——不为界面好看而伪造原生不具备的能力；
    - 原生的输出列宽**写死为 12 列**（``_TIMEINTERP_SPECS`` = 4 列站点信息 +
      11 列数值，表头由 ``base_headers[:4] + variables`` 拼出），只有恰好 8 个
      插值变量时列数才对得上。因此本阶段固定插值原生默认的 8 个参数，
      **不提供"只插值部分参数"的选项**；``parameters`` 只用于显式表达这一约束，
      传子集会被明确拒绝，而不是被静默忽略或擅自删列；
    - 源结果必须是定宽文本（txt / dat）。MAT 结构体不在原生读取范围内，
      因此会被明确拒绝而不是猜测解析；
    - 源结果必须含齐 8 个参数列：原生对缺列只打印警告并让整列保持 NaN，
      Web 不接受这种静默降级，改为创建任务前就报错并指出缺了哪几列。
    """

    source_task_id: str = Field(min_length=1, description="输入结果所属的任务 ID")
    parameters: list[OutputParameter] | None = Field(
        default=None, description="需要插值的参数；省略时取源结果中全部可用参数"
    )
    target_interval: int | None = Field(
        default=None, description="目标时间间隔（小时）；省略时使用 1 小时"
    )


class TimeInterpInputInfo(BaseModel):
    """一个可作为插值输入的已成功任务（GET /api/timeinterp/inputs）。

    这里只描述**源结果文件里真实存在**的信息（时次、站点数、参数列），
    前端据此让用户选择，不需要也不允许用户手填路径。
    """

    task_id: str
    name: str
    dataset: str
    dataset_name: str
    created_at: datetime
    result_format: str
    interpolatable: bool = False
    reason: str = ""
    parameters: list[str] = Field(default_factory=list)
    missing_columns: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    doy_folders: list[str] = Field(default_factory=list)
    time_labels: list[str] = Field(default_factory=list)
    site_count: int = 0
    hour_count: int = 0
    min_hours_per_day: int = 0
    max_hours_per_day: int = 0


class TimeInterpInputListResponse(BaseModel):
    """插值输入候选列表 + 后端真实支持的目标时间间隔选项。"""

    items: list[TimeInterpInputInfo]
    total: int
    target_intervals: list[int] = Field(default_factory=list)
    default_target_interval: int = 1


class GridRequest(BaseModel):
    """格网（Grid）模式请求体：经纬度范围 + 时间 + 输出参数。

    格网半径规则：引擎把**每个格点当作一个测站**独立反演，因此这里只描述区域。

    这里**故意不提供** ``resolution`` / ``lat_step`` / ``lon_step`` 等字段：
    空间分辨率由数据源自身的原生分辨率决定（``metcom.sources.SourceSpec``
    的 ``spatial_resolution``，顺序为 ``(lat_step, lon_step)``），并且
    ``metcom.retrieval.pipeline`` 会强制校验反演分辨率必须等于该值，
    所以它不是一个可以由用户自由指定的量。取值范围与格点数上限由
    ``services.validation.validate_grid`` 统一校验（422 + 中文 detail）。

    四个范围字段用 ``strict=True``：只接受 JSON 数字（整数或小数），
    **显式拒绝 bool**（否则 ``true`` 会被宽松模式静默转换成 1.0，把客户端
    的类型错误变成一个合法的经纬度）。文本型数字（``"116.0"``）同样不接受，
    调用方应在发送前完成数值转换（前端现有做法即 ``Number(...)``）。
    """

    dataset: str = Field(min_length=1, description="数据源 key，例如 era5")
    parameters: list[OutputParameter] = Field(default_factory=list)
    start_time: datetime
    end_time: datetime
    lon_min: float = Field(strict=True, description="区域最小经度 / 度（-180 ~ 180）")
    lon_max: float = Field(strict=True, description="区域最大经度 / 度（-180 ~ 180）")
    lat_min: float = Field(strict=True, description="区域最小纬度 / 度（-90 ~ 90）")
    lat_max: float = Field(strict=True, description="区域最大纬度 / 度（-90 ~ 90）")


class CustomRequest(GridRequest):
    """POST /api/custom 请求体（V1.0 只支持格网模式）。

    直接继承 ``GridRequest``，格点字段与在线计算的格网模式**完全同源**，
    不另造一套参数名；这里只额外声明 ``mode``。

    ``mode`` 复用 ``CustomMode`` 枚举（``/api/meta`` 已暴露 grid/station），
    但接受大小写不敏感输入（``"GRID"`` / ``"grid"`` 均可），内部统一按枚举值判定。
    ``station`` 目前未实现，由 ``services.custom_service`` 在创建任务前返回 422，
    不会创建任务、也不会走已有的 ``CalculationRequest.sites`` 流程。
    """

    mode: CustomMode

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> Any:
        """把 "GRID" / "Grid" 统一成枚举可识别的 "grid"。"""
        return value.strip().lower() if isinstance(value, str) else value


class TaskSubmittedResponse(BaseModel):
    """在线计算提交结果：前端拿到 task_id 后到任务中心跟踪状态。"""

    task_id: str
    status: TaskStatus
    name: str
    message: str = "任务已提交"


# --------------------------------------------------------------------------- #
# 任务中心（阶段 2）：统一 Task 数据结构
# --------------------------------------------------------------------------- #


class TaskCreate(BaseModel):
    """创建任务请求体。params 只放必要业务参数，不放原始数据。"""

    name: str = Field(min_length=1, max_length=80)
    type: TaskType
    params: dict[str, Any] = Field(default_factory=dict)


class TaskResponse(BaseModel):
    """统一 Task 结构（同时也是本地任务记录的存储结构）。"""

    id: str
    name: str
    type: TaskType
    status: TaskStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: int = 0
    message: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class TaskCreatedResponse(BaseModel):
    """创建任务的返回：前端拿到 id 后即可轮询。"""

    id: str
    status: TaskStatus


class TaskListResponse(BaseModel):
    """任务列表（分页）。"""

    items: list[TaskResponse]
    total: int
    page: int
    page_size: int
    pages: int


class TaskResultResponse(BaseModel):
    """任务结果：未完成 / 成功 / 失败都返回明确状态。"""

    task_id: str
    status: TaskStatus
    ready: bool
    message: str
    result: dict[str, Any] | None = None
    error: str | None = None


# ============================================================================
# 阶段 4：标准产品（成品仓库，只读）
# 契约与 /api/products 路由一一对应；is_demo 用于把"演示数据"与正式产品区分开，
# 前端据此加醒目标注，不允许把演示数据当成正式产品展示。
# ============================================================================


class ProductSourceInfo(BaseModel):
    """标准产品仓库里某个数据源的实际可用信息（只描述真实存在的数据）。"""

    source: str = Field(..., description="产品侧数据源代号，如 ERA5")
    dir_name: str = Field(..., description="原始目录名，如 output_ERA5")
    time_resolution: str = Field(..., description="时间分辨率，如 1h / 6h")
    regions: list[str] = Field(default_factory=list, description="实际存在的区域")
    resolutions: list[str] = Field(default_factory=list, description="实际存在的空间分辨率")
    dates: list[str] = Field(default_factory=list, description="实际存在的日期 YYYYMMDD")
    hours: list[int] = Field(default_factory=list, description="实际存在的时次")
    product_count: int = Field(0, description="该数据源的产品记录数")
    is_demo: bool = Field(False, description="是否来自演示数据目录")


class ProductSourcesResponse(BaseModel):
    """GET /api/products/sources 的响应。"""

    sources: list[ProductSourceInfo]
    demo_roots: list[str] = Field(default_factory=list, description="演示数据目录")
    product_roots: list[str] = Field(default_factory=list, description="实际生效的仓库根")


class ProductFileEntry(BaseModel):
    """单个产品文件（索引 + 磁盘校验后的结果）。"""

    path: str = Field(..., description="相对仓库根的路径（统一正斜杠）")
    filename: str
    hour: int
    size_bytes: int


class ProductEntry(BaseModel):
    """一条产品记录（一个数据源 + 区域 + 分辨率 + 日期）。"""

    source: str
    region: str
    resolution: str
    date: str = Field(..., description="日期 YYYYMMDD")
    hours: list[int]
    file_count: int = 0
    is_demo: bool = False
    files: list[ProductFileEntry] = Field(default_factory=list)


class ProductListResponse(BaseModel):
    """GET /api/products 的响应。"""

    products: list[ProductEntry]
    total: int
    demo_included: bool = Field(True, description="结果中是否包含演示数据")


class ProductDataPreview(BaseModel):
    """GET /api/products/{source}/{region}/{resolution}/{date}/{hour}/data 的响应。"""

    source: str
    region: str
    resolution: str
    date: str
    hour: int
    is_demo: bool = False
    filename: str
    metadata: dict[str, str] = Field(default_factory=dict, description="产品 Header")
    columns: list[str] = Field(default_factory=list)
    units: dict[str, str] = Field(default_factory=dict)
    data: list[list[float]] = Field(default_factory=list)
    total_rows: int = 0
    returned_rows: int = 0
