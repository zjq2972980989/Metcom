"""任务结果的落盘位置与元信息组装（数据下载 / 参数计算共用）。

约定（阶段 3）：
  - 结果文件写在 ``data/tasks/results/<task_id>/`` 下；
  - 任务记录与 metadata.json 里只保存**相对数据目录的路径**，
    绝不把服务器绝对路径写给前端；
  - 结果元信息至少包含 result_type / file_name / file_path。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from config import get_settings

logger = logging.getLogger("metcom.web.tasks")

# 结果元信息里最多列出多少条文件明细（避免任务记录被大量文件撑大）
MAX_LISTED_FILES = 500


def relative_path(path: Path | str) -> str:
    """绝对路径 → 相对平台数据目录的路径（对外只暴露这个形式）。"""
    resolved = Path(path).resolve()
    data_dir = Path(get_settings().data_dir).resolve()
    try:
        return resolved.relative_to(data_dir).as_posix()
    except ValueError:
        return resolved.name


def resolve_path(relative: str) -> Path:
    """相对路径 → 绝对路径；越出平台数据目录时抛 ValueError。"""
    settings = get_settings()
    candidate = (Path(settings.data_dir) / Path(relative)).resolve()
    if not candidate.is_relative_to(Path(settings.data_dir).resolve()):
        raise ValueError("结果路径越界")
    return candidate


def _relativize(value: str) -> str:
    """绝对路径字符串 → 相对数据目录的路径；本来就是相对路径时原样返回。"""
    candidate = Path(value)
    if not candidate.is_absolute():
        return value
    return relative_path(candidate)


def sanitize_result(result: dict[str, Any] | None) -> dict[str, Any]:
    """清洗结果元信息，保证对外绝不出现服务器绝对路径。

    阶段三之前遗留的任务记录里可能写有绝对路径；记录一旦落盘不会自动改写，
    因此读取时统一归一化，让任何历史记录经过 API 后都只暴露相对路径。
    """
    if not result:
        return {}

    cleaned = dict(result)
    for key in ("file_path", "output_dir"):
        value = cleaned.get(key)
        if isinstance(value, str) and value:
            cleaned[key] = _relativize(value)

    files = cleaned.get("files")
    if isinstance(files, list):
        cleaned["files"] = [
            _relativize(item) if isinstance(item, str) and item else item for item in files
        ]
    return cleaned


def build_result(
    *,
    result_type: str,
    path: Path | str,
    file_name: str,
    summary: str,
    files: Sequence[Path] = (),
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组装任务结果元信息（对外不含绝对路径）。"""
    result: dict[str, Any] = {
        "result_type": result_type,
        "file_name": file_name,
        "file_path": relative_path(path),
        "summary": summary,
        "file_count": len(files),
        "files": [relative_path(item) for item in list(files)[:MAX_LISTED_FILES]],
    }
    result.update(extra or {})
    return result


def write_metadata(task_id: str, task_type: str, name: str, result: dict[str, Any]) -> Path:
    """把结果元信息写到 results/<task_id>/metadata.json。"""
    settings = get_settings()
    directory = settings.results_dir / task_id
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "task_type": task_type,
        "name": name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    path = directory / "metadata.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Result generated: %s", relative_path(path))
    return path


__all__ = [
    "MAX_LISTED_FILES",
    "build_result",
    "relative_path",
    "resolve_path",
    "sanitize_result",
    "write_metadata",
]
