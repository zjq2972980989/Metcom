"""EGM2008 格网坐标轴兼容性测试（纬度方向归一化 + 经度闭合性）。

覆盖 metcom/utils/egm2008.py::load_egm_mat 的多数据源 .mat 处理：
  - 纬度降序文件（如 MERRA2 +90 → -90）→ 统一归一化为升序 -90 → +90；
  - 经度闭合网格（-180 … +180，含重复端点）与非闭合网格（-180 … 179.375）
    分别使用 360/(n-1) 与 360/n 步长。

极点参考值（EGM2008 南极 ≈ -30.150 m、北极 ≈ +14.899 m）仅用于独立构造/识别
极点行，不参与被测算法的计算。
"""

import sys
from pathlib import Path

import h5py
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metcom.utils.egm2008 import load_egm_mat

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EGM_DIR = PROJECT_ROOT / "web" / "data" / "dependent" / "egm2008"

NORTH_POLE_N = 14.899
SOUTH_POLE_N = -30.150


def _write_mat(path: Path, values: np.ndarray) -> None:
    """按 MATLAB v7.3 存储习惯写出：MATLAB (n_lon, n_lat) → HDF5 (n_lat, n_lon)。"""
    with h5py.File(path, "w") as f:
        f.create_dataset("egm2008", data=np.asarray(values, dtype=float).T)


def _pole_anchored_profile(n_lat: int, ascending: bool) -> np.ndarray:
    """沿纬度单调的剖面，两端严格等于南北极点值 (n_lat,)。"""
    lats = np.linspace(-90.0, 90.0, n_lat) if ascending else np.linspace(90.0, -90.0, n_lat)
    return SOUTH_POLE_N + (NORTH_POLE_N - SOUTH_POLE_N) * (lats + 90.0) / 180.0


def _lat_scale(n_lat: int) -> np.ndarray:
    """纬度权函数：极点处为 0，中间为 1（用于造沿经度变化的合成场）。"""
    return 1.0 - np.abs(np.linspace(-1.0, 1.0, n_lat))


def test_lat_axis_normalized_when_descending(tmp_path):
    n_lon, n_lat = 9, 5
    values = np.tile(_pole_anchored_profile(n_lat, ascending=False)[None, :], (n_lon, 1))
    mat = tmp_path / "desc.mat"
    _write_mat(mat, values)

    egm = load_egm_mat(mat)
    assert egm.lat_axis[0] == -90.0 and egm.lat_axis[-1] == 90.0
    assert np.all(np.diff(egm.lat_axis) > 0)
    # 纬度维已翻转：首列 = 南极、末列 = 北极
    assert np.allclose(egm.values[:, 0], SOUTH_POLE_N)
    assert np.allclose(egm.values[:, -1], NORTH_POLE_N)
    assert np.allclose(egm.values, values[:, ::-1])


def test_lat_axis_kept_when_already_ascending(tmp_path):
    n_lon, n_lat = 9, 5
    values = np.tile(_pole_anchored_profile(n_lat, ascending=True)[None, :], (n_lon, 1))
    mat = tmp_path / "asc.mat"
    _write_mat(mat, values)

    egm = load_egm_mat(mat)
    assert egm.lat_axis[0] == -90.0 and egm.lat_axis[-1] == 90.0
    assert np.allclose(egm.values, values)          # 不得翻动已经正确的文件


def test_lat_order_explicit_override(tmp_path):
    values = np.arange(9 * 5, dtype=float).reshape(9, 5)   # 无极点行，无法自动判定
    mat = tmp_path / "flat.mat"
    _write_mat(mat, values)

    with pytest.raises(ValueError, match="无法自动判定"):
        load_egm_mat(mat)
    assert np.allclose(load_egm_mat(mat, lat_order="ascending").values, values)
    assert np.allclose(load_egm_mat(mat, lat_order="descending").values, values[:, ::-1])
    with pytest.raises(ValueError, match="lat_order"):
        load_egm_mat(mat, lat_order="sideways")


def test_closed_lon_axis_spacing(tmp_path):
    """闭合网格：n_lon == 360/lon_sr + 1 → -180 … +180，步长 360/(n-1)。"""
    n_lon, n_lat = 9, 5
    values = np.tile(_pole_anchored_profile(n_lat, ascending=False)[None, :], (n_lon, 1))
    mat = tmp_path / "closed.mat"
    _write_mat(mat, values)

    egm = load_egm_mat(mat, expected_sr=(45.0, 45.0))
    assert egm.lon_axis[0] == -180.0 and egm.lon_axis[-1] == 180.0
    assert egm.lon_axis[1] - egm.lon_axis[0] == pytest.approx(45.0)


