"""Gridgen EGM2008 text adapter for Web Grid tasks.

The original Gridgen implementation consumes a three-column ``lat lon N`` text
grid through ``metcom.gridgen.orth_to_ell.load_egm2008_txt()``. The repository
ships the same EGM2008 data as a MATLAB v7.3/HDF5 ``.mat`` file. This module
performs only the mechanical conversion from that HDF5 grid to the Gridgen text
layout, then delegates parsing and bilinear interpolation to the original
metcom function.

The generated text is cached outside ``web/data`` and ``benchmarks``. Cache
files are keyed by the source file fingerprint, written to a unique temporary
file, and atomically published. A sidecar manifest records the expected line
count and text digest; a damaged cache is regenerated instead of used. Cache
generation is serialized by an operating-system file lock, so an abnormally
terminated process releases the lock automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import errno
import secrets
import tempfile
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import h5py
import numpy as np
from scipy.interpolate import RegularGridInterpolator

from metcom.gridgen.orth_to_ell import _save_to_text, load_egm2008_txt

_CACHE_VERSION = 1
_CACHE_ROOT_NAME = "metcom-web-egm-gridgen-cache"
_EXPECTED_MAT_SHAPE = (721, 1441)
_EXPECTED_LINE_COUNT = _EXPECTED_MAT_SHAPE[0] * _EXPECTED_MAT_SHAPE[1]
_N_SCALE = 1000.0
_CACHE_LOCK_TIMEOUT_S = 120.0
_CACHE_LOCK_POLL_S = 0.1
_PROCESS_OWNER_TOKEN = secrets.token_hex(16)
_NORTH_POLE_N_M = 14.899
_SOUTH_POLE_N_M = -30.150
_POLE_ROW_TOL_M = 2.0


class EgmGridgenError(ValueError):
    """Raised when the EGM grid or an interpolation request is invalid."""


@dataclass(frozen=True)
class GridgenEgm:
    """Prepared Gridgen EGM source and its original-method interpolator."""

    interpolator: RegularGridInterpolator
    text_path: Path
    source_path: Path
    source_sha256: str
    text_sha256: str
    line_count: int


@dataclass(frozen=True)
class _CacheLock:
    """An acquired operating-system lock tied to an open file descriptor."""

    fd: int
    path: Path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_manifest(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise EgmGridgenError(f"EGM2008 .mat 文件不存在：{resolved}")
    stat = resolved.stat()
    return {
        "source_path": str(resolved),
        "source_size": int(stat.st_size),
        "source_mtime_ns": int(stat.st_mtime_ns),
        "source_sha256": _sha256_file(resolved),
    }


def _default_cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / _CACHE_ROOT_NAME / f"v{_CACHE_VERSION}"


def _cache_paths(manifest: dict[str, Any], cache_dir: Path | None) -> tuple[Path, Path]:
    directory = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
    stem = f"egm2008_0250_{manifest['source_sha256'][:16]}"
    return directory / f"{stem}.txt", directory / f"{stem}.json"


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _cache_is_valid(cache_file: Path, manifest_file: Path,
                    source_manifest: dict[str, Any]) -> bool:
    if not cache_file.is_file() or not manifest_file.is_file():
        return False
    cached = _read_manifest(manifest_file)
    if cached is None:
        return False
    required = {
        "cache_version": _CACHE_VERSION,
        "source_path": source_manifest["source_path"],
        "source_size": source_manifest["source_size"],
        "source_mtime_ns": source_manifest["source_mtime_ns"],
        "source_sha256": source_manifest["source_sha256"],
        "line_count": _EXPECTED_LINE_COUNT,
    }
    if any(cached.get(key) != value for key, value in required.items()):
        return False
    try:
        text_sha256 = str(cached["text_sha256"])
        text_bytes = int(cached["text_bytes"])
    except (KeyError, TypeError, ValueError):
        return False
    try:
        if cache_file.stat().st_size != text_bytes:
            return False
        return _sha256_file(cache_file) == text_sha256
    except OSError:
        return False


def _try_acquire_os_lock(fd: int) -> bool:
    """Try to acquire an exclusive OS lock for ``fd`` without blocking."""
    try:
        import fcntl
    except ImportError:
        fcntl = None

    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise EgmGridgenError(f"无法获取 EGM2008 缓存文件锁：{exc}") from exc
        return True

    try:
        import msvcrt
    except ImportError as exc:
        raise EgmGridgenError("当前平台不支持 EGM2008 缓存文件锁") from exc

    os.lseek(fd, 0, os.SEEK_SET)
    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return False
        raise EgmGridgenError(f"无法获取 EGM2008 缓存文件锁：{exc}") from exc
    return True


def _release_os_lock(fd: int) -> None:
    """Release a lock explicitly; closing the descriptor is the final fallback."""
    try:
        import fcntl
    except ImportError:
        fcntl = None

    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        else:
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def _write_lock_metadata(fd: int, lock_file: Path) -> None:
    """Write diagnostic owner metadata while holding the OS lock."""
    payload = {
        "pid": os.getpid(),
        "created_at_unix": time.time(),
        "owner_token": _PROCESS_OWNER_TOKEN,
        "lock_path": str(lock_file.resolve()),
    }
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    os.write(fd, encoded)
    os.fsync(fd)


def _release_cache_lock(lock: _CacheLock) -> None:
    """Release an acquired cache lock without deleting the persistent lock file."""
    try:
        _release_os_lock(lock.fd)
    finally:
        os.close(lock.fd)


def _acquire_cache_lock(cache_file: Path, manifest_file: Path,
                        source_manifest: dict[str, Any]) -> _CacheLock | None:
    """Serialize cache generation using a non-blocking OS lock."""
    lock_file = cache_file.with_suffix(cache_file.suffix + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _CACHE_LOCK_TIMEOUT_S
    while True:
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_RDWR)
        except OSError as exc:
            raise EgmGridgenError(f"无法打开 EGM2008 缓存锁：{lock_file}（{exc}）") from exc

        try:
            if os.fstat(fd).st_size < 1:
                os.write(fd, b"\0")
            acquired = _try_acquire_os_lock(fd)
        except (OSError, EgmGridgenError):
            os.close(fd)
            raise

        if not acquired:
            os.close(fd)
            if _cache_is_valid(cache_file, manifest_file, source_manifest):
                return None
            if time.monotonic() >= deadline:
                raise EgmGridgenError(f"等待 EGM2008 缓存锁超时：{lock_file}")
            time.sleep(_CACHE_LOCK_POLL_S)
            continue

        try:
            _write_lock_metadata(fd, lock_file)
        except OSError as exc:
            _release_os_lock(fd)
            os.close(fd)
            raise EgmGridgenError(
                f"无法写入 EGM2008 缓存锁元数据：{lock_file}（{exc}）"
            ) from exc
        return _CacheLock(fd=fd, path=lock_file)


def load_reordered_grid(mat_path: Path | str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return the Gridgen-order latitude, longitude and N arrays.

    The returned arrays are exactly the logical rows written to the text cache:
    latitude is ``+90 -> -90``, longitude is ``0 -> 360``, and N has shape
    ``(n_lat, n_lon)``.
    """
    mat_path = Path(mat_path).resolve()
    try:
        with h5py.File(mat_path, "r") as handle:
            raw = np.asarray(handle["egm2008"][()], dtype=float)
    except (OSError, KeyError) as exc:
        raise EgmGridgenError(f"无法读取 EGM2008 .mat 数据集：{mat_path}") from exc

    if raw.shape != _EXPECTED_MAT_SHAPE:
        raise EgmGridgenError(
            f"EGM2008 .mat 维度不匹配：期望 {_EXPECTED_MAT_SHAPE}，实际 {raw.shape}"
        )
    if not np.all(np.isfinite(raw)):
        raise EgmGridgenError("EGM2008 .mat 包含 NaN 或 Inf")

    head_mean = float(raw[0].mean())
    tail_mean = float(raw[-1].mean())
    head_north = abs(head_mean - _NORTH_POLE_N_M) <= _POLE_ROW_TOL_M
    tail_north = abs(tail_mean - _NORTH_POLE_N_M) <= _POLE_ROW_TOL_M
    head_south = abs(head_mean - _SOUTH_POLE_N_M) <= _POLE_ROW_TOL_M
    tail_south = abs(tail_mean - _SOUTH_POLE_N_M) <= _POLE_ROW_TOL_M
    if head_north and tail_south:
        pass
    elif head_south and tail_north:
        raw = raw[::-1, :]
    else:
        raise EgmGridgenError(
            "无法判定 EGM2008 .mat 纬度方向：首末行不是可识别的南北极点"
        )

    n_lat, n_lon = raw.shape
    half = (n_lon - 1) // 2
    # Source columns are -180 ... 0 ... +180. The original Gridgen text uses
    # 0 ... 180 ... 360, with a repeated physical endpoint at 0/360.
    column_order = np.concatenate((
        np.arange(half, n_lon, dtype=int),
        np.arange(1, half, dtype=int),
        np.array([half], dtype=int),
    ))
    ordered = raw[:, column_order]

    lats = np.linspace(90.0, -90.0, n_lat)
    lons = np.linspace(0.0, 360.0, n_lon)
    return lats, lons, ordered


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _generate_text_cache(source_manifest: dict[str, Any], cache_file: Path,
                         manifest_file: Path) -> None:
    lats, lons, values = load_reordered_grid(source_manifest["source_path"])
    scaled = values * _N_SCALE
    if not np.allclose(scaled, np.rint(scaled), rtol=0.0, atol=1e-9):
        raise EgmGridgenError("EGM2008 N 值不是 0.001 m 整数倍，无法用 %0.3f 无损写入")

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=cache_file.parent, prefix=f".{cache_file.name}.", suffix=".tmp"
    )
    os.close(fd)
    temporary_path = Path(temporary)
    text_digest = hashlib.sha256()
    text_bytes = 0
    try:
        lon_strings = [f"{value:.6f}" for value in lons]
        with open(temporary_path, "w", encoding="utf-8", newline="\n") as handle:
            for lat, row in zip(lats, values):
                lat_text = f"{lat:.6f}"
                for lon_text, geoid in zip(lon_strings, row):
                    line = f"{lat_text} {lon_text} {geoid:.3f}\n"
                    encoded = line.encode("utf-8")
                    handle.write(line)
                    text_digest.update(encoded)
                    text_bytes += len(encoded)
        os.replace(temporary_path, cache_file)
    finally:
        temporary_path.unlink(missing_ok=True)

    _write_json_atomic(manifest_file, {
        "cache_version": _CACHE_VERSION,
        "source_path": source_manifest["source_path"],
        "source_size": source_manifest["source_size"],
        "source_mtime_ns": source_manifest["source_mtime_ns"],
        "source_sha256": source_manifest["source_sha256"],
        "line_count": _EXPECTED_LINE_COUNT,
        "text_bytes": text_bytes,
        "text_sha256": text_digest.hexdigest(),
    })


