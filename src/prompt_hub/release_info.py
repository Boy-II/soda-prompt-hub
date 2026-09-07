from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from prompt_hub import __version__
from prompt_hub.compute_bridge import COMPUTE_PROTOCOL_VERSION

if TYPE_CHECKING:
    from pathlib import Path

    from prompt_hub.config import Settings

PRODUCT_NAME = "Soda Prompt Hub"
WORKER_RELEASE_FORMAT = "soda-windows-worker-release-v1"
COMPONENT_LABELS = {
    "prompt_database": "提示词库",
    "creative_store": "创作项目",
    "background_jobs": "后台任务",
    "danbooru_tags": "标签补全",
    "embedding_index": "视觉索引",
}


def release_channel(version_value: str = __version__) -> str:
    """Return a stable machine-readable release channel from a PEP 440 version."""
    normalized = version_value.casefold()
    if "dev" in normalized:
        return "development"
    if "rc" in normalized:
        return "candidate"
    return "stable"


def system_version_info(settings: Settings) -> dict[str, Any]:
    """Describe code, schema, and bundled Worker versions without mutating user data."""
    main_versions = _schema_versions_readonly(settings.database_path)
    embedding_versions = _schema_versions_readonly(
        settings.embedding_index_root / "embeddings.sqlite"
    )
    combined = {**main_versions, **embedding_versions}
    return {
        "product": {
            "name": PRODUCT_NAME,
            "version": __version__,
            "release_channel": release_channel(),
            "release_channel_label": _release_channel_label(release_channel()),
        },
        "data": {
            "initialized": bool(main_versions),
            "components": main_versions,
            "summary": _schema_summary(main_versions),
        },
        "embedding": {
            "initialized": bool(embedding_versions),
            "components": embedding_versions,
            "summary": _schema_summary(embedding_versions),
        },
        "worker": {
            "bundled_version": __version__,
            "protocol_version": COMPUTE_PROTOCOL_VERSION,
            "release_format": WORKER_RELEASE_FORMAT,
        },
        "all_schema_components": combined,
    }


def worker_compatibility(worker_status: dict[str, Any] | None) -> dict[str, Any]:
    """Classify a Worker status without confusing version drift with protocol failure."""
    status = worker_status if isinstance(worker_status, dict) else {}
    worker_version = str(status.get("worker_version", "")).strip()
    protocol_version = str(status.get("protocol_version", "")).strip()
    base = {
        "worker_version": worker_version,
        "bundled_version": __version__,
        "protocol_version": protocol_version,
        "expected_protocol_version": COMPUTE_PROTOCOL_VERSION,
    }
    if not status:
        return {
            **base,
            "state": "not_checked",
            "compatible": False,
            "message": "Windows Worker 尚未运行自检。",
        }
    if protocol_version != COMPUTE_PROTOCOL_VERSION:
        return {
            **base,
            "state": "incompatible",
            "compatible": False,
            "message": "Worker 通信协议不兼容。请更新 Windows Worker 后再投递任务。",
        }
    if not worker_version:
        return {
            **base,
            "state": "update_recommended",
            "compatible": True,
            "message": "Worker 可以连接。当前版本无法识别。建议更新正式发行包。",
        }
    if _version_key(worker_version) < _version_key(__version__):
        return {
            **base,
            "state": "update_recommended",
            "compatible": True,
            "message": f"Worker {worker_version} 可以连接。建议更新到 {__version__}。",
        }
    return {
        **base,
        "state": "compatible",
        "compatible": True,
        "message": f"Worker {worker_version} 与当前通信协议兼容。",
    }


def _schema_versions_readonly(path: Path) -> dict[str, int]:
    if not path.is_file():
        return {}
    try:
        uri = f"{path.resolve().as_uri()}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            if table is None:
                return {}
            rows = connection.execute(
                """
                SELECT component, MAX(version)
                FROM schema_migrations
                GROUP BY component
                ORDER BY component
                """
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {str(component): int(version_value) for component, version_value in rows}


def _schema_summary(versions: dict[str, int]) -> str:
    if not versions:
        return "尚未初始化"
    return " · ".join(
        f"{COMPONENT_LABELS.get(component, component)} {version_value}"
        for component, version_value in versions.items()
    )


def _release_channel_label(channel: str) -> str:
    return {
        "development": "开发版",
        "candidate": "候选版",
        "stable": "正式版",
    }[channel]


def _version_key(version_value: str) -> tuple[int, int, int]:
    parts = []
    for segment in version_value.split(".")[:3]:
        digits = "".join(character for character in segment if character.isdigit())
        parts.append(int(digits or 0))
    padded = [*parts, 0, 0, 0]
    return padded[0], padded[1], padded[2]
