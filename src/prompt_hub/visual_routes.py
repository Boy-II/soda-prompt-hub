from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from prompt_hub.embedding_index import EmbeddingIndexError, EmbeddingIndexStore
from prompt_hub.local_visual import (
    LocalVisualIndexService,
    VisualIndexError,
    VisualModelError,
    bundled_visual_model_descriptor,
    detect_custom_visual_model,
    validate_custom_visual_model,
)
from prompt_hub.visual_model import (
    DOWNLOAD_JOB_TYPE,
    VisualModelConfigStore,
    scan_onnx_candidates,
)

if TYPE_CHECKING:
    from prompt_hub.background_jobs import BackgroundJobRunner, BackgroundJobStore
    from prompt_hub.config import Settings


class VisualIndexBuildInput(BaseModel):
    asset_types: list[str] = Field(default_factory=list, max_length=20)
    max_items: int = Field(default=0, ge=0, le=10000)


class VisualSourceQueryInput(BaseModel):
    source_sha256: str = Field(min_length=64, max_length=64)
    asset_types: list[str] = Field(default_factory=list, max_length=20)
    safety: str = Field(default="", max_length=40)
    scope_id: str = Field(default="", max_length=200)
    limit: int = Field(default=30, ge=1, le=100)


class VisualModelCustomInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    model_id: str = Field(min_length=1, max_length=300)
    model_revision: str = Field(min_length=1, max_length=200)
    dimension: int = Field(default=512, ge=1, le=8192)
    input_size: int = Field(default=224, ge=16, le=4096)


class VisualModelDetectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)


class VisualModelEnableDetectedInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=4096)
    model_id: str = Field(default="", max_length=300)
    model_revision: str = Field(default="", max_length=200)
    dimension: int = Field(ge=1, le=8192)
    input_size: int = Field(ge=16, le=4096)
    sha256: str = Field(min_length=64, max_length=64)


