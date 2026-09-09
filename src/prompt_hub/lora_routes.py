from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal, NoReturn

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from prompt_hub.lora_freeze import freeze_lora_project
from prompt_hub.lora_projects import LoraProjectError, LoraProjectStore

if TYPE_CHECKING:
    from prompt_hub.database import PromptDatabase
    from prompt_hub.dataset_curation import DatasetCurationStore
    from prompt_hub.dataset_workspace import DatasetWorkspaceStore


class LoraProjectInput(BaseModel):
    name: str = Field(default="未命名 LoRA 项目", max_length=160)
    concept_type: Literal["character", "outfit", "character_outfit", "style"] = "character"
    trigger_word: str = Field(min_length=3, max_length=64)
    outfit_trigger: str = Field(default="", max_length=64)
    target_families: list[Literal["anima", "krea2"]] | None = Field(
        default=None,
        min_length=1,
        max_length=2,
    )
    features: dict[str, list[str]] = Field(default_factory=dict)
    source_oc_character_id: str = Field(default="", max_length=300)
    dataset_notes: str = Field(default="", max_length=6000)
    target_models: list[str] = Field(default_factory=list, max_length=10)
    training_resolution: int = 1024
    training_node: str = Field(default="Windows 训练设备", max_length=120)
    test_plan: str = Field(default="", max_length=6000)


class LoraProjectUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=160)
    concept_type: Literal["character", "outfit", "character_outfit", "style"] | None = None
    trigger_word: str | None = Field(default=None, min_length=3, max_length=64)
    outfit_trigger: str | None = Field(default=None, max_length=64)
    target_families: list[Literal["anima", "krea2"]] | None = Field(
        default=None,
        min_length=1,
        max_length=2,
    )
    features: dict[str, list[str]] | None = None
    dataset_notes: str | None = Field(default=None, max_length=6000)
    target_models: list[str] | None = Field(default=None, max_length=10)
    training_resolution: int | None = None
    training_node: str | None = Field(default=None, max_length=120)
    test_plan: str | None = Field(default=None, max_length=6000)


class LoraWorkspaceAssetsInput(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=160)
    paths: list[str] = Field(min_length=1, max_length=5000)


class LoraAssetUpdate(BaseModel):
    status: Literal["candidate", "approved", "excluded", "needs_more", "regularization"] | None = (
        None
    )
    coverage: dict[str, list[str]] | None = None
    risk_flags: list[str] | None = Field(default=None, max_length=10)
    note: str | None = Field(default=None, max_length=2000)


