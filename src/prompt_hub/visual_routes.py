from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from prompt_hub.embedding_index import EmbeddingIndexError, EmbeddingIndexStore
from prompt_hub.local_visual import (
    LocalVisualIndexService,
    VisualIndexError,
    bundled_visual_model_descriptor,
    validate_custom_visual_model,
)
from prompt_hub.visual_model import (
    DOWNLOAD_JOB_TYPE,
    VisualModelConfigStore,
    VisualModelError,
)

if TYPE_CHECKING:
    from prompt_hub.background_jobs import BackgroundJobRunner, BackgroundJobStore


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


def create_visual_router(
    service: LocalVisualIndexService,
    store: EmbeddingIndexStore,
    job_runner: BackgroundJobRunner,
    job_store: BackgroundJobStore,
    config_store: VisualModelConfigStore,
    bundled_model_root: Path,
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

    @router.post("/api/visual-index/model/download", status_code=status.HTTP_202_ACCEPTED)
    def download_visual_model() -> dict[str, Any]:
        job = job_runner.submit(DOWNLOAD_JOB_TYPE, {})
        return {"job": job}

    @router.post("/api/visual-index/model/custom")
    def enable_custom_visual_model(payload: VisualModelCustomInput) -> dict[str, Any]:
        try:
            descriptor = validate_custom_visual_model(
                Path(payload.path).expanduser(),
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


def _latest_download_job(job_store: BackgroundJobStore) -> dict[str, Any] | None:
    for job in job_store.list_jobs(limit=20):
        if job["job_type"] == DOWNLOAD_JOB_TYPE:
            return job
    return None


def _split_types(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}
