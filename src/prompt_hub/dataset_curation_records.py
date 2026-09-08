from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import uuid4

from prompt_hub.dataset_curation_support import _now

if TYPE_CHECKING:
    from collections.abc import Mapping

CaptionProfile = Literal["anima", "krea2"]


def _state_item(state: dict[str, Any], relative_path: str) -> dict[str, Any]:
    raw_items = state.setdefault("items", {})
    if not isinstance(raw_items, dict):
        raw_items = {}
        state["items"] = raw_items
    raw_item = raw_items.get(relative_path)
    item = dict(raw_item) if isinstance(raw_item, dict) else {}
    item.setdefault("wd14", {"status": "untagged"})
    item["krea2_vlm"] = _vlm_record(item.get("krea2_vlm"))
    raw_captions = item.setdefault("captions", {})
    captions = raw_captions if isinstance(raw_captions, dict) else {}
    captions.setdefault("anima", _caption_record(None))
    captions.setdefault("krea2", _caption_record(None))
    item["captions"] = captions
    raw_items[relative_path] = item
    return item


class JobProgress(Protocol):
    job_id: str

    def update(self, current: int, total: int, message: str = "") -> None: ...


def _caption_record(value: object) -> dict[str, Any]:
    record = dict(value) if isinstance(value, dict) else {}
    record.setdefault("current", "")
    record.setdefault("status", "empty")
    record.setdefault("source", "")
    record.setdefault("updated_at", "")
    record.setdefault("versions", [])
    return record


def _snapshot_profile(payload: Mapping[str, Any]) -> str:
    explicit = str(payload.get("profile_id", ""))
    if explicit:
        return explicit
    operation = str(payload.get("operation", ""))
    return "krea2" if operation.startswith("edit-krea2") else "anima"


def _completed_by_job(item: object, job_id: str) -> bool:
    if not isinstance(item, dict):
        return False
    wd14 = item.get("wd14", {})
    return bool(
        isinstance(wd14, dict)
        and wd14.get("status") == "completed"
        and wd14.get("job_id") == job_id
    )


def _completed_vlm_by_job(item: object, job_id: str) -> bool:
    if not isinstance(item, dict):
        return False
    vlm = _vlm_record(item.get("krea2_vlm"))
    return bool(vlm["status"] in {"completed", "confirmed"} and vlm["job_id"] == job_id)


def _vlm_record(value: object) -> dict[str, Any]:
    record = dict(value) if isinstance(value, dict) else {}
    record.setdefault("status", "empty")
    record.setdefault("job_id", "")
    record.setdefault("worker_id", "")
    record.setdefault("model", "")
    record.setdefault("draft", "")
    record.setdefault("observations", {})
    record.setdefault("safety_warning", "")
    record.setdefault("source_sha256", "")
    record.setdefault("created_at", "")
    record.setdefault("error", "")
    return record


def _current_caption(item: object, profile_id: CaptionProfile) -> str:
    if not isinstance(item, dict):
        return ""
    captions = item.get("captions", {})
    if not isinstance(captions, dict):
        return ""
    return str(_caption_record(captions.get(profile_id))["current"])


def _set_caption(
    item: dict[str, Any],
    profile_id: CaptionProfile,
    caption: str,
    *,
    status: str,
    source: str,
    snapshot: str,
) -> None:
    raw_captions = item.setdefault("captions", {})
    if not isinstance(raw_captions, dict):
        raw_captions = {}
        item["captions"] = raw_captions
    record = _caption_record(raw_captions.get(profile_id))
    if str(record.get("current", "")) != caption:
        versions = record.get("versions", [])
        if not isinstance(versions, list):
            versions = []
        versions.append(
            {
                "version_id": f"caption-{uuid4().hex[:12]}",
                "caption": caption,
                "status": status,
                "source": source,
                "snapshot": snapshot,
                "created_at": _now(),
            }
        )
        record["versions"] = versions
    record.update(
        {
            "current": caption,
            "status": status if caption else "empty",
            "source": source,
            "updated_at": _now(),
        }
    )
    raw_captions[profile_id] = record
