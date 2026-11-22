from __future__ import annotations

import types

from metcom.retrieval.readers.era5 import Era5Reader
from metcom.retrieval.readers.grib_sources import GribSourceReader
from metcom.retrieval.readers.merra2 import Merra2Reader

READERS: dict[str, object] = {
    "era5": Era5Reader,
    "cmameso": GribSourceReader("cmameso"),
    "cra40": GribSourceReader("cra40"),
    "crav15": GribSourceReader("crav15"),
    "merra2": Merra2Reader,
}


def get_reader(key: str):
    """返回具备 iter_hours(base, start_date, end_date) 接口的读取器实例。"""
    if key not in READERS:
        raise KeyError(f"未知数据源: {key}，可选: {sorted(READERS)}")
    reader = READERS[key]
    if isinstance(reader, types.FunctionType):  # 兼容延迟构造
        reader = reader()
    return reader


__all__ = ["READERS", "get_reader"]