def prepare_gridgen_text(mat_path: Path | str, cache_dir: Path | str | None = None) -> Path:
    """Ensure and return the cached Gridgen ``lat lon N`` text file."""
    source = Path(mat_path)
    source_manifest = _source_manifest(source)
    cache_file, manifest_file = _cache_paths(
        source_manifest, Path(cache_dir) if cache_dir is not None else None
    )
    if _cache_is_valid(cache_file, manifest_file, source_manifest):
        return cache_file
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    cache_lock = _acquire_cache_lock(cache_file, manifest_file, source_manifest)
    try:
        if not _cache_is_valid(cache_file, manifest_file, source_manifest):
            _generate_text_cache(source_manifest, cache_file, manifest_file)
        if not _cache_is_valid(cache_file, manifest_file, source_manifest):
            raise EgmGridgenError(f"EGM2008 缓存生成后校验失败：{cache_file}")
    finally:
        if cache_lock is not None:
            _release_cache_lock(cache_lock)
    return cache_file


@lru_cache(maxsize=4)
def _load_interpolator_cached(text_path: str, text_sha256: str) -> RegularGridInterpolator:
    del text_sha256  # Digest is part of the key; parsing is delegated to metcom.
    interpolator, _, _ = load_egm2008_txt(Path(text_path))
    return interpolator


