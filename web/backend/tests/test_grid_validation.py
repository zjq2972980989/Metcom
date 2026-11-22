"""Grid 请求模型 + 格网参数校验的离线测试。

不联网、不生成格点、不写 site_file、不调用 gridgen / DEM / retrieval / adapter
的计算入口：只验证 ``models.schemas.GridRequest`` 的类型契约，以及
``services.validation.validate_grid`` 的业务校验（范围 / 顺序 / 有限性 /
bool / 格点数上限 / 复用既有 dataset·parameter·time 校验）。

格点数算法必须与引擎 ``metcom/gridgen/shp_dem_to_region.py::_colon`` 一致：
``n = round((b - a) / step) + 1``，两端点都包含。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from models.schemas import GridRequest
from services import validation
from services.errors import InvalidRequestError

START = datetime(2025, 6, 1, 0)
END = datetime(2025, 6, 1, 0)

_BASE = {
    "dataset": "era5",
    "parameters": ["PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"],
    "start_time": START,
    "end_time": END,
    "lon_min": 115.0,
    "lon_max": 116.0,
    "lat_min": 36.0,
    "lat_max": 37.0,
}


def _request(**overrides) -> GridRequest:
    """构造 GridRequest（可覆盖任意字段）。"""
    data = dict(_BASE)
    data.update(overrides)
    return GridRequest(**data)


def _raw(**overrides) -> SimpleNamespace:
    """构造绕过 pydantic 的原始请求，用于验证校验层自身的类型防线。"""
    data = dict(_BASE)
    data.update(overrides)
    return SimpleNamespace(**data)


# ------------------------------------------------------------- 1/2 合法区域 --


def test_legal_era5_region_passes():
    result = validation.validate_grid(
        _request(lon_min=115.0, lon_max=116.0, lat_min=36.0, lat_max=37.0))

    assert result["dataset"] == "era5"
    assert result["parameters"] == ["PWV", "WVD", "Tm", "ZTD", "ZHD", "ZWD"]
    assert (result["n_lon"], result["n_lat"], result["grid_points"]) == (5, 5, 25)
    assert result["start_time"] == "2025-06-01T00:00:00"
    assert result["end_time"] == "2025-06-01T00:00:00"


@pytest.mark.parametrize("lon_max", [115.3125, 115.375])
def test_unaligned_longitude_span_rejected(lon_max):
    with pytest.raises(InvalidRequestError, match="整数倍") as excinfo:
        validation.validate_grid(_request(lon_min=115.0, lon_max=lon_max))

    message = str(excinfo.value)
    assert "经度" in message
    assert "0.25" in message


def test_unaligned_latitude_span_rejected():
    with pytest.raises(InvalidRequestError, match="整数倍") as excinfo:
        validation.validate_grid(
            _request(lon_min=115.0, lon_max=116.0,
                     lat_min=36.0, lat_max=36.3125))

    message = str(excinfo.value)
    assert "纬度" in message
    assert "0.25" in message


def test_legal_merra2_region_passes():
    result = validation.validate_grid(
        _request(dataset="merra2", lon_min=100.0, lon_max=101.25,
                 lat_min=30.0, lat_max=31.0))

    assert result["dataset"] == "merra2"
    assert (result["n_lon"], result["n_lat"], result["grid_points"]) == (3, 3, 9)


def test_schema_has_no_user_editable_resolution():
    """分辨率由数据源决定，请求体里不允许出现可编辑的分辨率字段。"""
    assert set(GridRequest.model_fields) == {
        "dataset", "parameters", "start_time", "end_time",
        "lon_min", "lon_max", "lat_min", "lat_max",
    }


def test_resolution_comes_from_source_spec():
    """返回的分辨率直接取自数据源规格（(lat_step, lon_step)），不是用户输入。"""
    era5 = validation.validate_grid(_request())
    assert (era5["lat_step"], era5["lon_step"]) == (0.25, 0.25)

    merra2 = validation.validate_grid(
        _request(dataset="merra2", lon_min=100.0, lon_max=101.25,
                 lat_min=30.0, lat_max=31.0))
    assert (merra2["lat_step"], merra2["lon_step"]) == (0.5, 0.625)


# ------------------------------------------------------- 3/4 顺序（min < max）--


@pytest.mark.parametrize("overrides", [
    {"lon_min": 116.0, "lon_max": 115.0},
    {"lon_min": 115.0, "lon_max": 115.0},
])
def test_lon_min_must_be_less_than_lon_max(overrides):
    with pytest.raises(InvalidRequestError):
        validation.validate_grid(_request(**overrides))


@pytest.mark.parametrize("overrides", [
    {"lat_min": 37.0, "lat_max": 36.0},
    {"lat_min": 36.0, "lat_max": 36.0},
])
def test_lat_min_must_be_less_than_lat_max(overrides):
    with pytest.raises(InvalidRequestError):
        validation.validate_grid(_request(**overrides))


# --------------------------------------------------------------- 5/6 越界 --


@pytest.mark.parametrize("overrides", [
    {"lon_min": -180.5},
    {"lon_max": 180.5},
    {"lon_min": 200.0},
    {"lon_max": -200.0},
])
def test_longitude_out_of_range_rejected(overrides):
    with pytest.raises(InvalidRequestError):
        validation.validate_grid(_request(**overrides))


@pytest.mark.parametrize("overrides", [
    {"lat_min": -90.5},
    {"lat_max": 90.5},
    {"lat_min": 100.0},
    {"lat_max": -100.0},
])
def test_latitude_out_of_range_rejected(overrides):
    with pytest.raises(InvalidRequestError):
        validation.validate_grid(_request(**overrides))


@pytest.mark.parametrize("overrides", [
    {"lon_min": -180.0, "lon_max": 180.0, "lat_min": -90.0, "lat_max": 90.0},
])
def test_extreme_but_valid_bounds_are_range_accepted(overrides):
    """全球范围本身合法（越界与否只看数值），是否放行由格点数上限决定。"""
    with pytest.raises(InvalidRequestError) as excinfo:
        validation.validate_grid(_request(**overrides))
    # 拒绝原因必须是"格点数超限"，而不是"超出范围"
    assert "格点" in str(excinfo.value)


# ------------------------------------------------------ 9/10 非有限数值 --


@pytest.mark.parametrize("field", ["lon_min", "lon_max", "lat_min", "lat_max"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_rejected(field, value):
    with pytest.raises(InvalidRequestError) as excinfo:
        validation.validate_grid(_request(**{field: value}))
    assert "有限" in str(excinfo.value)


# ---------------------------------------------------------- 11 bool 拒绝 --


@pytest.mark.parametrize("field", ["lon_min", "lon_max", "lat_min", "lat_max"])
def test_bool_rejected_by_schema(field):
    """strict 数值字段：true/false 不会被静默转换成 1.0/0.0。"""
    with pytest.raises(ValidationError):
        _request(**{field: True})


@pytest.mark.parametrize("field", ["lon_min", "lon_max", "lat_min", "lat_max"])
def test_bool_rejected_by_validation_layer(field):
    """校验层自身也拒绝 bool（防御绕过 pydantic 的调用方）。"""
    with pytest.raises(InvalidRequestError):
        validation.validate_grid(_raw(**{field: True}))


@pytest.mark.parametrize("field", ["lon_min", "lon_max", "lat_min", "lat_max"])
def test_plain_number_strings_rejected_by_schema(field):
    """类型错误必须显式报错，不能靠字符串隐式转换。"""
    with pytest.raises(ValidationError):
        _request(**{field: "115.0"})


# ------------------------------------------------- 12/13 格点数上限 --


def test_grid_point_limit_constant():
    assert validation.MAX_GRID_POINTS == 10000


def test_large_grid_rejected_with_actionable_message():
    """ERA5 0.25°：73~135 / 18~54 → 249 × 145 = 36105 点，必须拒绝。"""
    with pytest.raises(InvalidRequestError) as excinfo:
        validation.validate_grid(
            _request(lon_min=73.0, lon_max=135.0, lat_min=18.0, lat_max=54.0))

    message = str(excinfo.value)
    assert "36105" in message         # 预计格点数
    assert "10000" in message         # 上限
    assert "ERA5" in message          # 数据源
    assert "0.25" in message          # 自动匹配的分辨率


def test_grid_point_limit_boundary_is_inclusive():
    """恰好等于上限可以通过；再多一格就被拒绝。"""
    span = 99 * 0.25                  # 经/纬各 100 点 → 100 × 100 = 10000
    ok = validation.validate_grid(
        _request(lon_min=0.0, lon_max=span, lat_min=0.0, lat_max=span))
    assert ok["grid_points"] == validation.MAX_GRID_POINTS

    with pytest.raises(InvalidRequestError):
        validation.validate_grid(
            _request(lon_min=0.0, lon_max=span + 0.25,
                     lat_min=0.0, lat_max=span))


# ------------------------------------------------ 14 格点数算法（ERA5 例） --


def test_era5_example_counts_match_colon_semantics():
    """73~75 / 35~37，ERA5 0.25° → 9 × 9 = 81（含两端点）。"""
    result = validation.validate_grid(
        _request(lon_min=73.0, lon_max=75.0, lat_min=35.0, lat_max=37.0))
    assert result["n_lon"] == 9
    assert result["n_lat"] == 9
    assert result["grid_points"] == 81


def test_lon_lat_steps_not_swapped_for_era5():
    """ERA5 经纬同分辨率，无法区分顺序，另用 MERRA-2 验证（见下）。"""
    result = validation.validate_grid(
        _request(lon_min=0.0, lon_max=2.0, lat_min=0.0, lat_max=2.0))
    assert (result["n_lon"], result["n_lat"], result["grid_points"]) == (9, 9, 81)


# --------------------------------------- 15 格点数算法（MERRA-2 防写反） --


def test_merra2_lon_lat_steps_not_swapped():
    """MERRA-2 原生 (lat_step, lon_step) = (0.5, 0.625)。

    取 lon 0~2.5、lat 0~2.0：

    - 正确：n_lon = 2.5/0.625+1 = 5，n_lat = 2.0/0.5+1 = 5 → 25
    - 若写反：n_lon = 2.5/0.5+1 = 6，n_lat = 2.0/0.625+1 = 4 → 24
    """
    result = validation.validate_grid(
        _request(dataset="merra2", lon_min=0.0, lon_max=2.5,
                 lat_min=0.0, lat_max=2.0))
    assert (result["n_lon"], result["n_lat"]) == (5, 5)
    assert result["grid_points"] == 25
    assert result["lat_step"] == 0.5
    assert result["lon_step"] == 0.625


def test_grid_point_counts_helper_uses_lat_lon_order():
    """直接验证 helper：spatial_resolution 的顺序是 (lat_step, lon_step)。"""
    spec = validation.require_source("merra2")
    n_lon, n_lat, total = validation.grid_point_counts(spec, 0.0, 2.5, 0.0, 2.0)
    assert (n_lon, n_lat, total) == (5, 5, 25)


# --------------------------------------------------- 复用既有校验机制 --


def test_unknown_dataset_rejected():
    with pytest.raises(InvalidRequestError):
        validation.validate_grid(_request(dataset="not-a-source"))


def test_parameters_reuse_existing_validation():
    with pytest.raises(InvalidRequestError):
        validation.validate_grid(_request(parameters=[]))
    with pytest.raises(InvalidRequestError):
        validation.validate_grid(_raw(parameters=["NOT_A_PARAM"]))


def test_time_reuse_existing_validation():
    with pytest.raises(InvalidRequestError):
        validation.validate_grid(
            _request(start_time=datetime(2025, 6, 2),
                     end_time=datetime(2025, 6, 1)))


def test_duplicate_parameters_deduplicated():
    result = validation.validate_grid(_request(parameters=["PWV", "PWV", "ZTD"]))
    assert result["parameters"] == ["PWV", "ZTD"]
