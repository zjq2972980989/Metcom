"""端到端流水线测试：合成 ERA5 NC → retrieve → 输出文件校验。"""

import sys
from pathlib import Path

import netCDF4
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.retrieval.pipeline import RetrievalOptions, run_retrieval
from metcom.utils.io import read_result_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATLAB_ROOT = PROJECT_ROOT.parent / "METCOM_ver3.1"
EGM_FILE = MATLAB_ROOT / "03_DataProcessing/METCOM_ERA5/dependentDataFile/geoidheight_EGM2008_0250.mat"

N_LEV = 37


@pytest.fixture(scope="module")
def synthetic_era5(tmp_path_factory):
    """构造一个时次的合成 ERA5 NC（经度跨越 0° 以验证 mod 映射）。"""
    root = tmp_path_factory.mktemp("era5_data")
    day = root / "20240101"
    day.mkdir()
    lon = np.concatenate([np.arange(358.0, 360.01, 0.25), np.arange(0.0, 1.51, 0.25)])
    lat = np.arange(35.0, 37.01, 0.25)
    plev = np.linspace(1000.0, 1.0, N_LEV)  # 文件层序：低层在前（1000→1 hPa）
    geo_h = np.linspace(-500.0, 28000.0, N_LEV)

    n_lon, n_lat = lon.size, lat.size
    rng = np.random.default_rng(42)
    # 空间缓变场 + 少量随机性
    spatial = (np.sin(np.deg2rad(lat))[None, :]
               + np.cos(np.deg2rad(lon))[:, None])  # (lon, lat)
    z = np.empty((n_lon, n_lat, N_LEV, 1))
    t = np.empty((n_lon, n_lat, N_LEV, 1))
    q = np.empty((n_lon, n_lat, N_LEV, 1))
    r = np.empty((n_lon, n_lat, N_LEV, 1))
    for k in range(N_LEV):
        z[:, :, k, 0] = (geo_h[k] + 50.0 * spatial) * 9.80665
        t[:, :, k, 0] = 280.0 - 6.0 * k + 2.0 * spatial
        q[:, :, k, 0] = np.clip(0.004 - 8e-5 * k, 1e-6, None) * (1 + 0.05 * spatial)
        r[:, :, k, 0] = np.clip(70.0 - 1.2 * k, 1.0, 100.0) + rng.normal(0, 0.1, (n_lon, n_lat))

    path = day / "era5_global_qrzt_2024010100.nc"
    with netCDF4.Dataset(path, "w") as ds:
        ds.createDimension("longitude", n_lon)
        ds.createDimension("latitude", n_lat)
        ds.createDimension("pressure_level", N_LEV)
        ds.createDimension("time", 1)
        v = ds.createVariable("longitude", "f8", ("longitude",))
        v[:] = lon
        v = ds.createVariable("latitude", "f8", ("latitude",))
        v[:] = lat  # 90→-90 递减约定在本例用升序也会被 flip（对升序输入 flip 后降序？
        # 注意：readSingleData 无条件 flip，因此这里按 MATLAB 预期写降序
        v[:] = lat[::-1]
        v = ds.createVariable("pressure_level", "f4", ("pressure_level",))
        v[:] = plev
        for name, arr in (("z", z), ("t", t), ("q", q), ("r", r)):
            v = ds.createVariable(name, "f4",
                                  ("longitude", "latitude", "pressure_level", "time"))
            v[:] = arr.astype(np.float32)
    return root, path


def test_end_to_end_synthetic(synthetic_era5, tmp_path):
    root, nc_path = synthetic_era5
    if not EGM_FILE.is_file():
        pytest.skip("原软件包 EGM2008 文件不在本机")

    site_file = tmp_path / "sites.txt"
    site_file.write_text(
        "A000    36.125000    -1.000000     50.000\n"
        "A001    36.125000    -1.000000     50.000\n"  # 与上面同一位置
        "A002    35.500000    -0.500000     80.500\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "output_ERA5"

    options = RetrievalOptions(
        source_key="era5",
        data_path=str(root),
        output_path=str(out_dir),
        output_format="txt",
        site_file=str(site_file),
        start_time="20240101",
        end_time="20240101",
        spatial_resolution=(0.25, 0.25),
        egm_file=str(EGM_FILE),
    )
    stats = run_retrieval(options)
    assert stats == {"hours": 1, "sites": 3, "outputs": 1}

    # 年积日路由：2024010100 → 2024001（闰年 1 月 1 日）
    out_file = out_dir / "2024001" / "ztd_2024010100_sit_era5.txt"
    assert out_file.is_file()

    # 表头与 MATLAB 基准完全一致
    header = out_file.read_text(encoding="utf-8").splitlines()[0]
    bench = PROJECT_ROOT / "benchmarks/output_ERA5/2024001/ztd_2024010100_sit_era5.txt"
    assert header == bench.read_text(encoding="utf-8").splitlines()[0]

    # 数值合理性与 mod 经度一致性：A000/A001 同位置同结果
    columns, data = read_result_file(out_file)
    assert data.shape[0] == 3
    assert np.all(np.isfinite(data[:, 1:]))
    ztd = data[:, columns.index("ZTD(m)")]
    assert (2.0 < ztd).all() and (ztd < 6.0).all()
    assert ztd[0] == pytest.approx(ztd[1], rel=1e-12)
    # 站点高度回写
    h = data[:, columns.index("height(m)")]
    assert h.tolist() == [50.0, 50.0, 80.5]


def test_site_outside_grid_raises(synthetic_era5, tmp_path):
    root, _ = synthetic_era5
    if not EGM_FILE.is_file():
        pytest.skip("原软件包 EGM2008 文件不在本机")
    site_file = tmp_path / "sites_bad.txt"
    site_file.write_text("A000    20.000000   359.000000     50.000\n", encoding="utf-8")
    options = RetrievalOptions(
        source_key="era5",
        data_path=str(root),
        output_path=str(tmp_path / "out"),
        site_file=str(site_file),
        start_time="20240101",
        end_time="20240101",
        spatial_resolution=(0.25, 0.25),
        egm_file=str(EGM_FILE),
    )
    with pytest.raises(ValueError):
        run_retrieval(options)
