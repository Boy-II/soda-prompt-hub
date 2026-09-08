from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from prompt_hub.dataset_curation_records import (
    CaptionProfile,
    JobProgress,
    _caption_record,
    _completed_by_job,
    _completed_vlm_by_job,
    _current_caption,
    _set_caption,
    _state_item,
    _vlm_record,
)
from prompt_hub.dataset_curation_support import (
    _normalize_caption,
    _now,
    _sha256,
    normalize_caption_settings,
    normalize_caption_with_settings,
)
from prompt_hub.dataset_tagging import normalize_tag_draft
from prompt_hub.dataset_workspace import DatasetWorkspaceError
from prompt_hub.local_model import draft_anima_tags, draft_krea2_caption
from prompt_hub.wd14 import ProviderMode, WD14Tagger

if TYPE_CHECKING:
    from threading import RLock

    from prompt_hub.config import Settings
    from prompt_hub.dataset_workspace import DatasetWorkspaceStore
    from prompt_hub.model_connections import ModelConnectionStore

Tagger = Callable[[Path], dict[str, object]]
TaggerFactory = Callable[[float, float, ProviderMode], Tagger]
Krea2Captioner = Callable[[Path, str, str, Mapping[str, Any]], dict[str, Any]]


def _batch_failure_message(label: str, failed: int, reasons: list[str]) -> str:
    """整批失败时把最常见的原因说出来。

    只写「共 N 张失败」的话。使用者得自己去翻每一张的记录才知道为什么。
    而整批失败几乎总是同一个原因——送错模型。服务没开。来源不见了。
    """
    base = f"{label} 队列全部失败, 共 {failed} 张"
    if not reasons:
        return base
    top, count = Counter(reasons).most_common(1)[0]
    return f"{base}。{count} 张的原因是 {top}"