def create_visual_router(
    service: LocalVisualIndexService,
    store: EmbeddingIndexStore,
    job_runner: BackgroundJobRunner,
    job_store: BackgroundJobStore,
    config_store: VisualModelConfigStore,
    bundled_model_root: Path,
    settings: Settings,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/visual-index/status")
    def visual_index_status() -> dict[str, Any]:
        payload = service.status()
        payload["mode"] = config_store.mode()
        payload["custom"] = config_store.custom()
        payload["download_job"] = _latest_download_job(job_store)
        return payload

    @router.post("/api/visual-index/build", status_code=status.HTTP_202_ACCEPTED)
    def build_visual_index(payload: VisualIndexBuildInput) -> dict[str, Any]:
        if not service.encoder.status()["available"]:
            raise HTTPException(status_code=503, detail=service.encoder.status()["reason"])
        job = job_runner.submit("local_visual_index", payload.model_dump(), max_attempts=2)
        return {"job": job}

    _register_model_routes(
        router,
        service=service,
        job_runner=job_runner,
        config_store=config_store,
        settings=settings,
        bundled_model_root=bundled_model_root,
    )

    @router.post("/api/visual-search/query")
    async def query_uploaded_image(
        request: Request,
        asset_types: str = "",
        safety: str = "",
        scope_id: str = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 30,
    ) -> dict[str, Any]:
        try:
            return service.query_bytes(
                await request.body(),
                asset_types=_split_types(asset_types),
                safety=safety,
                scope_id=scope_id,
                limit=limit,
            )
        except VisualIndexError as error:
            code = 503 if not service.encoder.status()["available"] else 409
            raise HTTPException(status_code=code, detail=str(error)) from error

    @router.post("/api/visual-search/by-source")
    def query_existing_image(payload: VisualSourceQueryInput) -> dict[str, Any]:
        try:
            return service.query_source(
                payload.source_sha256,
                asset_types=set(payload.asset_types),
                safety=payload.safety,
                scope_id=payload.scope_id,
                limit=payload.limit,
            )
        except VisualIndexError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/api/visual-index/{index_id}/clusters")
    def browse_visual_clusters(
        index_id: str,
        asset_types: str = "",
        limit: Annotated[int, Query(ge=1, le=1000)] = 240,
        threshold: Annotated[float, Query(ge=0.5, le=0.99)] = 0.84,
    ) -> dict[str, Any]:
        try:
            return store.clusters(
                index_id,
                asset_types=_split_types(asset_types) or None,
                limit=limit,
                threshold=threshold,
            )
        except EmbeddingIndexError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    return router


def _register_model_routes(
    router: APIRouter,
    *,
    service: LocalVisualIndexService,
    job_runner: BackgroundJobRunner,
    config_store: VisualModelConfigStore,
    settings: Settings,
    bundled_model_root: Path,
) -> None:
    @router.post("/api/visual-index/model/download", status_code=status.HTTP_202_ACCEPTED)
    def download_visual_model() -> dict[str, Any]:
        job = job_runner.submit(DOWNLOAD_JOB_TYPE, {})
        return {"job": job}

    @router.get("/api/visual-index/model/candidates")
    def list_onnx_candidates() -> dict[str, Any]:
        candidates = scan_onnx_candidates(settings.models_root)
        return {"models_root": str(settings.models_root), "candidates": candidates}

    @router.post("/api/visual-index/model/detect")
    def detect_visual_model(payload: VisualModelDetectInput) -> dict[str, Any]:
        try:
            model_path = _resolve_visual_model_path(payload.path, settings.models_root)
            return detect_custom_visual_model(model_path)
        except VisualModelError as error:
            code = 404 if "不存在" in str(error) else 422
            raise HTTPException(status_code=code, detail=str(error)) from error

    @router.post("/api/visual-index/model/enable-detected")
    def enable_detected_visual_model(
        payload: VisualModelEnableDetectedInput,
    ) -> dict[str, Any]:
        try:
            resolved_path = _resolve_visual_model_path(payload.path, settings.models_root)
            model_id = payload.model_id.strip() or resolved_path.stem
            model_revision = payload.model_revision.strip() or payload.sha256[:16]
            descriptor = validate_custom_visual_model(
                resolved_path,
                model_id=model_id,
                model_revision=model_revision,
                dimension=payload.dimension,
                input_size=payload.input_size,
            )
            _ensure_matching_digest(descriptor.sha256, payload.sha256)
            record = config_store.enable_custom(descriptor)
            service.encoder.set_descriptor(descriptor)
        except VisualModelError as error:
            code = 404 if "不存在" in str(error) else 422
            raise HTTPException(status_code=code, detail=str(error)) from error
        return {
            "mode": record["mode"],
            "custom": record["custom"],
            "reindex_required": True,
        }

    @router.post("/api/visual-index/model/custom")
    def enable_custom_visual_model(payload: VisualModelCustomInput) -> dict[str, Any]:
        try:
            model_path = _resolve_visual_model_path(payload.path, settings.models_root)
            descriptor = validate_custom_visual_model(
                model_path,
                model_id=payload.model_id,
                model_revision=payload.model_revision,
                dimension=payload.dimension,
                input_size=payload.input_size,
            )
            record = config_store.enable_custom(descriptor)
            service.encoder.set_descriptor(descriptor)
        except VisualModelError as error:
            code = 404 if "不存在" in str(error) else 422
            raise HTTPException(status_code=code, detail=str(error)) from error
        return {
            "mode": record["mode"],
            "custom": record["custom"],
            "reindex_required": True,
        }

    @router.delete("/api/visual-index/model/custom")
    def disable_custom_visual_model() -> dict[str, Any]:
        try:
            config_store.disable_custom()
            service.encoder.set_descriptor(bundled_visual_model_descriptor(bundled_model_root))
        except VisualModelError as error:
            code = 404 if "不存在" in str(error) else 422
            raise HTTPException(status_code=code, detail=str(error)) from error
        return {"mode": "bundled", "custom": None, "reindex_required": True}


def _resolve_visual_model_path(value: str, models_root: Path) -> Path:
    root = models_root.expanduser().resolve()
    requested = Path(value).expanduser()
    if not requested.is_absolute() or ".." in requested.parts:
        raise VisualModelError(f"模型文件必须使用模型目录内的绝对路径: {root}")
    try:
        requested.relative_to(root)
    except ValueError as error:
        raise VisualModelError(f"模型文件必须位于模型目录内: {root}") from error
    try:
        resolved = requested.resolve(strict=True)
    except FileNotFoundError as error:
        raise VisualModelError(f"模型文件不存在: {requested}") from error
    except OSError as error:
        raise VisualModelError(f"模型文件无法访问: {requested}") from error
    if resolved != requested:
        raise VisualModelError("视觉模型路径不允许使用符号链接")
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise VisualModelError(f"模型文件必须位于模型目录内: {root}") from error
    if not resolved.is_file():
        raise VisualModelError(f"模型文件不存在: {resolved}")
    if resolved.suffix.lower() != ".onnx":
        raise VisualModelError("视觉模型必须是 .onnx 文件")
    return resolved


def _ensure_matching_digest(actual: str, expected: str) -> None:
    if actual != expected.lower():
        raise VisualModelError("模型文件在检测后发生变化。请重新检测")


def _latest_download_job(job_store: BackgroundJobStore) -> dict[str, Any] | None:
    for job in job_store.list_jobs(limit=20):
        if job["job_type"] == DOWNLOAD_JOB_TYPE:
            return job
    return None


def _split_types(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}
