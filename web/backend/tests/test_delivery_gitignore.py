"""交付物入库规则测试（阶段 4 补充）。

只验证一件事：web/data/products/demo 的演示产品（含 index.json）能随项目分发，
而其他运行期数据（原始资料 / 任务结果 / 缓存 / 正式产品 / node_modules / .env）
仍然被忽略。

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
    "data/dependent/sites/sta_30sits.txt",
    "data/products/standard/README.md",
    "data/products/standard/ERA5/formal.H00",
    "frontend/node_modules/pkg.js",
    "frontend/dist/index.html",
    "backend/.env",
)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture()
def delivery_repo(tmp_path):
    """临时 git 仓库：真实 .gitignore + 真实 demo 数据 + 一份运行期文件样本。"""
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


def test_runtime_data_stays_ignored(delivery_repo):
    """其他运行期数据不得因为放开 demo 而被带进版本库。"""
    _repo, web = delivery_repo
    _git("add", "-A", cwd=web)
    tracked = {line for line in _git("ls-files", cwd=web).splitlines() if line.strip()}

    leaked = sorted(rel for rel in IGNORED_PATHS if rel in tracked)
    assert leaked == [], f"运行期文件被意外入库：{leaked}"

    # 跟踪到的 web 侧文件只应有规则文件、demo 数据和无关的源码样本
    non_demo = sorted(
        rel for rel in tracked if not rel.startswith("data/products/demo/")
    )
    assert non_demo == [".gitignore", "backend/main.py"]


def test_unrelated_data_directories_do_not_exist_in_gitignore(delivery_repo):
    """不做无关放开：仓库根/其他目录没有额外的 data 例外规则。"""
    _repo, web = delivery_repo
    rules = (web / ".gitignore").read_text(encoding="utf-8")
    negations = [line.strip() for line in rules.splitlines() if line.strip().startswith("!")]
    assert negations == ["!data/products/"]

    products_rules = (web / "data" / "products" / ".gitignore").read_text(encoding="utf-8")
    assert "!demo/" in products_rules
    assert "!demo/**" in products_rules
