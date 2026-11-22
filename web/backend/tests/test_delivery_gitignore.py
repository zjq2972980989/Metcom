"""交付物入库规则测试。

验证 web/data/products/demo 的演示产品和计算必需的小型依赖能随项目分发，
而其他运行期数据（原始资料 / 任务结果 / 缓存 / 正式产品数据 / node_modules /
.env）仍然被忽略。

做法：把真实的 .gitignore 规则复制到临时仓库，按真实目录形态铺一遍文件，
再 `git add -A` 看 git 实际跟踪了什么 —— 规则本身的行为由 git 裁决，
不做字符串匹配。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = BACKEND_DIR.parent / "frontend"
WEB_DIR = BACKEND_DIR.parent
DEMO_DIR = WEB_DIR / "data" / "products" / "demo"

# 必须保持"不入库"的运行期路径（有内容也要被忽略）
IGNORED_PATHS = (
    "data/raw/era5/era5_global.nc",
    "data/tasks/tasks.json",
    "data/tasks/results/task.json",
    "data/cache/intermediate.bin",
    "data/products/standard/ERA5/formal.H00",
    "frontend/node_modules/pkg.js",
    "frontend/dist/index.html",
    "backend/.env",
)

# 计算与测试必需的随仓库资产
REQUIRED_DEPENDENCIES = (
    "data/dependent/egm2008/geoidheight_EGM2008_0250.mat",
    "data/dependent/egm2008/geoidheight_EGM2008_0500_0625.mat",
    "data/dependent/sites/sta_30sits.txt",
    "data/dependent/sites/sta_merra2_30sits.txt",
)
STANDARD_README = "data/products/standard/README.md"


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture()
def delivery_repo(tmp_path):
    """临时 git 仓库：真实 .gitignore + 真实 demo 数据 + 运行期/可分发文件样本。"""
    if shutil.which("git") is None:
        pytest.skip("环境里没有 git，跳过交付规则校验")
    if not DEMO_DIR.is_dir():
        pytest.skip(f"演示数据目录不存在：{DEMO_DIR}")

    repo = tmp_path / "repo"
    web = repo / "web"
    web.mkdir(parents=True)

    # 真实规则
    shutil.copy(WEB_DIR / ".gitignore", web / ".gitignore")
    (web / "data" / "products").mkdir(parents=True)
    shutil.copy(WEB_DIR / "data" / "products" / ".gitignore", web / "data" / "products" / ".gitignore")

    # 真实演示数据
    shutil.copytree(DEMO_DIR, web / "data" / "products" / "demo")

    # 运行期样本（都必须被忽略）
    for rel in IGNORED_PATHS:
        target = web / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("runtime", encoding="utf-8")

    # 可分发资产样本（都必须被跟踪）
    for rel in (*REQUIRED_DEPENDENCIES, STANDARD_README):
        target = web / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("distributable", encoding="utf-8")

    (web / "backend").mkdir(parents=True, exist_ok=True)
    (web / "backend" / "main.py").write_text("app = None", encoding="utf-8")

    _git("init", "-q", cwd=repo)
    return repo, web


def test_demo_products_are_distributable(delivery_repo):
    """112 个演示产品 + index.json + README 必须被 git 跟踪。"""
    _repo, web = delivery_repo
    _git("add", "-A", cwd=web)
    tracked = {line for line in _git("ls-files", cwd=web).splitlines() if line.strip()}

    expected = sorted(
        path.relative_to(DEMO_DIR).as_posix()
        for path in DEMO_DIR.rglob("*")
        if path.is_file()
    )
    assert expected, "演示数据目录里没有文件"
    assert len([p for p in expected if Path(p).name.startswith("MetCom_")]) == 112

    missing = [
        rel for rel in expected
        if f"data/products/demo/{rel}" not in tracked
    ]
    assert missing == [], f"演示文件没有被跟踪：{missing[:5]}（共 {len(missing)} 个）"

    assert "data/products/demo/index.json" in tracked
    assert "data/products/demo/README.md" in tracked


def test_required_assets_are_distributable(delivery_repo):
    """计算依赖与正式产品目录说明必须随仓库分发。"""
    _repo, web = delivery_repo
    _git("add", "-A", cwd=web)
    tracked = {line for line in _git("ls-files", cwd=web).splitlines() if line.strip()}

    missing = sorted(
        rel for rel in (*REQUIRED_DEPENDENCIES, STANDARD_README) if rel not in tracked
    )
    assert missing == [], f"必需资产没有被跟踪：{missing}"


def test_runtime_data_stays_ignored(delivery_repo):
    """其他运行期数据不得因为放开可分发资产而被带进版本库。"""
    _repo, web = delivery_repo
    _git("add", "-A", cwd=web)
    tracked = {line for line in _git("ls-files", cwd=web).splitlines() if line.strip()}

    leaked = sorted(rel for rel in IGNORED_PATHS if rel in tracked)
    assert leaked == [], f"运行期文件被意外入库：{leaked}"

    # 跟踪到的 web 侧文件只应有规则文件、可分发资产、demo 数据和无关源码样本
    non_demo = sorted(
        rel for rel in tracked if not rel.startswith("data/products/demo/")
    )
    expected_non_demo = sorted(
        (
            ".gitignore",
            "backend/main.py",
            "data/products/.gitignore",
            STANDARD_README,
            *REQUIRED_DEPENDENCIES,
        )
    )
    assert non_demo == expected_non_demo


def test_ignore_negations_are_scoped(delivery_repo):
    """入库例外只覆盖计算依赖、标准产品说明和 demo。"""
    _repo, web = delivery_repo
    rules = (web / ".gitignore").read_text(encoding="utf-8")
    negations = [line.strip() for line in rules.splitlines() if line.strip().startswith("!")]
    assert negations == [
        "!data/dependent/",
        "!data/dependent/**",
        "!data/products/",
    ]

    products_rules = (web / "data" / "products" / ".gitignore").read_text(encoding="utf-8")
    assert "!standard/" in products_rules
    assert "!standard/README.md" in products_rules
    assert "!demo/" in products_rules
    assert "!demo/**" in products_rules