def create_lora_router(
    store: LoraProjectStore,
    workspace_store: DatasetWorkspaceStore,
    curation_store: DatasetCurationStore,
    database: PromptDatabase,
) -> APIRouter:
    router = APIRouter()

    @router.get("/api/lora/options")
    def get_lora_options() -> dict[str, Any]:
        return store.options()

    @router.get("/api/lora/projects")
    def list_lora_projects(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, Any]]:
        return store.list_projects()[:limit]

    @router.post("/api/lora/projects", status_code=status.HTTP_201_CREATED)
    def create_lora_project(payload: LoraProjectInput) -> dict[str, Any]:
        values = payload.model_dump(exclude={"source_oc_character_id"})
        values["target_families"] = payload.target_families or ["anima"]
        oc_snapshot = None
        if payload.source_oc_character_id:
            oc_snapshot = database.get_oc_character(payload.source_oc_character_id)
            if oc_snapshot is None:
                raise HTTPException(status_code=404, detail="OC character not found")
            if values["name"] == "未命名 LoRA 项目":
                values["name"] = f"{oc_snapshot['name']} · LoRA"
        try:
            return store.create(values, oc_snapshot=oc_snapshot)
        except LoraProjectError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/lora/projects/{project_id}")
    def get_lora_project(project_id: str) -> dict[str, Any]:
        project = store.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="LoRA project not found")
        return project

    @router.put("/api/lora/projects/{project_id}")
    def update_lora_project(
        project_id: str,
        payload: LoraProjectUpdate,
    ) -> dict[str, Any]:
        try:
            return store.update(project_id, payload.model_dump(exclude_unset=True))
        except LoraProjectError as error:
            _raise_lora_http(error)

    @router.post("/api/lora/projects/{project_id}/assets", status_code=status.HTTP_201_CREATED)
    def add_lora_project_assets(
        project_id: str,
        payload: LoraWorkspaceAssetsInput,
    ) -> dict[str, Any]:
        if store.get(project_id) is None:
            raise HTTPException(status_code=404, detail="LoRA project not found")
        report = workspace_store.read_current_report(payload.workspace_id)
        if report is None:
            raise HTTPException(status_code=404, detail="Dataset workspace report not found")
        requested = set(payload.paths)
        records = [
            item
            for item in report.get("images", [])
            if isinstance(item, dict)
            and item.get("valid") is True
            and str(item.get("relative_path", "")) in requested
        ]
        found = {str(item["relative_path"]) for item in records}
        if missing := sorted(requested - found):
            raise HTTPException(
                status_code=422,
                detail=f"数据集图片不存在或不可用：{', '.join(missing[:5])}",
            )
        try:
            return store.add_assets(project_id, payload.workspace_id, records)
        except LoraProjectError as error:
            _raise_lora_http(error)

    @router.put("/api/lora/projects/{project_id}/assets/{asset_id}")
    def update_lora_project_asset(
        project_id: str,
        asset_id: str,
        payload: LoraAssetUpdate,
    ) -> dict[str, Any]:
        try:
            return store.update_asset(project_id, asset_id, payload.model_dump(exclude_unset=True))
        except LoraProjectError as error:
            _raise_lora_http(error)

    @router.post("/api/lora/projects/{project_id}/coverage/preview")
    def preview_lora_project_coverage(project_id: str) -> dict[str, Any]:
        project = store.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="LoRA project not found")
        records = _coverage_records(project, workspace_store, curation_store)
        try:
            return store.preview_coverage(project_id, records)
        except LoraProjectError as error:
            _raise_lora_http(error)

    @router.post("/api/lora/projects/{project_id}/coverage/apply")
    def apply_lora_project_coverage(project_id: str) -> dict[str, Any]:
        project = store.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="LoRA project not found")
        records = _coverage_records(project, workspace_store, curation_store)
        try:
            return store.apply_coverage_review(project_id, records)
        except LoraProjectError as error:
            _raise_lora_http(error)

    @router.get("/api/lora/projects/{project_id}/readiness")
    def get_lora_project_readiness(project_id: str) -> dict[str, Any]:
        project = store.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="LoRA project not found")
        records = _coverage_records(project, workspace_store, curation_store)
        return _delivery_readiness(project, records)

    @router.post("/api/lora/projects/{project_id}/freeze", status_code=status.HTTP_201_CREATED)
    def freeze_project(project_id: str) -> dict[str, Any]:
        project = store.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="LoRA project not found")
        reports: dict[str, dict[str, Any]] = {}
        prepared = []
        for asset in project["assets"]:
            if asset["status"] not in {"approved", "regularization"}:
                continue
            workspace_id = str(asset["workspace_id"])
            if workspace_id not in reports:
                report = workspace_store.read_current_report(workspace_id)
                if report is None:
                    raise HTTPException(
                        status_code=422,
                        detail="Dataset workspace report not found",
                    )
                reports[workspace_id] = curation_store.decorate_report(workspace_id, report)
            record = next(
                (
                    item
                    for item in reports[workspace_id]["images"]
                    if item["relative_path"] == asset["relative_path"]
                ),
                None,
            )
            source = workspace_store.resolve_source_image(workspace_id, asset["relative_path"])
            if record is None or source is None:
                raise HTTPException(status_code=422, detail="LoRA source image not found")
            prepared.append(
                {
                    **asset,
                    "source_path": str(source),
                    "captions": record["curation"]["captions"],
                }
            )
        try:
            export = freeze_lora_project(project, prepared)
            updated = store.register_export(project_id, export)
        except LoraProjectError as error:
            _raise_lora_http(error)
        return {"project": updated, "export": export}

    @router.get(
        "/api/lora/projects/{project_id}/exports/{filename}",
        response_class=FileResponse,
        include_in_schema=False,
    )
    def download_lora_export(project_id: str, filename: str) -> FileResponse:
        path = store.resolve_export(project_id, filename)
        if path is None:
            raise HTTPException(status_code=404, detail="LoRA export not found")
        return FileResponse(path, filename=filename)

    return router


