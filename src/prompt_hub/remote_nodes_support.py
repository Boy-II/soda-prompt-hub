from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.parse
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

PRIMARY_COMPUTE_NODE_ID = "compute-5060ti"
COMPUTE_NODE_ROLE = "compute_5060ti"
DEFAULT_COMPUTE_NODE_LABEL = "Windows 绘图设备"
NODE_ROLES = {COMPUTE_NODE_ROLE}
BRIDGE_DIRECTORIES = ("outbox", "inbox", "processing", "completed", "failed")
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
CIVITAI_MODEL_PATH_RE = re.compile(r"^/models/(?P<model_id>[1-9]\d*)(?:/[^/?#]+)?/?$")
CIVITAI_HOSTS = {"civitai.com", "www.civitai.com", "civitai.red", "www.civitai.red"}
RESULT_FORMAT = "soda-compute-result-v1"
LORA_PREVIEW_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MODEL_ASSET_TYPES = {
    "checkpoint",
    "controlnet",
    "diffusion_model",
    "text_encoder",
    "upscaler",
    "vae",
}
MAX_LORA_PREVIEW_BYTES = 32 * 1024 * 1024
MAX_LORA_PREVIEW_COUNT = 1024
MAX_LORA_PREVIEW_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_CIVITAI_URL_LENGTH = 2000
TASK_RECEIPT_KINDS = {"comfyui_images", "lora_catalog", "model_catalog"}
TASK_LOCATION_STATUS = {
    "outbox": "queued",
    "processing": "running",
    "inbox": "returned",
    "completed": "completed",
    "failed": "failed",
}
TASK_LOCATION_PRIORITY = {
    "local": 0,
    "outbox": 1,
    "processing": 2,
    "inbox": 3,
    "completed": 4,
    "failed": 5,
}
SENSITIVE_TASK_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "credential",
    "credentials",
    "private_key",
}


class RemoteNodeError(ValueError):
    pass


