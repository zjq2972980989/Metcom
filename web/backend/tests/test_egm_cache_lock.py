"""EGM2008 text-cache lock recovery and cache-integrity tests."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from services import egm_gridgen_adapter


def _source_and_paths(tmp_path: Path):
    source = tmp_path / "source.mat"
    source.write_bytes(b"egm-source")
    source_manifest = egm_gridgen_adapter._source_manifest(source)
    cache_file, manifest_file = egm_gridgen_adapter._cache_paths(
        source_manifest, tmp_path / "cache"
    )
    return source_manifest, cache_file, manifest_file


def _write_fake_cache(source_manifest, cache_file: Path, manifest_file: Path) -> None:
    text_bytes = f"cache:{source_manifest['source_sha256']}\n".encode("utf-8")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(text_bytes)
    manifest_file.write_text(
        json.dumps(
            {
                "cache_version": egm_gridgen_adapter._CACHE_VERSION,
                "source_path": source_manifest["source_path"],
                "source_size": source_manifest["source_size"],
                "source_mtime_ns": source_manifest["source_mtime_ns"],
                "source_sha256": source_manifest["source_sha256"],
                "line_count": egm_gridgen_adapter._EXPECTED_LINE_COUNT,
                "text_bytes": len(text_bytes),
                "text_sha256": hashlib.sha256(text_bytes).hexdigest(),
            }
        ),
        encoding="utf-8",
    )


def _short_lock_timeout(monkeypatch) -> None:
    monkeypatch.setattr(egm_gridgen_adapter, "_CACHE_LOCK_TIMEOUT_S", 0.2)
    monkeypatch.setattr(egm_gridgen_adapter, "_CACHE_LOCK_POLL_S", 0.01)


def _run_lock_probe(source_manifest, cache_file: Path, manifest_file: Path) -> str:
    backend_dir = Path(egm_gridgen_adapter.__file__).resolve().parents[1]
    project_root = backend_dir.parent.parent
    env = os.environ.copy()
    python_path = [str(backend_dir), str(project_root)]
    if env.get("PYTHONPATH"):
        python_path.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    script = """
import json
import sys
from pathlib import Path

from services import egm_gridgen_adapter as adapter

adapter._CACHE_LOCK_TIMEOUT_S = 0.2
adapter._CACHE_LOCK_POLL_S = 0.01
source_manifest = json.loads(sys.argv[1])
try:
    lock = adapter._acquire_cache_lock(
        Path(sys.argv[2]), Path(sys.argv[3]), source_manifest
    )
except adapter.EgmGridgenError:
    print("blocked")
else:
    adapter._release_cache_lock(lock)
    print("acquired")
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            json.dumps(source_manifest),
            str(cache_file),
            str(manifest_file),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    return result.stdout.strip()


def test_os_lock_blocks_second_holder_until_release(monkeypatch, tmp_path):
    _short_lock_timeout(monkeypatch)
    source_manifest, cache_file, manifest_file = _source_and_paths(tmp_path)

    first = egm_gridgen_adapter._acquire_cache_lock(
        cache_file, manifest_file, source_manifest
    )
    assert first is not None
    try:
        with pytest.raises(egm_gridgen_adapter.EgmGridgenError, match="超时"):
            egm_gridgen_adapter._acquire_cache_lock(
                cache_file, manifest_file, source_manifest
            )
    finally:
        egm_gridgen_adapter._release_cache_lock(first)

    second = egm_gridgen_adapter._acquire_cache_lock(
        cache_file, manifest_file, source_manifest
    )
    assert second is not None
    egm_gridgen_adapter._release_cache_lock(second)