def _coverage_records(
    project: dict[str, Any],
    workspace_store: DatasetWorkspaceStore,
    curation_store: DatasetCurationStore,
) -> dict[str, dict[str, Any]]:
    reports: dict[str, dict[str, Any]] = {}
    records: dict[str, dict[str, Any]] = {}
    for asset in project.get("assets", []):
        workspace_id = str(asset.get("workspace_id", ""))
        if workspace_id not in reports:
            report = workspace_store.read_current_report(workspace_id)
            if report is None:
                reports[workspace_id] = {"images": []}
            else:
                reports[workspace_id] = curation_store.decorate_report(workspace_id, report)
        relative_path = str(asset.get("relative_path", ""))
        record = next(
            (
                item
                for item in reports[workspace_id].get("images", [])
                if isinstance(item, dict) and str(item.get("relative_path", "")) == relative_path
            ),
            None,
        )
        if record is not None:
            records[str(asset.get("asset_id", ""))] = record
    return records


def _delivery_readiness(
    project: dict[str, Any],
    records: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    assets = [
        asset
        for asset in project.get("assets", [])
        if asset.get("status") in {"approved", "regularization"}
    ]
    trigger = str(project.get("trigger_word", ""))
    hashes = [str(asset.get("sha256", "")) for asset in assets]
    duplicate_count = len(hashes) - len(set(hashes))
    family_results: dict[str, dict[str, Any]] = {}
    for family in project.get("target_families", []):
        counts = {
            "complete": 0,
            "missing": 0,
            "pending_review": 0,
            "non_english": 0,
            "missing_trigger": 0,
        }
        issue_workspaces: list[str] = []
        for asset in assets:
            record = records.get(str(asset.get("asset_id", "")), {})
            caption = (
                record.get("curation", {}).get("captions", {}).get(family, {})
                if isinstance(record, dict)
                else {}
            )
            text = str(caption.get("current", "")).strip()
            if not text:
                issue = "missing"
            elif caption.get("status") != "reviewed":
                issue = "pending_review"
            elif not text.isascii():
                issue = "non_english"
            elif trigger not in text:
                issue = "missing_trigger"
            else:
                issue = "complete"
            counts[issue] += 1
            if issue != "complete":
                workspace_id = str(asset.get("workspace_id", ""))
                if workspace_id and workspace_id not in issue_workspaces:
                    issue_workspaces.append(workspace_id)
        total = len(assets)
        family_results[str(family)] = {
            "total": total,
            **counts,
            "ready": total > 0 and counts["complete"] == total,
            "workspace_ids": issue_workspaces,
        }
    ready = (
        bool(assets)
        and duplicate_count == 0
        and all(item["ready"] for item in family_results.values())
    )
    return {
        "project_id": project.get("project_id"),
        "eligible_count": len(assets),
        "duplicate_count": duplicate_count,
        "families": family_results,
        "ready": ready,
    }


def _raise_lora_http(error: LoraProjectError) -> NoReturn:
    code = 404 if "not found" in str(error).lower() else 422
    raise HTTPException(status_code=code, detail=str(error)) from error