def _normalize_task_manifest(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RemoteNodeError("任务 manifest 必须是列表")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise RemoteNodeError("任务 manifest 条目无效")
        relative = PurePosixPath(str(item.get("relative_path", "")))
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise RemoteNodeError("任务 manifest relative_path 无效")
        digest = str(item.get("sha256", "")).strip().lower()
        if not SHA256_RE.fullmatch(digest):
            raise RemoteNodeError("任务 manifest SHA-256 无效")
        result.append(
            {
                "relative_path": relative.as_posix(),
                "sha256": digest,
                "size_bytes": max(0, int(item.get("size_bytes", 0))),
            }
        )
    return result


def _verify_result_output(
    bridge_root: Path,
    task_id: str,
    value: object,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RemoteNodeError("结果 output 条目无效")
    relative = PurePosixPath(str(value.get("relative_path", "")))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RemoteNodeError("结果 output relative_path 无效")
    expected_prefix = PurePosixPath("inbox") / task_id
    if relative.parts[: len(expected_prefix.parts)] != expected_prefix.parts:
        raise RemoteNodeError("结果 output 不属于该任务 inbox 目录")
    root_resolved = bridge_root.resolve()
    path = (root_resolved / Path(*relative.parts)).resolve()
    try:
        path.relative_to(root_resolved)
    except ValueError as error:
        raise RemoteNodeError("结果 output 越过共享目录") from error
    if not path.is_file():
        raise RemoteNodeError(f"结果文件不存在: {relative.as_posix()}")
    expected_hash = str(value.get("sha256", "")).strip().lower()
    if not SHA256_RE.fullmatch(expected_hash):
        raise RemoteNodeError(f"结果 SHA-256 无效: {relative.as_posix()}")
    actual_hash = _sha256(path)
    if actual_hash != expected_hash:
        raise RemoteNodeError(f"结果 SHA-256 不匹配: {relative.as_posix()}")
    actual_size = path.stat().st_size
    expected_size = int(value.get("size_bytes", 0) or 0)
    if expected_size != actual_size:
        raise RemoteNodeError(f"结果文件大小不匹配: {relative.as_posix()}")
    kind = str(value.get("kind", ""))
    result = {
        "kind": kind,
        "relative_path": relative.as_posix(),
        "sha256": actual_hash,
        "size_bytes": actual_size,
    }
    if kind in {"lora_preview", "model_preview"}:
        if actual_size > MAX_LORA_PREVIEW_BYTES:
            raise RemoteNodeError(f"预览图超过安全上限: {relative.as_posix()}")
        preview_fields = {
            "preview_index": max(0, min(int(value.get("preview_index", 0)), 9999)),
            "source_relative_path": _safe_relative_value(
                str(value.get("source_relative_path", "")),
                "preview source_relative_path",
            ),
            "media_type": _lora_preview_media_type(path),
        }
        if kind == "model_preview":
            preview_fields["asset_id"] = _safe_id(
                str(value.get("asset_id", "")),
                "asset_id",
            )
        else:
            preview_fields["lora_id"] = _safe_id(
                str(value.get("lora_id", "")),
                "lora_id",
            )
        result.update(preview_fields)
    return result


def _copy_verified_file(source: Path, target: Path, expected_hash: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        if _sha256(target) != expected_hash:
            raise RemoteNodeError("同名预览缓存已存在且内容不同")
        return
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        shutil.copyfile(source, temporary)
        if _sha256(temporary) != expected_hash:
            raise RemoteNodeError("预览图复制后 SHA-256 不匹配")
        temporary.replace(target)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def _lora_preview_media_type(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix not in LORA_PREVIEW_SUFFIXES:
        raise RemoteNodeError("LoRA 预览图扩展名不受支持")
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError as error:
        raise RemoteNodeError("无法读取 LoRA 预览图") from error
    if suffix == ".png" and header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if suffix in {".jpg", ".jpeg"} and header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if suffix == ".webp" and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    if suffix == ".gif" and header[:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    raise RemoteNodeError("LoRA 预览图内容与扩展名不匹配")


def _safe_relative_value(value: str, label: str) -> str:
    relative = PurePosixPath(value.strip().replace("\\", "/"))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RemoteNodeError(f"{label} 无效")
    return relative.as_posix()


def _reject_task_credentials(value: object) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().casefold().replace("-", "_")
            if normalized in SENSITIVE_TASK_KEYS:
                raise RemoteNodeError("任务 payload 不允许包含密码、token 或其他凭据")
            _reject_task_credentials(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_task_credentials(nested)


def _task_summary(
    envelope: dict[str, Any],
    *,
    location: str,
    local: dict[str, Any],
) -> dict[str, Any]:
    merged = {**local, **envelope}
    result_status = str(envelope.get("status", ""))
    status = TASK_LOCATION_STATUS.get(location, "recorded")
    if location == "failed" and result_status == "canceled":
        status = "canceled"
    received_at = str(local.get("received_at", ""))
    if received_at and result_status == "completed":
        status = "completed"
    return {
        "task_id": str(merged.get("task_id", "")),
        "task_type": str(merged.get("task_type", "")),
        "target_role": str(merged.get("target_role", "")),
        "status": status,
        "result_status": result_status,
        "location": location,
        "attempt": int(local.get("attempt", merged.get("attempt", 1)) or 1),
        "retry_of": str(local.get("retry_of", merged.get("retry_of", ""))),
        "project_id": str(local.get("project_id", merged.get("project_id", ""))),
        "workspace_id": str(local.get("workspace_id", merged.get("workspace_id", ""))),
        "run_id": str(local.get("run_id", merged.get("run_id", ""))),
        "worker_id": str(envelope.get("worker_id", "")),
        "created_at": str(local.get("created_at", merged.get("created_at", ""))),
        "started_at": str(envelope.get("started_at", "")),
        "finished_at": str(envelope.get("finished_at", "")),
        "updated_at": str(
            envelope.get("finished_at")
            or envelope.get("started_at")
            or local.get("created_at")
            or merged.get("created_at", "")
        ),
        "received_at": received_at,
        "receipt_kind": str(local.get("receipt_kind", "")),
        "error": str(envelope.get("error", ""))[:2000],
    }


def _present(value: object) -> bool:
    return value is not None and value not in ("", [], {})


def _optional_safe_id(value: str, label: str) -> str:
    return _safe_id(value, label) if value.strip() else ""


def _positive_civitai_id(value: object) -> int | None:
    text = str(value).strip() if isinstance(value, (str, int, float)) else ""
    if not text.isdigit():
        return None
    parsed = int(text)
    return parsed if parsed > 0 else None


def _metadata_civitai_ids(metadata: object) -> tuple[int | None, int | None]:
    if not isinstance(metadata, dict):
        return None, None
    model_id = _positive_civitai_id(metadata.get("civitai_model_id", metadata.get("modelId")))
    version_id = _positive_civitai_id(
        metadata.get("civitai_version_id", metadata.get("modelVersionId"))
    )
    return model_id, version_id


def _safe_civitai_source_url(value: object, metadata: object) -> str:
    model_id, version_id = _metadata_civitai_ids(metadata)
    if isinstance(value, str) and len(value) <= MAX_CIVITAI_URL_LENGTH:
        with suppress(ValueError):
            parsed = urllib.parse.urlsplit(value.strip())
            host = (parsed.hostname or "").casefold()
            matched = CIVITAI_MODEL_PATH_RE.fullmatch(parsed.path)
            if parsed.scheme in {"http", "https"} and host in CIVITAI_HOSTS and matched:
                source_model_id = int(matched.group("model_id"))
                query = urllib.parse.parse_qs(parsed.query)
                source_version_id = _positive_civitai_id((query.get("modelVersionId") or [""])[0])
                chosen_version = source_version_id or version_id
                base = f"https://{host}/models/{source_model_id}"
                return f"{base}?modelVersionId={chosen_version}" if chosen_version else base
    if model_id is None:
        return ""
    base = f"https://civitai.com/models/{model_id}"
    return f"{base}?modelVersionId={version_id}" if version_id else base


def _catalog_source_url(item: object) -> str:
    if not isinstance(item, dict):
        return ""
    return _safe_civitai_source_url(
        item.get("source_url", ""),
        item.get("metadata", {}),
    )


def _new_task_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"task-{stamp}-{uuid4().hex[:12]}"


def _normalize_lora_item(item: dict[str, Any]) -> dict[str, Any]:
    lora_id = _safe_id(str(item.get("lora_id", "")), "lora_id")
    relative = PurePosixPath(str(item.get("relative_path", "")))
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise RemoteNodeError("LoRA relative_path 无效")
    digest = str(item.get("sha256", "")).strip().lower()
    if digest and not SHA256_RE.fullmatch(digest):
        raise RemoteNodeError("LoRA SHA-256 无效")
    preview = str(item.get("preview_relative_path", "")).strip()
    if preview:
        preview_path = PurePosixPath(preview)
        if preview_path.is_absolute() or ".." in preview_path.parts:
            raise RemoteNodeError("LoRA preview_relative_path 无效")
    preview_relative_paths = _string_list(item.get("preview_relative_paths", []), 100)
    for value in preview_relative_paths:
        _safe_relative_value(value, "LoRA preview_relative_paths")
    preview_files = _normalize_lora_preview_files(item.get("preview_files", []))
    metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
    return {
        "lora_id": lora_id,
        "name": str(item.get("name", "")).strip()[:300] or relative.stem,
        "relative_path": relative.as_posix(),
        "sha256": digest,
        "size_bytes": max(0, int(item.get("size_bytes", 0))),
        "base_model": str(item.get("base_model", "")).strip()[:300],
        "model_family": str(item.get("model_family", "")).strip()[:160],
        "source_url": _safe_civitai_source_url(item.get("source_url", ""), metadata),
        "trigger_words": _string_list(item.get("trigger_words", []), 100),
        "tags": _string_list(item.get("tags", []), 300),
        "preview_relative_path": preview,
        "preview_relative_paths": preview_relative_paths,
        "preview_files": preview_files,
        "notes": str(item.get("notes", "")).strip()[:2000],
        "metadata": metadata,
    }


def _normalize_model_item(item: dict[str, Any]) -> dict[str, Any]:
    asset_id = _safe_id(str(item.get("asset_id", "")), "asset_id")
    asset_type = str(item.get("asset_type", "")).strip()
    if asset_type not in MODEL_ASSET_TYPES:
        raise RemoteNodeError("模型 asset_type 无效")
    root_id = _safe_id(str(item.get("root_id", "")), "root_id")
    relative_value = _safe_relative_value(
        str(item.get("relative_path", "")),
        "模型 relative_path",
    )
    relative = PurePosixPath(relative_value)
    preview = str(item.get("preview_relative_path", "")).strip()
    if preview:
        _safe_relative_value(preview, "模型 preview_relative_path")
    preview_relative_paths = _string_list(item.get("preview_relative_paths", []), 100)
    for value in preview_relative_paths:
        _safe_relative_value(value, "模型 preview_relative_paths")
    preview_files = _normalize_lora_preview_files(item.get("preview_files", []))
    metadata = item.get("metadata", {}) if isinstance(item.get("metadata"), dict) else {}
    return {
        "asset_id": asset_id,
        "asset_type": asset_type,
        "root_id": root_id,
        "name": str(item.get("name", "")).strip()[:300] or relative.stem,
        "relative_path": relative.as_posix(),
        "size_bytes": max(0, int(item.get("size_bytes", 0))),
        "modified_at": str(item.get("modified_at", "")).strip()[:80],
        "model_family": str(item.get("model_family", "")).strip()[:160],
        "source_url": _safe_civitai_source_url(item.get("source_url", ""), metadata),
        "preview_relative_path": preview,
        "preview_relative_paths": preview_relative_paths,
        "preview_files": preview_files,
        "metadata": metadata,
    }


def _model_type_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        asset_type = str(item.get("asset_type", ""))
        if asset_type in MODEL_ASSET_TYPES:
            counts[asset_type] = counts.get(asset_type, 0) + 1
    return dict(sorted(counts.items()))


def _normalize_lora_preview_files(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:100]:
        if not isinstance(item, dict):
            continue
        filename = _safe_id(str(item.get("filename", "")), "preview filename")
        digest = str(item.get("sha256", "")).strip().lower()
        if not SHA256_RE.fullmatch(digest):
            raise RemoteNodeError("LoRA preview SHA-256 无效")
        source_relative = str(item.get("source_relative_path", ""))
        result.append(
            {
                "filename": filename,
                "sha256": digest,
                "size_bytes": max(0, int(item.get("size_bytes", 0))),
                "media_type": str(item.get("media_type", ""))[:100],
                "source_relative_path": _safe_relative_value(
                    source_relative,
                    "LoRA preview source_relative_path",
                ),
            }
        )
    return result


def _string_list(value: object, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:300] for item in value[:limit] if str(item).strip()]


def _safe_id(value: str, label: str) -> str:
    clean = value.strip()
    if not SAFE_ID_RE.fullmatch(clean):
        raise RemoteNodeError(f"{label} 无效")
    return clean


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return fallback
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return fallback
    return payload if isinstance(payload, dict) else fallback


def _read_required_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise RemoteNodeError(f"无法读取回传 JSON：{path.name}") from error
    if not isinstance(payload, dict):
        raise RemoteNodeError(f"回传 JSON 必须是对象：{path.name}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{hashlib.sha256(os.urandom(16)).hexdigest()[:8]}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
