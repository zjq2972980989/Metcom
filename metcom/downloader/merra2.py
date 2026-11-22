"""MERRA-2 真实下载器（NASA GES DISC / Earthdata Login）。

与 ERA5 的 CDS 下载器完全独立，不复用 ``metcom.downloader.runner``，也不改它。

产品与官方规则（经 NASA CMR 公共元数据接口核实，集合 C1276812884-GES_DISC）：
  - 集合: M2I6NPANA v5.12.4
          MERRA-2 inst6_3d_ana_Np: 3d, 6-Hourly, Instantaneous, Pressure-Level,
          Analysis, Analyzed Meteorological Fields (0.625 x 0.5 degree)
  - 流号: 400（2011 年至今）
  - 官方文件名: ``MERRA2_400.inst6_3d_ana_Np.YYYYMMDD.nc4``（一天一文件，
    含 00/06/12/18 UTC 四个分析时次）
  - 官方数据目录: https://goldsmr5.gesdisc.eosdis.nasa.gov/data/MERRA2/M2I6NPANA.5.12.4/YYYY/MM/
  - 认证: NASA Earthdata Login（urs.earthdata.nasa.gov）

与现有 retrieval 的差异（本轮只报告，不改 retrieval）：
  ``metcom/retrieval/readers/merra2.py`` 以 ``MERRA2_400.inst6_3d_ana_Np.YYYYMMDD.SUB.nc``
  做 ``fullmatch`` 匹配；官方文件既没有 ``.SUB``，扩展名也是 ``.nc4``，因此官方原始文件
  目前不会被该读取器直接匹配。本模块只按官方规则真实下载，是否调整读取器属于后续任务。

凭据（二选一，均不写进代码或 YAML，日志也不输出）：
  - 环境变量 ``EARTHDATA_USERNAME`` / ``EARTHDATA_PASSWORD``
  - ``~/.netrc`` 中 ``machine urs.earthdata.nasa.gov`` 的 login/password
"""

from __future__ import annotations

import http.cookiejar
import logging
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

logger = logging.getLogger("metcom.downloader.merra2")

# 官方集合与主机（CMR 确认）
MERRA2_COLLECTION = "M2I6NPANA.5.12.4"
MERRA2_PRODUCT = "inst6_3d_ana_Np"
MERRA2_STREAM = "400"
MERRA2_HOST = "goldsmr5.gesdisc.eosdis.nasa.gov"
MERRA2_DATA_BASE = f"https://{MERRA2_HOST}/data/MERRA2/{MERRA2_COLLECTION}"
EARTHDATA_HOST = "urs.earthdata.nasa.gov"

# MERRA-2 inst6 的四个分析时次（UTC 整点）
MERRA2_VALID_HOURS = (0, 6, 12, 18)

_CHUNK_SIZE = 1 << 20


class Merra2DownloadError(RuntimeError):
    """MERRA-2 下载失败（message 面向用户，可直接写入任务错误）。"""


class Merra2AuthError(Merra2DownloadError):
    """未配置或无法建立 NASA Earthdata Login 凭据。"""


# ---------------------------------------------------------------- 规则 --
def merra2_file_name(day: date) -> str:
    """官方文件名：MERRA2_400.inst6_3d_ana_Np.YYYYMMDD.nc4。"""
    return f"MERRA2_{MERRA2_STREAM}.{MERRA2_PRODUCT}.{day:%Y%m%d}.nc4"


def merra2_file_url(day: date) -> str:
    """官方数据下载地址（goldsmr5 数据服务）。"""
    return f"{MERRA2_DATA_BASE}/{day:%Y}/{day:%m}/{merra2_file_name(day)}"


def validate_merra2_time(moment: datetime) -> datetime:
    """校验时间必须是 00/06/12/18 UTC 整点；否则抛 Merra2DownloadError。"""
    if moment.minute or moment.second or moment.microsecond:
        raise Merra2DownloadError(
            f"MERRA-2 只提供 00/06/12/18 UTC 整点分析场，收到 {moment.isoformat()}"
        )
    if moment.hour not in MERRA2_VALID_HOURS:
        allowed = "/".join(f"{hour:02d}" for hour in MERRA2_VALID_HOURS)
        raise Merra2DownloadError(
            f"MERRA-2 时间必须为 {allowed} UTC 整点，收到 {moment:%Y-%m-%d %H:%M}"
        )
    return moment


def iter_merra2_days(start_time: datetime, end_time: datetime) -> list[date]:
    """时间范围覆盖的日期列表（一天一个文件，含四个时次）。"""
    validate_merra2_time(start_time)
    validate_merra2_time(end_time)
    if start_time > end_time:
        raise Merra2DownloadError("开始时间不能晚于结束时间。")

    days: list[date] = []
    current = start_time.date()
    while current <= end_time.date():
        days.append(current)
        current += timedelta(days=1)
    return days


# ---------------------------------------------------------------- 凭据 --
def earthdata_credentials() -> tuple[str, str] | None:
    """读取 Earthdata 凭据，返回 (username, password)；未配置返回 None。

    绝不记录、打印或在异常信息里携带凭据内容。
    """
    username = os.environ.get("EARTHDATA_USERNAME")
    password = os.environ.get("EARTHDATA_PASSWORD")
    if username and password:
        return username, password

    try:
        import netrc

        auth = netrc.netrc().authenticators(EARTHDATA_HOST)
    except Exception:                               # noqa: BLE001 - 探测失败视为未配置
        auth = None
    if auth and auth[0] and auth[2]:
        return auth[0], auth[2]
    return None