def test_merra2_shape_non_closed_lon_axis(tmp_path):
    """MERRA2 形状：576 个经度点 = -180 … 179.375（非闭合，步长 0.625）。"""
    n_lon, n_lat = 576, 361
    prof = _pole_anchored_profile(n_lat, ascending=True)
    lon_phase = np.sin(2.0 * np.pi * np.arange(n_lon) / n_lon)
    values = prof[None, :] + 50.0 * lon_phase[:, None] * _lat_scale(n_lat)[None, :]
    mat = tmp_path / "merra2_shape.mat"
    _write_mat(mat, values)

    egm = load_egm_mat(mat, expected_sr=(0.5, 0.625))
    assert egm.lat_axis[0] == -90.0 and egm.lat_axis[-1] == 90.0
    assert egm.lon_axis[0] == -180.0
    assert egm.lon_axis[-1] == pytest.approx(179.375, abs=1e-9)
    assert egm.lon_axis[-1] != 180.0
    assert egm.lon_axis[1] - egm.lon_axis[0] == pytest.approx(0.625)


def test_non_closed_lon_axis_detected_without_expected_sr(tmp_path):
    """无 expected_sr 时按首末子午线数值判定闭合性。"""
    n_lon, n_lat = 576, 361
    prof = _pole_anchored_profile(n_lat, ascending=True)
    lon_phase = np.sin(2.0 * np.pi * np.arange(n_lon) / n_lon)
    values = prof[None, :] + 50.0 * lon_phase[:, None] * _lat_scale(n_lat)[None, :]
    mat = tmp_path / "auto.mat"
    _write_mat(mat, values)

    egm = load_egm_mat(mat)
    assert egm.lon_axis[-1] == pytest.approx(179.375, abs=1e-9)   # 非闭合，不是 180


@pytest.mark.parametrize("fname, sr, closed", [
    ("geoidheight_EGM2008_0250.mat", (0.25, 0.25), True),
    ("geoidheight_EGM2008_0500_0625.mat", (0.5, 0.625), False),
])
def test_repo_egm_files_lat_lon_axes(fname, sr, closed):
    """仓库内两个真实 EGM 文件：纬度一律归一化为升序，经度按闭合性取步长。"""
    mat = EGM_DIR / fname
    if not mat.is_file():
        pytest.skip(f"{fname} 不在本机")

    egm = load_egm_mat(mat, expected_sr=sr)
    assert egm.lat_axis[0] == -90.0 and egm.lat_axis[-1] == 90.0
    assert np.all(np.diff(egm.lat_axis) > 0)
    assert egm.lon_axis[0] == -180.0
    if closed:
        assert egm.lon_axis[-1] == 180.0
    else:
        assert egm.lon_axis[-1] == pytest.approx(179.375, abs=1e-9)

    with h5py.File(mat, "r") as f:
        raw = np.asarray(f["egm2008"][()], dtype=float)   # (n_lat, n_lon)，方向随文件而异
    n_lon, n_lat = egm.values.shape
    assert raw.shape == (n_lat, n_lon)

    # 独立判定文件原始纬度方向（仅用极点参考值，不依赖被测代码）：首行是北极 → 降序
    file_descending = abs(float(raw[0].mean()) - NORTH_POLE_N) <= 1.0

    # 随机节点交叉验证：归一化后 values[lon_idx, lat_idx] 必须等于原始 HDF5 中
    # 真实纬度对应的那一行
    rng = np.random.default_rng(0)
    for _ in range(20):
        li = int(rng.integers(0, n_lat))
        lj = int(rng.integers(0, n_lon))
        row = (n_lat - 1 - li) if file_descending else li
        assert egm.values[lj, li] == pytest.approx(raw[row, lj], abs=1e-12)

    # 测站 0001 附近节点：纬度方向若镜像会读到另一半球的值
    li = int(np.max(np.where(egm.lat_axis <= 21.5159)[0]))
    lj = int(np.max(np.where(egm.lon_axis <= 111.25)[0]))
    row = (n_lat - 1 - li) if file_descending else li
    assert egm.values[lj, li] == pytest.approx(raw[row, lj], abs=1e-12)
    assert egm.values[lj, li] != pytest.approx(raw[n_lat - 1 - row, lj], abs=1e-6)