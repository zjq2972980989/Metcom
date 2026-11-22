"""GRIB2 → NetCDF 转换（替代原 GribToNcConverter_cfgrib.exe，基于 cfgrib）。

原 MATLAB 流程：grib2Trans.m 调用 GribToNcConverter_cfgrib.exe 将各日 GRIB2
文件转换为 NC 到 tempTransData/<yyyymmdd>/，供 readSingleData 读取。
转换输出需满足：变量名 gh/t/r/q（cfgrib shortName），气压坐标 isobaricInhPa，
维度 longitude/latitude/isobaricInhPa。

注意：本机无 GRIB2 样例数据，此路径尚未用真实数据验证（见 docs/porting_notes.md）；
若 cfgrib/eccodes 未安装会给出明确报错。
"""

from __future__ import annotations

from pathlib import Path


def _require_cfgrib():
    try:
        import cfgrib  # noqa: F401 —— 导入即校验可用性
        import eccodes  # noqa: F401
        import xarray as xr
    except ImportError as e:
        raise RuntimeError(
            "GRIB2 转换需要 cfgrib 与 eccodes（pip install cfgrib eccodes），"
            f"当前环境不可用: {e}"
        ) from e
    return xr


def convert_grib_file(grib_path: Path, out_nc: Path) -> Path:
    """转换单个 GRIB2 文件为 NC（变量名与 MATLAB 读取约定对齐）。

    按 typeOfLevel=isobaricInhPa 过滤等压面层（真实数据验证：CRAV15/CRA40/
    CMAMESO 的 GRIB2 均含 surface/tropopause 等多种层类型，须过滤）。
    """
    from metcom.retrieval.eccodes_backend import configure
    configure()  # 必须在 eccodes/cfgrib 首次导入前注入 vendor 解码库
    _require_cfgrib()
    import xarray as xr_

    ds = xr_.open_dataset(grib_path, engine="cfgrib",
                          backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}})
    try:
        rename: dict[str, str] = {}
        if "isobaric" in ds.coords and "isobaricInhPa" not in ds.coords:
            rename["isobaric"] = "isobaricInhPa"
        if rename:
            ds = ds.rename(rename)
        ds.to_netcdf(out_nc)
    finally:
        ds.close()
    return out_nc


def convert_day_folder(day_grib_folder: Path | str, day_out_folder: Path | str) -> list[Path]:
    """转换一个日文件夹内全部 GRIB2 文件（对应 grib2Trans.m 的按日转换）。"""
    day_grib_folder = Path(day_grib_folder)
    day_out_folder = Path(day_out_folder)
    day_out_folder.mkdir(parents=True, exist_ok=True)

    grib_files = sorted(
        p for p in day_grib_folder.iterdir()
        if p.suffix.lower() in (".grib", ".grib2", ".grb", ".grb2") or p.name.endswith(".grib2")
    )
    converted: list[Path] = []
    for grib_path in grib_files:
        out_nc = day_out_folder / (grib_path.stem + ".nc")
        if out_nc.is_file():
            converted.append(out_nc)   # 断点跳过（原 exe 亦如此）
            continue
        convert_grib_file(grib_path, out_nc)
        converted.append(out_nc)
        print(f"GRIB2→NC: {grib_path.name} -> {out_nc.name}")
    if not grib_files:
        print(f"警告: 在 {day_grib_folder} 中未找到 GRIB2 文件")
    return converted