def earthdata_credentials_available() -> bool:
    """当前机器是否已配置 Earthdata 凭据（只返回布尔值）。"""
    return earthdata_credentials() is not None


def build_earthdata_opener(credentials: tuple[str, str] | None = None):
    """构造带 cookie + Earthdata Login 认证的 urllib opener。

    未配置凭据时抛 Merra2AuthError，明确失败，绝不伪装成功。
    """
    creds = credentials or earthdata_credentials()
    if creds is None:
        raise Merra2AuthError(
            "未配置 NASA Earthdata Login 凭据：请设置 EARTHDATA_USERNAME / "
            "EARTHDATA_PASSWORD 环境变量，或在 ~/.netrc 写入 "
            f"machine {EARTHDATA_HOST} 的 login/password。"
        )

    username, password = creds
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, f"https://{EARTHDATA_HOST}", username, password)
    auth = urllib.request.HTTPBasicAuthHandler(manager)
    cookies = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies), auth)


# ---------------------------------------------------------------- 下载 --
def _cleanup(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _expected_content_length(response) -> int | None:
    """响应头声明的 Content-Length；缺失或非法时返回 None（不因此判失败）。"""
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        expected = int(headers.get("Content-Length"))
    except (TypeError, ValueError):
        return None
    return expected if expected >= 0 else None


def _download_one(opener, url: str, target: Path, timeout: float) -> int:
    """流式下载单个文件到临时 .part 再原子改名；返回字节数。"""
    tmp = target.with_name(target.name + ".part")
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with opener.open(url, timeout=timeout) as response:
            expected = _expected_content_length(response)
            with tmp.open("wb") as handle:
                while True:
                    chunk = response.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    handle.write(chunk)
                    written += len(chunk)
        if written <= 0:
            raise Merra2DownloadError(f"下载内容为空：{target.name}")
        if expected is not None and written != expected:
            raise Merra2DownloadError(
                f"下载不完整（Incomplete MERRA-2 download: expected {expected} bytes, "
                f"wrote {written} bytes）：{url}"
            )
        os.replace(tmp, target)
    except Merra2DownloadError:
        _cleanup(tmp)
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError) as exc:
        _cleanup(tmp)
        raise Merra2DownloadError(f"下载失败：{target.name}（{exc}）") from exc
    return written


def run_merra2_download(
    *,
    start_time: datetime,
    end_time: datetime,
    output_dir: Path,
    force: bool = False,
    opener=None,
    progress: Callable[[int, str], None] | None = None,
    timeout: float = 120.0,
) -> dict:
    """真实下载 MERRA-2 inst6_3d_ana_Np 日文件到 ``output_dir/<yyyy>/``。

    任何失败或零文件都抛 Merra2DownloadError；未配置凭据抛 Merra2AuthError。
    返回 ``{"files": [...], "stats": {...}, "failed": [...]}``。
    ``opener`` 仅用于测试注入，生产留空即使用 Earthdata 认证 opener。
    """
    days = iter_merra2_days(start_time, end_time)
    if not days:
        raise Merra2DownloadError("时间范围内没有任何 MERRA-2 时次。")

    if opener is None:
        opener = build_earthdata_opener()

    output_dir = Path(output_dir)
    files: list[Path] = []
    skipped: list[Path] = []
    failed: list[tuple[str, str]] = []
    total_bytes = 0
    downloaded = 0

    for index, day in enumerate(days, 1):
        target = output_dir / f"{day.year}" / merra2_file_name(day)
        url = merra2_file_url(day)

        if not force and target.is_file() and target.stat().st_size > 0:
            skipped.append(target)
            files.append(target)
            if progress is not None:
                progress(int(index / len(days) * 100), f"已存在，跳过 {target.name}")
            continue

        if progress is not None:
            progress(int((index - 1) / len(days) * 100), f"正在下载 {target.name}")
        try:
            size = _download_one(opener, url, target, timeout)
        except Merra2DownloadError as exc:
            failed.append((day.isoformat(), str(exc)))
            logger.error("MERRA-2 download failed: %s — %s", target.name, exc)
            continue

        files.append(target)
        downloaded += 1
        total_bytes += size
        logger.info("MERRA-2 downloaded: %s (%.1f MB)", target.name, size / 1048576)

    if failed:
        detail = "；".join(f"{day}: {message}" for day, message in failed)
        raise Merra2DownloadError(
            f"{len(failed)}/{len(days)} 个 MERRA-2 文件下载失败：{detail}"
        )
    if not files:
        raise Merra2DownloadError("MERRA-2 下载失败：未获取到任何数据文件。")

    stats = {
        "total": len(days),
        "downloaded": downloaded,
        "skipped": len(skipped),
        "success": len(files),
        "failed": 0,
        "bytes": total_bytes,
    }
    return {"files": [str(path) for path in files], "stats": stats, "failed": []}


__all__ = [
    "Merra2AuthError",
    "Merra2DownloadError",
    "build_earthdata_opener",
    "earthdata_credentials",
    "earthdata_credentials_available",
    "iter_merra2_days",
    "merra2_file_name",
    "merra2_file_url",
    "run_merra2_download",
    "validate_merra2_time",
]