def load_gridgen_egm(mat_path: Path | str,
                     cache_dir: Path | str | None = None) -> GridgenEgm:
    """Prepare the text cache and load it through the original Gridgen parser."""
    text_path = prepare_gridgen_text(mat_path, cache_dir=cache_dir)
    manifest_file = text_path.with_suffix(".json")
    manifest = _read_manifest(manifest_file)
    if manifest is None:
        raise EgmGridgenError(f"无法读取 EGM2008 缓存清单：{manifest_file}")
    text_sha256 = str(manifest["text_sha256"])
    interpolator = _load_interpolator_cached(str(text_path), text_sha256)
    return GridgenEgm(
        interpolator=interpolator,
        text_path=text_path,
        source_path=Path(mat_path).resolve(),
        source_sha256=str(manifest["source_sha256"]),
        text_sha256=text_sha256,
        line_count=int(manifest["line_count"]),
    )


def load_gridgen_interpolator(mat_path: Path | str,
                              cache_dir: Path | str | None = None) -> RegularGridInterpolator:
    """Load Gridgen EGM text and return the original bilinear interpolator."""
    return load_gridgen_egm(mat_path, cache_dir=cache_dir).interpolator


def interpolate_geoid_heights(interpolator: RegularGridInterpolator,
                              lons: Any, lats: Any) -> np.ndarray:
    """Interpolate N for paired longitudes/latitudes using Gridgen semantics."""
    lon_values = np.asarray(lons, dtype=float).ravel()
    lat_values = np.asarray(lats, dtype=float).ravel()
    if lon_values.size != lat_values.size:
        raise EgmGridgenError("经度点数与纬度点数不一致")
    if lon_values.size == 0:
        raise EgmGridgenError("EGM2008 插值点为空")
    if not (np.all(np.isfinite(lon_values)) and np.all(np.isfinite(lat_values))):
        raise EgmGridgenError("EGM2008 插值坐标包含 NaN 或 Inf")
    if np.any(lat_values < -90.0) or np.any(lat_values > 90.0):
        raise EgmGridgenError("EGM2008 插值纬度超出 -90..90")
    if np.any(lon_values < -180.0) or np.any(lon_values > 360.0):
        raise EgmGridgenError("EGM2008 插值经度超出 -180..360")

    lon_0360 = np.where(lon_values < 0.0, lon_values + 360.0, lon_values)
    geoid = np.asarray(
        interpolator(np.column_stack([lat_values, lon_0360])), dtype=float
    )
    if not np.all(np.isfinite(geoid)):
        raise EgmGridgenError("EGM2008 插值结果为 NaN 或 Inf")
    return geoid