class DatasetCurationJobsMixin:
    settings: Settings
    workspace_store: DatasetWorkspaceStore
    _lock: RLock
    _tagger_factory: TaggerFactory
    _krea2_captioner: Krea2Captioner
    _model_connections: ModelConnectionStore | None

    def read_state(self, workspace_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def _write_state(self, workspace_id: str, state: dict[str, Any]) -> None:
        raise NotImplementedError

    def _require_report(self, workspace_id: str) -> dict[str, Any]:
        raise NotImplementedError

    def _known_record(self, workspace_id: str, relative_path: str) -> dict[str, Any]:
        raise NotImplementedError

    def _write_snapshot(
        self,
        workspace_id: str,
        *,
        operation: str,
        profile_id: CaptionProfile,
        changes: list[dict[str, Any]],
    ) -> str:
        raise NotImplementedError

    def tag_job(self, payload: Mapping[str, Any], context: JobProgress) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id", ""))
        if not workspace_id:
            raise DatasetWorkspaceError("WD14 job is missing workspace_id")
        self.workspace_store.require_source(workspace_id)
        general_threshold = self.settings.wd14_general_threshold
        character_threshold = self.settings.wd14_character_threshold
        caption_settings = normalize_caption_settings("anima", payload)
        provider = str(payload.get("provider", "auto"))
        if provider not in {"auto", "coreml", "cpu"}:
            raise DatasetWorkspaceError("Unsupported WD14 provider")
        tagger_mode = str(payload.get("tagger", "wd14"))
        if tagger_mode not in {"wd14", "model"}:
            raise DatasetWorkspaceError("Unsupported tagger")
        model = str(payload.get("model", "")).strip()
        if tagger_mode == "model" and not model:
            raise DatasetWorkspaceError("使用模型打标时必须选择打标模型")
        paths = self._select_tag_paths(workspace_id, payload)
        job_id = str(getattr(context, "job_id", ""))
        state = self.read_state(workspace_id)
        if job_id:
            paths = [
                path for path in paths if not _completed_by_job(_state_item(state, path), job_id)
            ]
        if not paths:
            return {
                "workspace_id": workspace_id,
                "requested": 0,
                "completed": 0,
                "failed": 0,
                "skipped": 0,
            }
        label = "模型打标" if tagger_mode == "model" else "WD14"
        context.update(0, len(paths), f"正在准备{label}")
        tagger = (
            None
            if tagger_mode == "model"
            else self._tagger_factory(
                general_threshold,
                character_threshold,
                provider,  # type: ignore[arg-type]
            )
        )
        completed = 0
        failed = 0
        skipped = 0
        reasons: list[str] = []
        overwrite = bool(payload.get("overwrite", False))
        for index, relative_path in enumerate(paths, start=1):
            context.update(index - 1, len(paths), f"{label} {index}/{len(paths)} · {relative_path}")
            current = _state_item(state, relative_path)
            wd14 = current.get("wd14", {})
            if (
                not overwrite
                and isinstance(wd14, dict)
                and str(wd14.get("status", "")) == "completed"
            ):
                skipped += 1
                context.update(index, len(paths), f"已跳过 {relative_path}")
                continue
            image_path = self.workspace_store.resolve_source_image(workspace_id, relative_path)
            if image_path is None:
                self._store_tag_failure(
                    workspace_id,
                    state,
                    relative_path,
                    "图片不存在或已变更",
                    job_id=job_id,
                )
                failed += 1
                continue
            try:
                if tagger_mode == "model":
                    result = draft_anima_tags(
                        image_path=image_path,
                        model=model,
                        existing_tags=_current_caption(current, "anima"),
                        mode=caption_settings["mode"],
                        trigger=caption_settings["trigger"],
                        media_tags=caption_settings["media_tags"],
                        options=caption_settings["options"],
                        max_tokens=caption_settings["max_tokens"],
                        connections=self._model_connections,
                    )
                elif tagger is not None:
                    tagger_result = tagger(image_path)
                    result = {
                        **tagger_result,
                        "tag_string": normalize_caption_with_settings(
                            "anima",
                            str(tagger_result.get("tag_string", "")),
                            caption_settings,
                        ),
                        "caption_settings": caption_settings,
                    }
                else:
                    raise DatasetWorkspaceError("WD14 模型尚未准备完成")
            except Exception as error:  # noqa: BLE001
                self._store_tag_failure(
                    workspace_id,
                    state,
                    relative_path,
                    str(error),
                    job_id=job_id,
                    tagger=tagger_mode,
                    model=model,
                )
                reasons.append(str(error))
                failed += 1
            else:
                self._store_tag_result(
                    workspace_id,
                    state,
                    relative_path,
                    result,
                    job_id=job_id,
                    tagger=tagger_mode,
                    model=model,
                )
                completed += 1
            context.update(index, len(paths), f"已处理 {index}/{len(paths)}")
        if paths and completed == 0 and failed:
            raise DatasetWorkspaceError(_batch_failure_message(label, failed, reasons))
        return {
            "workspace_id": workspace_id,
            "requested": len(paths),
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
        }

    def krea2_vlm_job(self, payload: Mapping[str, Any], context: JobProgress) -> dict[str, Any]:
        workspace_id = str(payload.get("workspace_id", ""))
        if not workspace_id:
            raise DatasetWorkspaceError("Krea 2 VLM job is missing workspace_id")
        self.workspace_store.require_source(workspace_id)
        model = str(payload.get("model", "")).strip()
        if not model:
            raise DatasetWorkspaceError("Krea 2 VLM job is missing model")
        caption_settings = normalize_caption_settings("krea2", payload)
        paths = self._select_krea2_paths(workspace_id, payload)
        job_id = str(getattr(context, "job_id", ""))
        state = self.read_state(workspace_id)
        if job_id:
            paths = [
                path
                for path in paths
                if not _completed_vlm_by_job(_state_item(state, path), job_id)
            ]
        if not paths:
            return {
                "workspace_id": workspace_id,
                "model": model,
                "requested": 0,
                "completed": 0,
                "failed": 0,
                "skipped": 0,
            }
        completed = 0
        failed = 0
        skipped = 0
        reasons: list[str] = []
        for index, relative_path in enumerate(paths, start=1):
            context.update(
                index - 1,
                len(paths),
                f"Krea 2 VLM {index}/{len(paths)} · {relative_path}",
            )
            record = self._known_record(workspace_id, relative_path)
            image_path = self.workspace_store.resolve_source_image(workspace_id, relative_path)
            expected_sha256 = str(record.get("sha256", ""))
            if image_path is None or not expected_sha256 or _sha256(image_path) != expected_sha256:
                self._store_krea2_failure(
                    workspace_id,
                    state,
                    relative_path,
                    "图片不存在或扫描后已变更",
                    model=model,
                    job_id=job_id,
                    source_sha256=expected_sha256,
                )
                reasons.append("图片不存在或扫描后已变更")
                failed += 1
                continue
            item = _state_item(state, relative_path)
            existing_caption = _current_caption(item, "krea2")
            try:
                result = self._krea2_captioner(
                    image_path, model, existing_caption, caption_settings
                )
                draft = _normalize_caption("krea2", str(result.get("draft", "")))
                if not draft:
                    raise DatasetWorkspaceError("本地视觉模型返回了空的 Krea 2 草稿")
            except Exception as error:  # noqa: BLE001
                self._store_krea2_failure(
                    workspace_id,
                    state,
                    relative_path,
                    str(error),
                    model=model,
                    job_id=job_id,
                    source_sha256=expected_sha256,
                )
                reasons.append(str(error))
                failed += 1
            else:
                self._store_krea2_result(
                    workspace_id,
                    state,
                    relative_path,
                    result,
                    draft=draft,
                    model=model,
                    job_id=job_id,
                    source_sha256=expected_sha256,
                )
                completed += 1
            context.update(index, len(paths), f"已处理 {index}/{len(paths)}")
        if paths and completed == 0 and failed:
            raise DatasetWorkspaceError(_batch_failure_message("Krea 2 VLM", failed, reasons))
        return {
            "workspace_id": workspace_id,
            "model": model,
            "requested": len(paths),
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
        }

    def update_krea2_draft(
        self,
        workspace_id: str,
        relative_path: str,
        *,
        draft: str,
        confirm: bool,
    ) -> dict[str, Any]:
        self._known_record(workspace_id, relative_path)
        clean = _normalize_caption("krea2", draft)
        if confirm and not clean:
            raise DatasetWorkspaceError("确认前需要一份英文 Krea 2 草稿")
        with self._lock:
            state = self.read_state(workspace_id)
            item = _state_item(state, relative_path)
            vlm = _vlm_record(item.get("krea2_vlm"))
            vlm.update(
                {
                    "status": "confirmed" if confirm else "completed" if clean else "empty",
                    "draft": clean,
                    "edited_at": _now(),
                    "error": "",
                }
            )
            snapshot = ""
            if confirm:
                before = _current_caption(item, "krea2")
                snapshot = self._write_snapshot(
                    workspace_id,
                    operation="confirm-krea2-vlm",
                    profile_id="krea2",
                    changes=[{"relative_path": relative_path, "before": before, "after": clean}],
                )
                _set_caption(
                    item,
                    "krea2",
                    clean,
                    status="reviewed",
                    source="vlm-confirmed",
                    snapshot=snapshot,
                )
                vlm["confirmed_at"] = _now()
                vlm["confirmed_snapshot"] = snapshot
            item["krea2_vlm"] = vlm
            self._write_state(workspace_id, state)
        return {
            "workspace_id": workspace_id,
            "relative_path": relative_path,
            "krea2_vlm": vlm,
            "caption": _caption_record(item["captions"]["krea2"]),
            "snapshot": snapshot,
        }

    def import_krea2_vlm_results(
        self,
        workspace_id: str,
        *,
        model: str,
        worker_id: str,
        task_id: str,
        items: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        report = self._require_report(workspace_id)
        records = {
            str(item.get("relative_path", "")): str(item.get("sha256", ""))
            for item in report.get("images", [])
            if isinstance(item, dict)
        }
        prepared = []
        for raw in items:
            relative_path = str(raw.get("relative_path", ""))
            source_sha256 = str(raw.get("source_sha256", ""))
            expected = records.get(relative_path, "")
            if not expected or source_sha256 != expected:
                raise DatasetWorkspaceError(f"源 SHA-256 回验失败: {relative_path}")
            error = str(raw.get("error", "")).strip()
            draft = "" if error else _normalize_caption("krea2", str(raw.get("caption_draft", "")))
            if not error and not draft:
                raise DatasetWorkspaceError(f"远程 Krea 2 草稿为空: {relative_path}")
            prepared.append((relative_path, source_sha256, draft, error, raw))
        if not prepared:
            raise DatasetWorkspaceError("没有可导入的 Krea 2 VLM 结果")
        state = self.read_state(workspace_id)
        imported = 0
        failed = 0
        for relative_path, source_sha256, draft, error, raw in prepared:
            if error:
                self._store_krea2_failure(
                    workspace_id,
                    state,
                    relative_path,
                    error,
                    model=model,
                    job_id=task_id,
                    source_sha256=source_sha256,
                )
                failed += 1
                continue
            self._store_krea2_result(
                workspace_id,
                state,
                relative_path,
                {
                    "model": model,
                    "observations": raw.get("observations", {}),
                    "safety_warning": raw.get("safety_warning", ""),
                    "worker_id": worker_id,
                },
                draft=draft,
                model=model,
                job_id=task_id,
                source_sha256=source_sha256,
            )
            imported += 1
        return {
            "workspace_id": workspace_id,
            "task_id": task_id,
            "worker_id": worker_id,
            "model": model,
            "imported": imported,
            "failed": failed,
        }

    def _default_tagger_factory(
        self,
        general_threshold: float,
        character_threshold: float,
        provider: ProviderMode,
    ) -> Tagger:
        return WD14Tagger(
            model_root=self.settings.wd14_model_root,
            model_name=self.settings.wd14_model_name,
            general_threshold=general_threshold,
            character_threshold=character_threshold,
            provider=provider,
        ).tag

    @staticmethod
    def _default_krea2_captioner(
        image_path: Path,
        model: str,
        existing_caption: str,
        caption_settings: Mapping[str, Any],
    ) -> dict[str, Any]:
        return draft_krea2_caption(
            image_path=image_path,
            model=model,
            existing_caption=existing_caption,
            mode=caption_settings["mode"],
            trigger=str(caption_settings["trigger"]),
            media_tags=bool(caption_settings["media_tags"]),
            options=dict(caption_settings["options"]),
            max_tokens=int(caption_settings["max_tokens"]),
        )

    def _select_tag_paths(
        self,
        workspace_id: str,
        payload: Mapping[str, Any],
    ) -> list[str]:
        report = self._require_report(workspace_id)
        valid = [
            str(item.get("relative_path", ""))
            for item in report.get("images", [])
            if isinstance(item, dict) and item.get("valid") is True
        ]
        scope = str(payload.get("scope", "untagged"))
        requested = payload.get("paths", [])
        requested_set = (
            {str(path) for path in requested if isinstance(path, str)}
            if isinstance(requested, list)
            else set()
        )
        if scope in {"selected", "filtered"}:
            return [path for path in valid if path in requested_set]
        state = self.read_state(workspace_id)
        if scope == "all":
            return valid
        if scope == "untagged":
            return [
                path
                for path in valid
                if str(_state_item(state, path).get("wd14", {}).get("status", "untagged"))
                != "completed"
            ]
        if scope == "failed":
            return [
                path
                for path in valid
                if str(_state_item(state, path).get("wd14", {}).get("status", "")) == "failed"
            ]
        raise DatasetWorkspaceError("Unsupported WD14 queue scope")

    def _select_krea2_paths(
        self,
        workspace_id: str,
        payload: Mapping[str, Any],
    ) -> list[str]:
        report = self._require_report(workspace_id)
        valid = [
            str(item.get("relative_path", ""))
            for item in report.get("images", [])
            if isinstance(item, dict) and item.get("valid") is True
        ]
        scope = str(payload.get("scope", "missing"))
        requested = payload.get("paths", [])
        requested_set = (
            {str(path) for path in requested if isinstance(path, str)}
            if isinstance(requested, list)
            else set()
        )
        if scope == "selected":
            return [path for path in valid if path in requested_set]
        if scope == "all":
            return valid
        state = self.read_state(workspace_id)
        if scope == "missing":
            return [
                path
                for path in valid
                if not str(_vlm_record(_state_item(state, path).get("krea2_vlm"))["draft"])
            ]
        if scope == "failed":
            return [
                path
                for path in valid
                if _vlm_record(_state_item(state, path).get("krea2_vlm"))["status"] == "failed"
            ]
        raise DatasetWorkspaceError("Unsupported Krea 2 VLM queue scope")

    def _store_krea2_result(
        self,
        workspace_id: str,
        state: dict[str, Any],
        relative_path: str,
        result: Mapping[str, Any],
        *,
        draft: str,
        model: str,
        job_id: str,
        source_sha256: str,
    ) -> None:
        with self._lock:
            latest = self.read_state(workspace_id)
            item = _state_item(latest, relative_path)
            raw_observations = result.get("observations", {})
            observations = raw_observations if isinstance(raw_observations, dict) else {}
            item["krea2_vlm"] = {
                "status": "completed",
                "job_id": job_id,
                "worker_id": str(result.get("worker_id", "")),
                "model": str(result.get("model", model)) or model,
                "draft": draft,
                "observations": observations,
                "safety_warning": str(result.get("safety_warning", ""))[:2000],
                "caption_settings": result.get("caption_settings", {}),
                "source_sha256": source_sha256,
                "created_at": _now(),
                "error": "",
            }
            self._write_state(workspace_id, latest)
            state.clear()
            state.update(latest)

    def _store_krea2_failure(
        self,
        workspace_id: str,
        state: dict[str, Any],
        relative_path: str,
        error: str,
        *,
        model: str,
        job_id: str,
        source_sha256: str,
    ) -> None:
        with self._lock:
            latest = self.read_state(workspace_id)
            item = _state_item(latest, relative_path)
            previous = _vlm_record(item.get("krea2_vlm"))
            previous.update(
                {
                    "status": "failed",
                    "job_id": job_id,
                    "model": model,
                    "source_sha256": source_sha256,
                    "created_at": _now(),
                    "error": error[:2000],
                }
            )
            item["krea2_vlm"] = previous
            self._write_state(workspace_id, latest)
            state.clear()
            state.update(latest)

    def _store_tag_result(
        self,
        workspace_id: str,
        state: dict[str, Any],
        relative_path: str,
        result: Mapping[str, object],
        *,
        job_id: str,
        tagger: str = "wd14",
        model: str = "",
    ) -> None:
        with self._lock:
            latest = self.read_state(workspace_id)
            item = _state_item(latest, relative_path)
            draft = normalize_tag_draft(str(result.get("tag_string", "")))
            item["wd14"] = {
                "status": "completed",
                "job_id": job_id,
                "tagger": tagger,
                "model": str(result.get("model", "SmilingWolf/wd-swinv2-tagger-v3")),
                "provider": str(result.get("provider", "")),
                "tagged_at": _now(),
                "general_threshold": result.get(
                    "general_threshold",
                    self.settings.wd14_general_threshold,
                ),
                "character_threshold": result.get(
                    "character_threshold",
                    self.settings.wd14_character_threshold,
                ),
                "caption_settings": result.get("caption_settings", {}),
                "rating": result.get("rating"),
                "general": result.get("general", []),
                "characters": result.get("characters", []),
                "elapsed_seconds": result.get("elapsed_seconds"),
                "safety_warning": str(result.get("safety_warning", ""))[:2000],
                "error": "",
            }
            if tagger == "model" and model:
                item["wd14"]["model"] = model
            current = _caption_record(item.get("captions", {}).get("anima"))
            if current["status"] != "reviewed":
                _set_caption(
                    item,
                    "anima",
                    draft,
                    status="draft",
                    source=tagger,
                    snapshot="",
                )
            self._write_state(workspace_id, latest)
            state.clear()
            state.update(latest)

    def _store_tag_failure(
        self,
        workspace_id: str,
        state: dict[str, Any],
        relative_path: str,
        error: str,
        *,
        job_id: str,
        tagger: str = "wd14",
        model: str = "",
    ) -> None:
        with self._lock:
            latest = self.read_state(workspace_id)
            item = _state_item(latest, relative_path)
            item["wd14"] = {
                "status": "failed",
                "job_id": job_id,
                "tagger": tagger,
                "model": model,
                "error": error[:2000],
                "tagged_at": _now(),
            }
            self._write_state(workspace_id, latest)
            state.clear()
            state.update(latest)
