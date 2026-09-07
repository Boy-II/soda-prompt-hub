from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Query, status

from prompt_hub.tag_completions import TAG_DOWNLOAD_JOB_TYPE, TagCompletionStore

if TYPE_CHECKING:
    from prompt_hub.background_jobs import BackgroundJobRunner


def create_tag_completion_router(
    store: TagCompletionStore,
    job_runner: BackgroundJobRunner,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/tag-completions")
    def get_tag_completions(
        q: Annotated[str, Query(max_length=200)] = "",
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
    ) -> dict[str, Any]:
        return store.query(q=q, limit=limit)

    @router.get("/api/tag-completions/status")
    def get_tag_completion_status() -> dict[str, Any]:
        return store.status()

    @router.post("/api/tag-completions/download", status_code=status.HTTP_202_ACCEPTED)
    def trigger_tag_download() -> dict[str, Any]:
        job = job_runner.submit(TAG_DOWNLOAD_JOB_TYPE, {}, max_attempts=1)
        return {"job": job}

    return router
