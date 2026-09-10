from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from prompt_hub.web_capture import (
    SourceNotFoundError,
    SourceProtectedError,
    WebCaptureError,
    WebCaptureNotFoundError,
    WebCaptureService,
)

if TYPE_CHECKING:
    from prompt_hub.background_jobs import BackgroundJobRunner
    from prompt_hub.source_sync import SourceSyncService


class SourceSyncInput(BaseModel):
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    clone_missing: bool = False


class WebCaptureInput(BaseModel):
    url: str = Field(min_length=8, max_length=4096)
    title: str = Field(default="", max_length=300)
    note: str = Field(default="", max_length=6000)
    safety: str = Field(default="sfw", max_length=40)
    license_name: str = Field(default="unknown", max_length=160)


def create_source_router(
    service: SourceSyncService,
    job_runner: BackgroundJobRunner,
    web_capture: WebCaptureService,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/sources/sync-status")
    def get_source_sync_status() -> list[dict[str, Any]]:
        return service.status()

    @router.get("/api/sources/{source_id}/facets")
    def get_source_facets(source_id: str) -> dict[str, list[str]]:
        source = service.database.get_source(source_id)
        if source is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="资料来源不存在")
        return service.database.source_facets(source_id)

    @router.post("/api/sources/sync", status_code=status.HTTP_202_ACCEPTED)
    def sync_sources(payload: SourceSyncInput) -> dict[str, Any]:
        job = job_runner.submit(
            "source_sync",
            {"source_ids": payload.source_ids, "clone_missing": payload.clone_missing},
            max_attempts=1,
        )
        return {"job": job}

    @router.delete("/api/sources/{source_id}")
    def delete_source(
        source_id: str,
        purge_marks: bool = False,
    ) -> dict[str, Any]:
        preset_ids = service.configured_source_ids()
        try:
            return web_capture.delete_source(
                source_id,
                purge_marks=purge_marks,
                preset_source_ids=preset_ids,
            )
        except SourceProtectedError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(error),
            ) from error
        except SourceNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except WebCaptureError as error:
            raise HTTPException(
                status_code=422,
                detail=str(error),
            ) from error

    @router.get("/api/web-captures")
    def list_web_captures() -> list[dict[str, Any]]:
        return web_capture.list_captures()

    @router.post("/api/web-captures", status_code=status.HTTP_201_CREATED)
    def save_web_capture(payload: WebCaptureInput) -> dict[str, Any]:
        try:
            return web_capture.capture(**payload.model_dump())
        except WebCaptureError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.delete("/api/web-captures/{capture_id}")
    def delete_web_capture(
        capture_id: str,
        purge_marks: bool = False,
    ) -> dict[str, Any]:
        try:
            return web_capture.delete_capture(capture_id, purge_marks=purge_marks)
        except WebCaptureNotFoundError as error:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
        except WebCaptureError as error:
            raise HTTPException(
                status_code=422,
                detail=str(error),
            ) from error

    @router.get("/api/web-captures/{capture_id}/media", response_class=FileResponse)
    def read_web_capture_media(capture_id: str) -> FileResponse:
        try:
            path = web_capture.resolve_media(capture_id)
        except WebCaptureError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return FileResponse(path)

    return router
