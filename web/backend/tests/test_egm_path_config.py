"""EGM2008 path configuration and Gridgen/retrieval source consistency tests."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from config import PROJECT_ROOT, get_settings
from engine import adapter
from services import egm_gridgen_adapter, grid_service
from services.errors import InvalidRequestError

REAL_WEB_DATA = PROJECT_ROOT / "web" / "data"
ERA5_EGM_NAME = "geoidheight_EGM2008_0250.mat"


def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def _configure_dependent(monkeypatch, dependent_dir: Path) -> None:
    monkeypatch.setenv("METCOM_WEB_METCOM_DEPENDENT_DIR", str(dependent_dir))
    _clear_settings_cache()


def test_default_era5_configuration_resolves_real_egm_file(monkeypatch):
    monkeypatch.setenv("METCOM_WEB_DATA_DIR", str(REAL_WEB_DATA))
    monkeypatch.delenv("METCOM_WEB_METCOM_DEPENDENT_DIR", raising=False)
    _clear_settings_cache()

    spec = adapter.source_spec("era5")
    expected = REAL_WEB_DATA / "dependent" / "egm2008" / ERA5_EGM_NAME

    assert expected.is_file()
    assert adapter.resolve_egm_path(spec) == expected
    assert grid_service.resolve_gridgen_egm_path("era5") == expected


def test_custom_engine_egm_dir_is_used_by_gridgen_build(monkeypatch, tmp_path):
    dependent = tmp_path / "dependent"
    egm_dir = dependent / "egm2008"
    egm_dir.mkdir(parents=True)
    target = egm_dir / ERA5_EGM_NAME
    target.write_bytes(b"configured-egm-placeholder")
    _configure_dependent(monkeypatch, dependent)

    recorded: dict[str, Path] = {}

    def fake_load(path, cache_dir=None):
        del cache_dir
        recorded["path"] = Path(path)
        return SimpleNamespace(
            source_path=Path(path),
            text_path=tmp_path / "egm-text.txt",
            interpolator=lambda points: np.zeros(len(points), dtype=float),
        )

    monkeypatch.setattr(egm_gridgen_adapter, "load_gridgen_egm", fake_load)

    grid_service.build_grid_height_files(
        dataset="era5",
        lon_min=115.0,
        lon_max=115.5,
        lat_min=37.0,
        lat_max=37.5,
        orthometric_path=tmp_path / "orthometric.txt",
        ellipsoidal_path=tmp_path / "ellipsoidal.txt",
    )

    assert recorded["path"] == target


def test_missing_target_does_not_fall_back_to_real_default(monkeypatch, tmp_path):
    dependent = tmp_path / "dependent"
    (dependent / "egm2008").mkdir(parents=True)
    _configure_dependent(monkeypatch, dependent)
    expected = dependent / "egm2008" / ERA5_EGM_NAME

    with pytest.raises(InvalidRequestError) as excinfo:
        grid_service.resolve_gridgen_egm_path("era5")

    message = str(excinfo.value)
    assert "ERA5" in message
    assert str(expected) in message


def test_invalid_egm_directory_configuration_fails_clearly(monkeypatch, tmp_path):
    dependent_file = tmp_path / "dependent-file"
    dependent_file.write_text("not a directory", encoding="utf-8")
    _configure_dependent(monkeypatch, dependent_file)

    with pytest.raises(InvalidRequestError) as excinfo:
        grid_service.resolve_gridgen_egm_path("era5")

    message = str(excinfo.value)
    assert "ERA5" in message
    assert str(dependent_file / "egm2008") in message


def test_target_path_that_is_directory_fails_clearly(monkeypatch, tmp_path):
    dependent = tmp_path / "dependent"
    target_directory = dependent / "egm2008" / ERA5_EGM_NAME
    target_directory.mkdir(parents=True)
    _configure_dependent(monkeypatch, dependent)

    with pytest.raises(InvalidRequestError) as excinfo:
        grid_service.resolve_gridgen_egm_path("era5")

    message = str(excinfo.value)
    assert "ERA5" in message
    assert str(target_directory) in message


def test_unsupported_source_resolution_fails_instead_of_using_other_egm(
    monkeypatch, tmp_path
):
    dependent = tmp_path / "dependent"
    egm_dir = dependent / "egm2008"
    egm_dir.mkdir(parents=True)
    merra2_spec = adapter.source_spec("merra2")
    expected = egm_dir / merra2_spec.egm_file
    expected.write_bytes(b"unverified-resolution-placeholder")
    _configure_dependent(monkeypatch, dependent)

    with pytest.raises(InvalidRequestError) as excinfo:
        grid_service.resolve_gridgen_egm_path("merra2")

    message = str(excinfo.value)
    assert "MERRA2" in message
    assert str(expected) in message
    assert "0.25" in message


def test_cache_is_bound_to_source_sha256_after_source_change(monkeypatch, tmp_path):
    source = tmp_path / "source.mat"
    source.write_bytes(b"first")
    original_stat = source.stat()
    cache_dir = tmp_path / "cache"
    generated: list[str] = []

    def fake_generate(source_manifest, cache_file, manifest_file):
        generated.append(source_manifest["source_sha256"])
        text_bytes = f"cache:{source_manifest['source_sha256']}\n".encode("utf-8")
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(text_bytes)
        manifest_file.write_text(
            json.dumps({
                "cache_version": egm_gridgen_adapter._CACHE_VERSION,
                "source_path": source_manifest["source_path"],
                "source_size": source_manifest["source_size"],
                "source_mtime_ns": source_manifest["source_mtime_ns"],
                "source_sha256": source_manifest["source_sha256"],
                "line_count": egm_gridgen_adapter._EXPECTED_LINE_COUNT,
                "text_bytes": len(text_bytes),
                "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
            }),
            encoding="utf-8",
        )

    monkeypatch.setattr(egm_gridgen_adapter, "_generate_text_cache", fake_generate)

    first = egm_gridgen_adapter.prepare_gridgen_text(source, cache_dir=cache_dir)

    source.write_bytes(b"other")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    second = egm_gridgen_adapter.prepare_gridgen_text(source, cache_dir=cache_dir)

    assert second != first
    assert len(generated) == 2
    assert generated[0] != generated[1]


def test_retrieval_and_gridgen_resolve_the_same_configured_path(
    monkeypatch, tmp_path
):
    raw_root = tmp_path / "raw"
    (raw_root / "era5").mkdir(parents=True)
    dependent = tmp_path / "dependent"
    egm_dir = dependent / "egm2008"
    egm_dir.mkdir(parents=True)
    target = egm_dir / ERA5_EGM_NAME
    target.write_bytes(b"configured-egm-placeholder")
    site_file = tmp_path / "sites.txt"
    site_file.write_text("S1 30.0 100.0 10.0\n", encoding="utf-8")

    monkeypatch.setenv("METCOM_WEB_METCOM_DATA_ROOT", str(raw_root))
    _configure_dependent(monkeypatch, dependent)
    monkeypatch.setenv("METCOM_WEB_METCOM_SITE_FILE", str(site_file))
    _clear_settings_cache()

    spec = adapter.source_spec("era5")
    _, retrieval_egm, _ = adapter._engine_inputs(spec, site_file=site_file)
    gridgen_egm = grid_service.resolve_gridgen_egm_path("era5")

    assert retrieval_egm == gridgen_egm == target