def write_height_files(*, orthometric_path: Path | str,
                       ellipsoidal_path: Path | str,
                       station_names: list[str],
                       lats: Any, lons: Any,
                       orthometric: Any, ellipsoidal: Any) -> tuple[Path, Path]:
    """Atomically write the two four-column site files with ``_save_to_text``."""
    lat_values = np.asarray(lats, dtype=float).ravel()
    lon_values = np.asarray(lons, dtype=float).ravel()
    ortho_values = np.asarray(orthometric, dtype=float).ravel()
    ellip_values = np.asarray(ellipsoidal, dtype=float).ravel()
    arrays = (lat_values, lon_values, ortho_values, ellip_values)
    if len(station_names) != lat_values.size or any(
        values.size != lat_values.size for values in arrays
    ):
        raise EgmGridgenError("站点名称、经纬度和高度数组长度不一致")
    if not all(np.all(np.isfinite(values)) for values in arrays):
        raise EgmGridgenError("站点经纬度或高度包含 NaN 或 Inf")

    ortho_path = Path(orthometric_path)
    ellip_path = Path(ellipsoidal_path)
    if ortho_path.resolve() == ellip_path.resolve():
        raise EgmGridgenError("正高文件和椭球高文件不能是同一路径")
    for path in (ortho_path, ellip_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    temporary_paths: list[Path] = []
    try:
        for target, heights in ((ortho_path, ortho_values), (ellip_path, ellip_values)):
            fd, temporary = tempfile.mkstemp(
                dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
            )
            os.close(fd)
            temporary_path = Path(temporary)
            temporary_paths.append(temporary_path)
            _save_to_text(temporary_path, station_names, lat_values, lon_values, heights)
        os.replace(temporary_paths[0], ortho_path)
        os.replace(temporary_paths[1], ellip_path)
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)
    return ortho_path, ellip_path


__all__ = [
    "EgmGridgenError",
    "GridgenEgm",
    "interpolate_geoid_heights",
    "load_gridgen_egm",
    "load_gridgen_interpolator",
    "load_reordered_grid",
    "prepare_gridgen_text",
    "write_height_files",
]
