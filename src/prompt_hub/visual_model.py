from __future__ import annotations

import hashlib
import json
from http import HTTPStatus
from pathlib import Path
from threading import Lock
from typing import IO, TYPE_CHECKING, Any, override
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener
from uuid import uuid4

from prompt_hub.embedding_index import MAX_EMBEDDING_DIMENSION
from prompt_hub.local_visual import (
    MAX_INPUT_SIZE,
    MIN_INPUT_SIZE,
    MODEL_FILENAME,
    MODEL_ID,
    MODEL_REVISION,
    MODEL_SHA256,
    MODEL_SIZE_BYTES,
    VisualModelDescriptor,
    VisualModelError,
    bundled_visual_model_descriptor,
    write_model_info,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from http.client import HTTPMessage

    from prompt_hub.background_jobs import JobContext, JobHandler

VISUAL_MODEL_FORMAT = "soda-visual-model-v1"
DOWNLOAD_JOB_TYPE = "visual_model_download"
DOWNLOAD_URL = f"https://huggingface.co/{MODEL_ID}/resolve/{MODEL_REVISION}/onnx/{MODEL_FILENAME}"
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_USER_AGENT = "SodaPromptHub/1.0 visual-model-installer"
_ALLOWED_ROOT_HOSTS = ("huggingface.co",)
_ALLOWED_HOST_SUFFIXES = (".hf.co", ".huggingface.co")


class VisualModelConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    def initialize(self) -> None:
        with self._lock:
            if not self.path.exists():
                self._write(
                    {
                        "format": VISUAL_MODEL_FORMAT,
                        "mode": "bundled",
                        "custom": None,
                    }
                )

    def mode(self) -> str:
        with self._lock:
            value = self._read()
        return "custom" if value.get("mode") == "custom" else "bundled"

    def custom(self) -> dict[str, Any] | None:
        with self._lock:
            record = self._read().get("custom")
        if not isinstance(record, dict):
            return None
        return dict(record)

    def enable_custom(self, descriptor: VisualModelDescriptor) -> dict[str, Any]:
        with self._lock:
            value = self._read()
            value["mode"] = "custom"
            value["custom"] = {
                "model_id": descriptor.model_id,
                "model_revision": descriptor.model_revision,
                "dimension": descriptor.dimension,
                "path": str(descriptor.path),
                "input_size": descriptor.input_size,
                "sha256": descriptor.sha256,
            }
            self._write(value)
        return value

    def disable_custom(self) -> dict[str, Any]:
        with self._lock:
            value = self._read()
            if value.get("mode") != "custom":
                raise VisualModelError("自定义视觉模型不存在，当前使用的是官方模型")
            value["mode"] = "bundled"
            value["custom"] = None
            self._write(value)
        return value

    def resolve_descriptor(self, bundled_root: Path) -> VisualModelDescriptor:
        record = self.custom()
        if record is None:
            return bundled_visual_model_descriptor(bundled_root)
        try:
            return _descriptor_from_record(record)
        except (KeyError, TypeError, ValueError) as error:
            raise VisualModelError(f"自定义视觉模型配置无效：{error}") from error

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"format": VISUAL_MODEL_FORMAT, "mode": "bundled", "custom": None}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)


def download_bundled_visual_model(
    model_root: Path,
    payload: Mapping[str, Any],
    context: JobContext,
    *,
    expected_size: int = MODEL_SIZE_BYTES,
    expected_sha256: str = MODEL_SHA256,
    opener: OpenerDirector | None = None,
) -> dict[str, Any]:
    del payload
    model_root.mkdir(parents=True, exist_ok=True)
    target = model_root / MODEL_FILENAME
    part = model_root / f"{MODEL_FILENAME}.part"
    assert_https_download_url(DOWNLOAD_URL)
    handle = opener or build_opener(WhitelistedRedirectHandler())
    offset = part.stat().st_size if part.is_file() else 0
    if offset >= expected_size and part.is_file():
        return _finalize_download(part, target, model_root, expected_size, expected_sha256)
    request = Request(
        DOWNLOAD_URL,
        headers={
            "User-Agent": DOWNLOAD_USER_AGENT,
            "Range": f"bytes={offset}-",
        },
    )
    try:
        with handle.open(request, timeout=DOWNLOAD_TIMEOUT) as response:
            status = getattr(response, "status", HTTPStatus.OK)
            accepted = (
                HTTPStatus.OK,
                HTTPStatus.PARTIAL_CONTENT,
                HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
            )
            if status not in accepted:
                raise VisualModelError(f"模型下载失败：HTTP {status}")
            headers = dict(getattr(response, "headers", {}) or {})
            content_length = headers.get("Content-Length", headers.get("content-length", ""))
            if status == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE:
                return _finalize_download(part, target, model_root, expected_size, expected_sha256)
            if status == HTTPStatus.OK:
                offset = 0
                part.unlink(missing_ok=True)
            mode = "ab" if offset else "wb"
            expected_total = int(content_length) if str(content_length).isdigit() else expected_size
            with part.open(mode) as sink:
                while True:
                    context.raise_if_cancelled()
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    sink.write(chunk)
                    offset += len(chunk)
                    context.update(offset, expected_total, _progress_message(offset, expected_size))
    except HTTPError as error:
        raise VisualModelError(f"模型下载失败：服务返回 HTTP {error.code}") from error
    except (URLError, OSError) as error:
        raise VisualModelError(f"模型下载中断：{error}") from error
    context.update(offset, expected_size, "下载完成，正在校验完整性……")
    return _finalize_download(part, target, model_root, expected_size, expected_sha256)


