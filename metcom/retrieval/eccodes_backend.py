"""eccodes 后端选择：让纯 Python 环境具备 JPEG2000 GRIB 解码能力。

背景（详见 docs/porting_notes.md "GRIB 直读重构"章节）：
  - pip 版 eccodes 轮子为 vcpkg 构建，未编译 JPEG2000 解码器，无法解码
    CRA40 / CMA-MESO 的 JPEG 压缩 GRIB2（CRAV15 为简单打包可直读）；
  - 原版 GribToNcConverter_cfgrib.exe（cfgribTrans 项目产物）内嵌一套
    已验证可用的组合：eccodes C 库 2.42.0（Jasper 后端）+ 纯 Python 绑定
    eccodes/gribapi 2.37.0（conda-forge 构建，无 pyd）；
  - 本包将该整套运行时（Python 绑定 + 10 个 DLL）提取至
    ``metcom/libs/eccodes-jpeg/``，在首次 import eccodes/cfgrib 之前调用
    :func:`configure` 把它置于 sys.path 最前，即可在 pip 环境中获得与 exe
    相同的解码能力，GRIB 全部直读，无需 GRIB→NC 预转换。

机制：``configure`` 将 vendor 目录插入 ``sys.path[0]``（使 ``import
eccodes/gribapi`` 解析到 vendor 的 2.37.0 纯 Python 绑定），并把 vendor
目录前置到 ``PATH``（供 eccodes.dll 的依赖 jasper/jpeg8/zlib 等 DLL 解析）。
vendor 副本的 ``gribapi/bindings.py`` 已打容错补丁（免除 _eccodes.pyd
校验、容忍 findlibs 缺失），以 cffi ABI 模式直接加载同目录的
eccodes.dll 2.42.0——与 exe 的运行时组合完全一致。

环境变量：
  - ``METCOM_ECCODES_DIR``: 显式指定 vendor 目录（含 eccodes/g、gribapi/ 与 DLL）；
  - ``METCOM_ECCODES_DISABLE_VENDOR=1``: 禁用注入（用环境自带 eccodes，
    此时 CRA40/CMA-MESO 直读将因缺 JPEG2000 解码器而失败）。

注意：``eccodes/`` 子目录内的 10 个 DLL **不可裁剪**——cffi dlopen 使用
LOAD_WITH_ALTERED_SEARCH_PATH，依赖 DLL 从该目录解析（根目录副本供 PATH
搜索备用），实测删减会导致 cfgrib 引擎注册失败。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_VENDOR_SUBDIR = "libs/eccodes-jpeg"
_VENDOR_DLLS = ("eccodes.dll", "eccodes_memfs.dll", "jasper.dll",
                "aec.dll", "libpng16.dll")
_CONFIGURED = False


def vendor_dir() -> Path | None:
    """包内 vendor 目录（Python 绑定与 DLL 齐全时返回，否则 None）。"""
    base = Path(__file__).resolve().parents[1] / _VENDOR_SUBDIR   # metcom/libs/...
    if (base / "eccodes" / "__init__.py").is_file() \
            and (base / "gribapi" / "bindings.py").is_file() \
            and all((base / d).is_file() for d in _VENDOR_DLLS):
        return base
    return None


def configure() -> None:
    """在首次 import eccodes/cfgrib 之前调用（幂等）。

    优先级：
      1. 环境变量 ``METCOM_ECCODES_DIR`` 显式指定的 vendor 目录；
      2. 包内 vendor 目录 ``metcom/libs/eccodes-jpeg/``；
      3. ``METCOM_ECCODES_DISABLE_VENDOR=1`` 时跳过注入；
      4. 均不满足 → no-op（pip 轮子原样：CRAV15 可直读，JPEG 源将报错）。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if os.environ.get("METCOM_ECCODES_DISABLE_VENDOR"):
        return

    explicit = os.environ.get("METCOM_ECCODES_DIR")
    if explicit and (Path(explicit) / "eccodes" / "__init__.py").is_file():
        target = Path(explicit)
    else:
        target = vendor_dir()
    if target is None:
        return

    for mod in ("eccodes", "gribapi", "cfgrib"):
        if mod in sys.modules:
            raise RuntimeError(
                f"eccodes_backend.configure() 必须在 import {mod} 之前调用"
                "（vendor 注入已迟）"
            )

    target_str = str(target)
    if target_str not in sys.path:
        sys.path.insert(0, target_str)
    path = os.environ.get("PATH", "")
    if target_str not in path.split(os.pathsep):
        os.environ["PATH"] = target_str + os.pathsep + path


def jpeg_decode_hint(e: Exception) -> str:
    """为 JPEG 解码失败（FunctionalityNotEnabledError 等）生成解决指引。"""
    has_vendor = vendor_dir() is not None
    lines = [
        f"GRIB2 数值解码失败: {type(e).__name__}: {e}",
        "原因: 当前 eccodes 库缺少 JPEG2000 解码器（CRA40/CMA-MESO 的 GRIB2"
        " 为 JPEG 压缩；CRAV15 不受影响）。",
        "解决途径:",
        "  1. 确认包内 metcom/libs/eccodes-jpeg/ 完整（"
        + ("存在" if has_vendor else "缺失！请从 cfgribTrans/dist 的 zip 重新提取")
        + "），且未被 METCOM_ECCODES_DISABLE_VENDOR=1 禁用；",
        "  2. 或安装带 JPEG 支持的 eccodes（如 conda: conda install -c conda-forge"
        " eccodes python-eccodes，并用 METCOM_ECCODES_DIR 指向其目录）；",
        "  3. 或使用原版 GribToNcConverter_cfgrib.exe 预转换为 NC 后读取。",
    ]
    return "\n".join(lines)
