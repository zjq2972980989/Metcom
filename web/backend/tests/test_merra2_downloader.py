"""MERRA-2 下载器测试（NASA GES DISC / Earthdata）。

全部离线：不发真实请求、不写真实用户目录、不下载任何数据。
HTTP 通过注入的假 opener 模拟，既验证成功落盘，也验证失败抛错与凭据缺失的明确失败。
"""

from __future__ import annotations

import io
import urllib.error
from datetime import date, datetime

import pytest

from metcom.downloader import merra2

DAY = date(2025, 6, 1)
OFFICIAL_NAME = "MERRA2_400.inst6_3d_ana_Np.20250601.nc4"
OFFICIAL_URL = (
    "https://goldsmr5.gesdisc.eosdis.nasa.gov/data/MERRA2/M2I6NPANA.5.12.4/"
    "2025/06/MERRA2_400.inst6_3d_ana_Np.20250601.nc4"
)


class _FakeResponse(io.BytesIO):
    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False


class _FakeOpener:
    """最小 opener：记录请求 URL，返回固定字节或抛错。"""

    def __init__(self, payload: bytes = b"merra2-bytes", fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail
        self.urls: list[str] = []

    def open(self, url, timeout=None):                  # noqa: ANN001
        self.urls.append(url)
        if self.fail:
            raise urllib.error.URLError("simulated network failure")
        return _FakeResponse(self.payload)


# ---------------------------------------------------------------- 时间 --
@pytest.mark.parametrize("hour", [0, 6, 12, 18])
def test_valid_time_accepted(hour):
    moment = datetime(2025, 6, 1, hour)
    assert merra2.validate_merra2_time(moment) == moment
    assert merra2.iter_merra2_days(moment, moment) == [DAY]


def test_invalid_hour_rejected():
    with pytest.raises(merra2.Merra2DownloadError):
        merra2.validate_merra2_time(datetime(2025, 6, 1, 1))


def test_invalid_minute_rejected():
    with pytest.raises(merra2.Merra2DownloadError):
        merra2.validate_merra2_time(datetime(2025, 6, 1, 6, 30))


def test_reversed_range_rejected():
    with pytest.raises(merra2.Merra2DownloadError):
        merra2.iter_merra2_days(datetime(2025, 6, 2, 0), datetime(2025, 6, 1, 0))


# ------------------------------------------------------------ 文件名/URL --
def test_file_name_rule():
    assert merra2.merra2_file_name(DAY) == OFFICIAL_NAME


def test_url_rule():
    assert merra2.merra2_file_url(DAY) == OFFICIAL_URL


# ---------------------------------------------------------------- 凭据 --
def test_missing_credentials_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(merra2, "earthdata_credentials", lambda: None)

    with pytest.raises(merra2.Merra2AuthError) as excinfo:
        merra2.run_merra2_download(
            start_time=datetime(2025, 6, 1, 0),
            end_time=datetime(2025, 6, 1, 0),
            output_dir=tmp_path,
        )

    message = str(excinfo.value)
    assert "Earthdata" in message
    assert not list(tmp_path.rglob("*.nc4"))


# ---------------------------------------------------------------- 下载 --
def test_mock_download_writes_file(tmp_path):
    opener = _FakeOpener(payload=b"x" * 4096)

    result = merra2.run_merra2_download(
        start_time=datetime(2025, 6, 1, 6),
        end_time=datetime(2025, 6, 1, 6),
        output_dir=tmp_path,
        opener=opener,
    )

    target = tmp_path / "2025" / OFFICIAL_NAME
    assert target.is_file()
    assert target.stat().st_size == 4096
    assert result["files"] == [str(target)]
    assert result["stats"]["downloaded"] == 1
    assert result["stats"]["failed"] == 0
    assert opener.urls == [OFFICIAL_URL]


def test_mock_download_failure_raises(tmp_path):
    opener = _FakeOpener(fail=True)

    with pytest.raises(merra2.Merra2DownloadError):
        merra2.run_merra2_download(
            start_time=datetime(2025, 6, 1, 0),
            end_time=datetime(2025, 6, 1, 0),
            output_dir=tmp_path,
            opener=opener,
        )

    assert not list(tmp_path.rglob("*.nc4"))
    assert not list(tmp_path.rglob("*.part"))


def test_multi_day_range_downloads_one_file_per_day(tmp_path):
    opener = _FakeOpener(payload=b"y" * 1024)

    result = merra2.run_merra2_download(
        start_time=datetime(2025, 6, 1, 0),
        end_time=datetime(2025, 6, 2, 18),
        output_dir=tmp_path,
        opener=opener,
    )

    assert result["stats"]["total"] == 2
    assert result["stats"]["downloaded"] == 2
    assert (tmp_path / "2025" / OFFICIAL_NAME).is_file()
    assert (tmp_path / "2025" / "MERRA2_400.inst6_3d_ana_Np.20250602.nc4").is_file()


def test_existing_file_is_skipped(tmp_path):
    opener = _FakeOpener(payload=b"z" * 512)
    target = tmp_path / "2025" / OFFICIAL_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"already-downloaded")

    result = merra2.run_merra2_download(
        start_time=datetime(2025, 6, 1, 0),
        end_time=datetime(2025, 6, 1, 0),
        output_dir=tmp_path,
        opener=opener,
    )

    assert result["stats"]["skipped"] == 1
    assert result["stats"]["downloaded"] == 0
    assert opener.urls == []


# ------------------------------------------------------- Adapter 能力声明 --
def test_adapter_declares_merra2_downloader_capability(monkeypatch):
    from engine import adapter

    monkeypatch.setattr(adapter, "_merra2_credentials_available", lambda: False)
    adapter._capability_cache = None

    capabilities = adapter.source_capabilities()
    assert capabilities["merra2"]["downloader_available"] is True
    assert capabilities["merra2"]["download_available"] is False
    assert capabilities["era5"]["downloader_available"] is True
    assert capabilities["cra40"]["downloader_available"] is False

    assert "merra2" in adapter.DOWNLOADER_SOURCES
    # 本轮起 MERRA-2 已接入 Web 下载白名单；是否能真正下载由凭据决定（见 download_available）
    assert "merra2" in adapter.DOWNLOADABLE_SOURCES

    sources = {item["key"]: item for item in adapter.list_sources()}
    assert sources["merra2"]["downloader_available"] is True
    assert sources["merra2"]["download_available"] is False