def make_download_handler(model_root: Path) -> JobHandler:
    def handler(payload: Mapping[str, Any], context: JobContext) -> dict[str, Any]:
        return download_bundled_visual_model(model_root, payload, context)

    return handler


def assert_https_download_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not _host_allowed(parsed.hostname or ""):
        raise VisualModelError(f"下载地址不允许：{parsed.scheme}://{parsed.netloc}")


class WhitelistedRedirectHandler(HTTPRedirectHandler):
    @override
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or not _host_allowed(parsed.hostname or ""):
            raise VisualModelError(f"下载被重定向到不允许的地址：{parsed.scheme}://{parsed.netloc}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _finalize_download(
    part: Path,
    target: Path,
    model_root: Path,
    expected_size: int,
    expected_sha256: str,
) -> dict[str, Any]:
    if not part.is_file():
        raise VisualModelError("模型下载未产生任何文件")
    size = part.stat().st_size
    if size != expected_size:
        part.unlink(missing_ok=True)
        raise VisualModelError(f"模型文件大小不符：下载得到 {size} 字节，期望 {expected_size} 字节")
    digest = _sha256_file(part)
    if digest != expected_sha256:
        part.unlink(missing_ok=True)
        raise VisualModelError("模型文件 SHA-256 校验失败，已清除不完整下载")
    part.replace(target)
    write_model_info(model_root, sha256=expected_sha256)
    return {
        "installed": True,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "size_bytes": size,
        "sha256": digest,
    }


def _descriptor_from_record(record: Mapping[str, Any]) -> VisualModelDescriptor:
    model_id = str(record["model_id"]).strip()
    model_revision = str(record["model_revision"]).strip()
    dimension = int(record["dimension"])
    input_size = int(record["input_size"])
    path = Path(str(record["path"]))
    sha256 = str(record["sha256"]).lower()
    if not model_id or not model_revision or not sha256:
        raise ValueError("model_id / model_revision / sha256 不能为空")
    dimension_valid = 1 <= dimension <= MAX_EMBEDDING_DIMENSION
    size_valid = MIN_INPUT_SIZE <= input_size <= MAX_INPUT_SIZE
    if not dimension_valid or not size_valid:
        raise ValueError("dimension 或 input_size 超出范围")
    return VisualModelDescriptor(
        model_id=model_id,
        model_revision=model_revision,
        dimension=dimension,
        path=path,
        input_size=input_size,
        sha256=sha256,
    )


def _host_allowed(host: str) -> bool:
    lowered = host.lower()
    if lowered in _ALLOWED_ROOT_HOSTS:
        return True
    return any(lowered.endswith(suffix) for suffix in _ALLOWED_HOST_SUFFIXES)


def _progress_message(current: int, total: int) -> str:
    current_mb = current / 1_048_576
    total_mb = total / 1_048_576
    return f"已下载 {current_mb:.1f} MB / {total_mb:.1f} MB"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


MAX_SCAN_DEPTH = 5
MAX_SCAN_RESULTS = 200


def scan_onnx_candidates(models_root: Path) -> list[dict[str, object]]:
    if not models_root.is_dir():
        return []
    results: list[dict[str, object]] = []
    _walk_onnx(models_root, models_root, 0, results)
    results.sort(key=lambda item: str(item["filename"]).casefold())
    return results


def _walk_onnx(
    root: Path,
    current: Path,
    depth: int,
    results: list[dict[str, object]],
) -> None:
    if depth > MAX_SCAN_DEPTH or len(results) >= MAX_SCAN_RESULTS:
        return
    try:
        entries = sorted(current.iterdir(), key=lambda item: item.name.casefold())
    except PermissionError:
        return
    for entry in entries:
        if len(results) >= MAX_SCAN_RESULTS:
            return
        if entry.is_dir() and not entry.name.startswith(".") and not entry.is_symlink():
            _walk_onnx(root, entry, depth + 1, results)
        elif entry.is_file() and entry.suffix.lower() == ".onnx":
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            try:
                relative_dir = str(entry.parent.relative_to(root))
            except ValueError:
                relative_dir = str(entry.parent)
            results.append(
                {
                    "path": str(entry),
                    "filename": entry.name,
                    "size_bytes": size,
                    "directory": relative_dir,
                }
            )
