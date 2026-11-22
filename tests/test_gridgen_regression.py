"""P3 格点生成回归测试：与 MATLAB 基准输出逐字节对比。

基准：
  benchmarks/DEM_Output/bth_dem_0125deg_admin.nc   （MATLAB 生成的掩膜 DEM）
  benchmarks/staList_Output/bth_0125_orthometric.txt / _ellipsoidal.txt
原始数据（需与 METCOM_ver3.1 同级）：
  01_MakeGrdText/lib/earth_relief_03m.nc + 0125.txt
"""

import sys
from pathlib import Path

import netCDF4
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.gridgen.orth_to_ell import load_egm2008_txt, process_dem_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BENCH = PROJECT_ROOT / "benchmarks"
MATLIB = PROJECT_ROOT.parent / "METCOM_ver3.1" / "01_MakeGrdText" / "lib"

EGM_FINE = MATLIB / "0125.txt"
DEMO_ADMIN = BENCH / "DEM_Output" / "bth_dem_0125deg_admin.nc"
BENCH_ORTHO = BENCH / "staList_Output" / "bth_0125_orthometric.txt"
BENCH_ELLIP = BENCH / "staList_Output" / "bth_0125_ellipsoidal.txt"


def _fmt_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    if not EGM_FINE.is_file():
        pytest.skip("原软件包 EGM2008 文本不在本机")
    egm_interp, _, _ = load_egm2008_txt(EGM_FINE)
    out_dir = tmp_path_factory.mktemp("staList")
    ortho, ellip = process_dem_file(
        DEMO_ADMIN, egm_interp,
        output_prefix="bth", output_res=0.125, base_res=0.125,
        output_dir=out_dir, max_stations=25000)
    return ortho, ellip


def test_orthometric_bytes_identical(generated):
    ortho, _ = generated
    got, expected = _fmt_lines(ortho), _fmt_lines(BENCH_ORTHO)
    assert len(got) == len(expected), f"行数不一致: {len(got)} vs {len(expected)}"
    diff = [f"L{i+1}:\n  got: {g!r}\n  exp: {e!r}"
            for i, (g, e) in enumerate(zip(got, expected)) if g != e]
    assert not diff, f"{len(diff)} 行不一致，前 3 处:\n" + "\n".join(diff[:3])


def test_ellipsoidal_bytes_identical(generated):
    _, ellip = generated
    got, expected = _fmt_lines(ellip), _fmt_lines(BENCH_ELLIP)
    assert len(got) == len(expected)
    diff = [f"L{i+1}:\n  got: {g!r}\n  exp: {e!r}"
            for i, (g, e) in enumerate(zip(got, expected)) if g != e]
    assert not diff, f"{len(diff)} 行不一致，前 3 处:\n" + "\n".join(diff[:3])


def test_dem_clip_admin_benchmark(tmp_path):
    """行政掩膜 DEM 与 MATLAB 基准逐值对比（NaN 位置必须一致）。"""
    import shutil
    if not (MATLIB / "earth_relief_03m.nc").is_file():
        pytest.skip("原软件包全球 DEM 不在本机")

    from metcom.gridgen.shp_dem_to_region import run_dem_clip

    # 拷贝 lib 所需文件结构到临时工作目录（config 相对路径基于 workdir）
    workdir = tmp_path / "work"
    (workdir / "lib").mkdir(parents=True)
    shutil.copy(MATLIB / "earth_relief_03m.nc", workdir / "lib" / "earth_relief_03m.nc")
    shp_src = MATLIB / "ChinaAdminDivisonSHP" / "District"
    shutil.copytree(shp_src, workdir / "lib" / "ChinaAdminDivisonSHP" / "District")

    cfg = {
        "workdir": str(workdir),
        "dem_path": "lib/earth_relief_03m.nc",
        "city_shp": "lib/ChinaAdminDivisonSHP/District/district.shp",
        "output_dir": str(tmp_path / "DEM_Output"),
        "resolution": 0.125,
        "regions": [{
            "name": "BTH",
            "extent": [113.0, 120.0, 36.0, 43.0],
            "prefixes": ["11", "12", "13"],
            "is_province_level": True,
            "apply_mask": True,
            "file_name": "bth_dem_0125deg_admin.nc",
        }],
    }
    results = run_dem_clip(cfg)
    assert not results["failed"], results["failed"]

    got_file = tmp_path / "DEM_Output" / "bth_dem_0125deg_admin.nc"
    ds_g = netCDF4.Dataset(got_file, "r")
    ds_e = netCDF4.Dataset(DEMO_ADMIN, "r")
    try:
        lon_g = np.asarray(ds_g.variables["lon"][:])
        lon_e = np.asarray(ds_e.variables["lon"][:])
        lat_g = np.asarray(ds_g.variables["lat"][:])
        lat_e = np.asarray(ds_e.variables["lat"][:])
        assert np.allclose(lon_g, lon_e, atol=1e-9), "经度向量不一致"
        assert np.allclose(lat_g, lat_e, atol=1e-9), "纬度向量不一致"

        elev_g = np.asarray(ds_g.variables["elevation"][:])
        elev_e = np.asarray(ds_e.variables["elevation"][:])
        if ds_g.variables["elevation"].dimensions[0] == "lon":
            elev_g = elev_g.T
        if ds_e.variables["elevation"].dimensions[0] == "lon":
            elev_e = elev_e.T

        nan_g, nan_e = np.isnan(elev_g), np.isnan(elev_e)
        assert (nan_g == nan_e).all(), (
            f"掩膜 NaN 位置不一致: Python 有效 {int((~nan_g).sum())} vs "
            f"MATLAB 有效 {int((~nan_e).sum())}，"
            f"差异数 {int((nan_g != nan_e).sum())}")
        assert np.allclose(elev_g[~nan_g], elev_e[~nan_e], rtol=1e-9, atol=1e-6)
    finally:
        ds_g.close()
        ds_e.close()
