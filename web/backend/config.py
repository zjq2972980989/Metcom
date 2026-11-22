"""MetCom Web 后端配置。

路径全部由文件位置推导，不依赖启动时的工作目录：

    PROJECT_ROOT  MetCOM 仓库根（现有 Python MetCom 计算引擎所在）
    WEB_DIR       web/（Web 平台根，前后端同层）
    data_dir      运行期数据目录：raw / products / tasks / cache
    tasks_dir     任务记录与结果：tasks/tasks.json + tasks/results/

环境变量统一前缀 ``METCOM_WEB_``，例如 ``METCOM_WEB_PORT=9000``。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent      # web/backend
WEB_DIR = BACKEND_DIR.parent                        # web/
PROJECT_ROOT = WEB_DIR.parent                       # MetCOM/


class Settings(BaseSettings):
    """全部配置项（后续阶段只增不改）。"""

    model_config = SettingsConfigDict(
        env_prefix="METCOM_WEB_",
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MetCom Web API"
    version: str = "0.3.0"
    phase: str = "阶段 3：在线计算"

    host: str = "127.0.0.1"
    port: int = 8000

    # 阶段 2 的模拟任务执行器：每个进度节点之间的等待秒数。
    # 只影响 mock_tasks.py，接入真实业务后此项失效。
    task_mock_step_seconds: float = 0.6

    # ---- 阶段 3：现有 Python MetCom 引擎的资源位置（全部由后台决定，前端不参与）----
    # 原始再分析资料根目录，按数据源分子目录：<root>/<数据源 key>/
    # 默认 web/data/raw；如需指向仓库外的资料盘，用 METCOM_WEB_METCOM_DATA_ROOT 覆盖。
    metcom_data_root: Path | None = None
    # 引擎依赖文件（EGM2008 改正文件等）目录
    metcom_dependent_dir: Path | None = None
    # 站点列表文件（name lat lon h）；为空时用 dependent/sites/sta_30sits.txt
    metcom_site_file: Path | None = None
    # CDS API 凭据文件（.cdsapirc）；为空时按 backend/.cdsapirc、~/.cdsapirc 依次查找
    metcom_cdsapirc: Path | None = None
    # 反演结果输出格式：txt | dat | mat（与引擎 output_format 一致）
    metcom_output_format: str = "txt"
    # 结果目录整体打包下载的体积上限（MB），超过时提示改用数据目录
    result_zip_max_mb: int = 512
    # 标准产品按日期整体打包下载的体积上限（MB）
    product_zip_max_mb: int = 512

    # 前端开发服务器地址（CORS 白名单，仅开发期需要）
    dev_origins: list[str] = [
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ]

    data_dir: Path = WEB_DIR / "data"
    project_root: Path = PROJECT_ROOT

    @property
    def raw_dir(self) -> Path:
        """原始再分析资料目录（按数据源分子目录）。"""
        return self.data_dir / "raw"

    @property
    def products_dir(self) -> Path:
        """标准产品目录。"""
        return self.data_dir / "products"

    @property
    def tasks_dir(self) -> Path:
        """任务过程数据与结果目录。"""
        return self.data_dir / "tasks"

    @property
    def cache_dir(self) -> Path:
        """中间过程缓存目录。"""
        return self.data_dir / "cache"

    @property
    def tasks_file(self) -> Path:
        """本地任务记录文件（V1.0 不上数据库）。"""
        return self.tasks_dir / "tasks.json"

    @property
    def results_dir(self) -> Path:
        """任务结果文件目录（阶段 2 起由任务执行器写入）。"""
        return self.tasks_dir / "results"

    # ---- 阶段 4：标准产品（成品仓库，Web 只读；产品由 metcom_product 生成）----
    # 演示产品目录（同学交付的 demo 数据，必须在界面上标注为演示数据）
    metcom_standard_demo_dir: Path | None = None
    # 正式产品目录（China 1x1 等正式规格；当前无数据，目录存在即为空）
    metcom_standard_products_dir: Path | None = None

    @property
    def standard_demo_dir(self) -> Path:
        """演示标准产品根目录（web/data/products/demo）。"""
        if self.metcom_standard_demo_dir:
            return Path(self.metcom_standard_demo_dir)
        return self.products_dir / "demo"

    @property
    def standard_products_dir(self) -> Path:
        """正式标准产品根目录（web/data/products/standard）。"""
        if self.metcom_standard_products_dir:
            return Path(self.metcom_standard_products_dir)
        return self.products_dir / "standard"

    @property
    def standard_product_roots(self) -> list[Path]:
        """标准产品仓库根目录列表：正式优先，演示其次；不存在的目录由读取层忽略。"""
        return [self.standard_products_dir, self.standard_demo_dir]

    @property
    def engine_data_root(self) -> Path:
        """现有 MetCom 引擎读取原始资料的根目录。"""
        return Path(self.metcom_data_root) if self.metcom_data_root else self.raw_dir

    @property
    def engine_dependent_dir(self) -> Path:
        """引擎依赖文件（EGM2008 等）目录。"""
        if self.metcom_dependent_dir:
            return Path(self.metcom_dependent_dir)
        return self.data_dir / "dependent"

    @property
    def engine_egm_dir(self) -> Path:
        """EGM2008 大地水准面改正文件目录。"""
        return self.engine_dependent_dir / "egm2008"

    @property
    def engine_site_file(self) -> Path:
        """默认站点列表文件。"""
        if self.metcom_site_file:
            return Path(self.metcom_site_file)
        return self.engine_dependent_dir / "sites" / "sta_30sits.txt"

    def ensure_dirs(self) -> list[Path]:
        """确保运行期目录存在，返回本次新建的目录。"""
        created: list[Path] = []
        for path in (
            self.raw_dir,
            self.products_dir,
            self.tasks_dir,
            self.results_dir,
            self.cache_dir,
            self.engine_egm_dir,
            self.engine_site_file.parent,
        ):
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
                created.append(path)
        return created


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内配置单例。"""
    return Settings()
