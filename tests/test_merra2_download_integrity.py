"""MERRA-2 下载完整性校验测试（完全离线）。

只覆盖 Content-Length 对账这一处最小修复：
  A. Content-Length 与写入字节一致        -> 成功落盘
  B. Content-Length 与写入字节不一致      -> Merra2DownloadError，且不生成最终 .nc4
  C. 无 / 非法 Content-Length             -> 保持原有兼容行为（不因缺 header 直接失败）
  D. 空响应                               -> 仍然失败
  E. 原子改名只发生在完整性校验通过之后

不发真实请求、不访问 NASA / Earthdata、不使用真实账号、不下载任何真实文件。
"""

from __future__ import annotations

import io
import os
from datetime import datetime

import pytest

from metcom.downloader import merra2

START = datetime(2025, 6, 1, 0)
NAME = "MERRA2_400.inst6_3d_ana_Np.20250601.nc4"
URL = (
    "https://goldsmr5.gesdisc.eosdis.nasa.gov/data/MERRA2/M2I6NPANA.5.12.4/"
    "2025/06/MERRA2_400.inst6_3d_ana_Np.20250601.nc4"
)


class _FakeResponse(io.BytesIO):
    """带 headers 的假响应，模拟 http.client.HTTPResponse 的 Content-Length。"""

    def __init__(self, payload: bytes, content_length=None) -> None:
        super().__init__(payload)
        self.headers: dict[str, str] = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False


class _NoHeadersResponse(io.BytesIO):
    """完全不带 headers 的假响应（兼容既有测试里最简的 opener 形状）。"""

    def __enter__(self) -> "_NoHeadersResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False


class _FakeOpener:
    def __init__(self, payload: bytes, content_length=None, headers_attr: bool = True) -> None:
        self.payload = payload
        self.content_length = content_length
        self.headers_attr = headers_attr
        self.urls: list[str] = []

    def open(self, url, timeout=None):                  # noqa: ANN001
        self.urls.append(url)
        if self.headers_attr:
            return _FakeResponse(self.payload, self.content_length)
        return _NoHeadersResponse(self.payload)


def _run(tmp_path, opener):
    return merra2.run_merra2_download(
        start_time=START,
        end_time=START,
        output_dir=tmp_path,
        opener=opener,
    )


# ------------------------------------------------------------------- A --
def test_a_complete_download_succeeds(tmp_path, monkeypatch):
    monkeypatch.setattr(merra2, "_CHUNK_SIZE", 7)
    opener = _FakeOpener(b"n" * 100, content_length=100)

    result = _run(tmp_path, opener)

    target = tmp_path / "2025" / NAME
    assert opener.urls == [URL]
    assert target.is_file()
    assert target.stat().st_size == 100
    assert result["files"] == [str(target)]
    assert result["stats"]["downloaded"] == 1
    assert result["stats"]["failed"] == 0
    assert not list(tmp_path.rglob("*.part"))


# ------------------------------------------------------------------- B --
def test_b_content_length_mismatch_fails_without_final_file(tmp_path, monkeypatch):
    monkeypatch.setattr(merra2, "_CHUNK_SIZE", 7)
    opener = _FakeOpener(b"n" * 60, content_length=100)

    with pytest.raises(merra2.Merra2DownloadError) as excinfo:
        _run(tmp_path, opener)

    message = str(excinfo.value)
    assert "expected 100 bytes" in message
    assert "wrote 60 bytes" in message
    assert URL in message
    assert not list(tmp_path.rglob("*.nc4"))
    assert not list(tmp_path.rglob("*.part"))


def test_b_truncated_payload_is_not_promoted(tmp_path):
    """写入多于 0 但少于声明长度时，绝不允许 .part 变成最终 .nc4。"""
    opener = _FakeOpener(b"n" * 2471856, content_length=534787845)

    with pytest.raises(merra2.Merra2DownloadError) as excinfo:
        _run(tmp_path, opener)

    assert "expected 534787845 bytes, wrote 2471856 bytes" in str(excinfo.value)
    assert not list(tmp_path.rglob("*.nc4"))


# ------------------------------------------------------------------- C --
def test_c_missing_content_length_keeps_working(tmp_path):
    opener = _FakeOpener(b"n" * 100, content_length=None)

    result = _run(tmp_path, opener)

    assert (tmp_path / "2025" / NAME).stat().st_size == 100
    assert result["stats"]["downloaded"] == 1


def test_c_response_without_headers_attribute_keeps_working(tmp_path):
    opener = _FakeOpener(b"n" * 100, headers_attr=False)

    result = _run(tmp_path, opener)

    assert (tmp_path / "2025" / NAME).stat().st_size == 100
    assert result["stats"]["downloaded"] == 1


@pytest.mark.parametrize("bad_length", ["not-a-number", "-5", ""])
def test_c_invalid_content_length_keeps_working(tmp_path, bad_length):
    opener = _FakeOpener(b"n" * 100, content_length=bad_length)

    result = _run(tmp_path, opener)

    assert (tmp_path / "2025" / NAME).stat().st_size == 100
    assert result["stats"]["downloaded"] == 1


# ------------------------------------------------------------------- D --
@pytest.mark.parametrize("content_length", [0, None])
def test_d_empty_response_still_fails(tmp_path, content_length):
    opener = _FakeOpener(b"", content_length=content_length)

    with pytest.raises(merra2.Merra2DownloadError) as excinfo:
        _run(tmp_path, opener)

    assert "下载内容为空" in str(excinfo.value)
    assert not list(tmp_path.rglob("*.nc4"))
    assert not list(tmp_path.rglob("*.part"))


# ------------------------------------------------------------------- E --
def test_e_rename_only_after_integrity_check(tmp_path, monkeypatch):
    renamed: list[tuple[str, str]] = []
    real_replace = os.replace

    def _spy(src, dst):
        renamed.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(merra2.os, "replace", _spy)

    with pytest.raises(merra2.Merra2DownloadError):
        _run(tmp_path, _FakeOpener(b"n" * 60, content_length=100))
    assert renamed == []
    assert not list(tmp_path.rglob("*.nc4"))

    _run(tmp_path, _FakeOpener(b"n" * 100, content_length=100))
    assert len(renamed) == 1
    assert renamed[0][0].endswith(".part")
    assert renamed[0][1].endswith(NAME)


def test_e_failed_download_never_leaves_complete_looking_file(tmp_path):
    opener = _FakeOpener(b"n" * 60, content_length=100)

    with pytest.raises(merra2.Merra2DownloadError):
        _run(tmp_path, opener)

    assert list(tmp_path.rglob("*.nc4")) == []
    assert list(tmp_path.rglob("*20250601*")) == []