def test_legacy_stale_lock_file_is_safely_recovered(monkeypatch, tmp_path):
    _short_lock_timeout(monkeypatch)
    source_manifest, cache_file, manifest_file = _source_and_paths(tmp_path)
    lock_file = cache_file.with_suffix(cache_file.suffix + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("pid=999999999\ncreated_at=1\n", encoding="utf-8")
    old = time.time() - 86400
    os.utime(lock_file, (old, old))

    lock = egm_gridgen_adapter._acquire_cache_lock(
        cache_file, manifest_file, source_manifest
    )
    assert lock is not None
    try:
        assert lock.path == lock_file
    finally:
        egm_gridgen_adapter._release_cache_lock(lock)

    metadata = json.loads(lock_file.read_text(encoding="utf-8"))
    assert metadata["pid"] == os.getpid()
    assert metadata["owner_token"]
    assert metadata["created_at_unix"] > old


def test_old_but_actively_held_lock_is_not_reclaimed(monkeypatch, tmp_path):
    _short_lock_timeout(monkeypatch)
    source_manifest, cache_file, manifest_file = _source_and_paths(tmp_path)
    lock = egm_gridgen_adapter._acquire_cache_lock(
        cache_file, manifest_file, source_manifest
    )
    assert lock is not None
    try:
        old = time.time() - 86400
        os.utime(lock.path, (old, old))
        with pytest.raises(egm_gridgen_adapter.EgmGridgenError, match="超时"):
            egm_gridgen_adapter._acquire_cache_lock(
                cache_file, manifest_file, source_manifest
            )
    finally:
        egm_gridgen_adapter._release_cache_lock(lock)


def test_os_lock_conflicts_across_processes(tmp_path):
    source_manifest, cache_file, manifest_file = _source_and_paths(tmp_path)
    lock = egm_gridgen_adapter._acquire_cache_lock(
        cache_file, manifest_file, source_manifest
    )
    assert lock is not None
    try:
        assert _run_lock_probe(source_manifest, cache_file, manifest_file) == "blocked"
    finally:
        egm_gridgen_adapter._release_cache_lock(lock)

    assert _run_lock_probe(source_manifest, cache_file, manifest_file) == "acquired"


def test_lock_recovers_after_holder_process_is_terminated(monkeypatch, tmp_path):
    _short_lock_timeout(monkeypatch)
    source_manifest, cache_file, manifest_file = _source_and_paths(tmp_path)
    backend_dir = Path(egm_gridgen_adapter.__file__).resolve().parents[1]
    project_root = backend_dir.parent.parent
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join((str(backend_dir), str(project_root)))
    script = """
import json
import sys
import time
from pathlib import Path

from services import egm_gridgen_adapter as adapter

lock = adapter._acquire_cache_lock(
    Path(sys.argv[2]), Path(sys.argv[3]), json.loads(sys.argv[1])
)
if lock is None:
    raise RuntimeError("lock unexpectedly skipped")
print("locked", flush=True)
time.sleep(30)
"""
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            json.dumps(source_manifest),
            str(cache_file),
            str(manifest_file),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(egm_gridgen_adapter.EgmGridgenError, match="超时"):
            egm_gridgen_adapter._acquire_cache_lock(
                cache_file, manifest_file, source_manifest
            )
    finally:
        process.terminate()
        process.wait(timeout=5)

    recovered = egm_gridgen_adapter._acquire_cache_lock(
        cache_file, manifest_file, source_manifest
    )
    assert recovered is not None
    egm_gridgen_adapter._release_cache_lock(recovered)


def test_concurrent_generation_publishes_once_and_cache_hits(
    monkeypatch, tmp_path
):
    _short_lock_timeout(monkeypatch)
    source_manifest, cache_file, manifest_file = _source_and_paths(tmp_path)
    lock_file = cache_file.with_suffix(cache_file.suffix + ".lock")
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("pid=999999999\n", encoding="utf-8")

    generation_calls = 0
    generation_lock = threading.Lock()

    def fake_generate(current_manifest, current_cache, current_manifest_file):
        nonlocal generation_calls
        with generation_lock:
            generation_calls += 1
        time.sleep(0.05)
        _write_fake_cache(current_manifest, current_cache, current_manifest_file)

    monkeypatch.setattr(egm_gridgen_adapter, "_generate_text_cache", fake_generate)
    barrier = threading.Barrier(3)
    results: list[Path] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(
                egm_gridgen_adapter.prepare_gridgen_text(
                    source_manifest["source_path"], cache_dir=cache_file.parent
                )
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised in the test thread below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    assert errors == []
    assert results == [cache_file, cache_file]
    assert generation_calls == 1

    hit = egm_gridgen_adapter.prepare_gridgen_text(
        source_manifest["source_path"], cache_dir=cache_file.parent
    )
    assert hit == cache_file
    assert generation_calls == 1


def test_corrupt_cache_and_manifest_are_regenerated(monkeypatch, tmp_path):
    _short_lock_timeout(monkeypatch)
    source_manifest, cache_file, manifest_file = _source_and_paths(tmp_path)
    generation_calls = 0

    def fake_generate(current_manifest, current_cache, current_manifest_file):
        nonlocal generation_calls
        generation_calls += 1
        _write_fake_cache(current_manifest, current_cache, current_manifest_file)

    monkeypatch.setattr(egm_gridgen_adapter, "_generate_text_cache", fake_generate)
    prepare = lambda: egm_gridgen_adapter.prepare_gridgen_text(
        source_manifest["source_path"], cache_dir=cache_file.parent
    )

    assert prepare() == cache_file
    assert prepare() == cache_file
    assert generation_calls == 1

    cache_file.write_text("corrupt-text\n", encoding="utf-8")
    assert prepare() == cache_file
    assert generation_calls == 2

    manifest_file.write_text("{not-json", encoding="utf-8")
    assert prepare() == cache_file
    assert generation_calls == 3
