"""GRIB 直读 vendor 运行时测试（eccodes_backend + 真实 GRIB2 数值解码）。

SOMEDATA / vendor 缺失时自动跳过。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.retrieval.eccodes_backend import configure, vendor_dir

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CRA40_GRIB = (PROJECT_ROOT.parent / "SOMEDATA" / "ReanalysisData" / "data_CRA40"
              / "20240101" / "CRA40_GPH_2024010100_GLB_0P25_HOUR_V1_0_0.grib2")


def test_vendor_runtime_complete():
    """vendor 运行时应完整（Python 绑定 + 全部 DLL 随包分发）。"""
    vd = vendor_dir()
    assert vd is not None, (
        f"vendor 运行时不完整: {PROJECT_ROOT / 'metcom' / 'libs' / 'eccodes-jpeg'}"
        " 下缺少 Python 绑定或 DLL")
    assert (vd / "eccodes" / "__init__.py").is_file()
    assert (vd / "gribapi" / "bindings.py").is_file()


def test_vendor_jpeg_grib_decode():
    """vendor 注入后，cfgrib 应能解码 JPEG2000 压缩的 CRA40 GRIB2 数值段。"""
    if not CRA40_GRIB.is_file():
        pytest.skip("SOMEDATA 测试数据不在本机")
    configure()  # 必须在 eccodes/cfgrib 导入前
    import numpy as np
    import xarray as xr

    ds = xr.open_dataset(
        str(CRA40_GRIB), engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"},
                        "indexpath": ""})
    arr = np.asarray(ds["gh"].values)
    assert arr.shape == (47, 721, 1440)
    assert np.isfinite(arr).all()
    # 位势高度物理量级：地面 ~ 数十米，顶层 ~ 5 万米
    assert 0 < arr.max() < 60000
    ds.close()
