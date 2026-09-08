from __future__ import annotations

import os
from pathlib import Path
from threading import Lock
from typing import Any

from prompt_hub.compute_bridge import COMPUTE_PROTOCOL_VERSION, compute_contract
from prompt_hub.release_info import worker_compatibility
from prompt_hub.remote_catalogs import RemoteCatalogMixin
from prompt_hub.remote_nodes_support import (
    BRIDGE_DIRECTORIES,
    COMPUTE_NODE_ROLE,
    DEFAULT_COMPUTE_NODE_LABEL,
    NODE_ROLES,
    PRIMARY_COMPUTE_NODE_ID,
    RESULT_FORMAT,
    SAFE_ID_RE,
    TASK_LOCATION_PRIORITY,
    TASK_LOCATION_STATUS,
    TASK_RECEIPT_KINDS,
    RemoteNodeError,
    _new_task_id,
    _normalize_task_manifest,
    _now,
    _optional_safe_id,
    _present,
    _read_json,
    _reject_task_credentials,
    _safe_civitai_source_url,
    _safe_id,
    _task_summary,
    _verify_result_output,
    _write_json,
)

__all__ = [
    "BRIDGE_DIRECTORIES",
    "DEFAULT_COMPUTE_NODE_LABEL",
    "RemoteNodeError",
    "RemoteNodeStore",
    "_safe_civitai_source_url",
]


class RemoteNodeStore(RemoteCatalogMixin):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.nodes_path = root / "nodes.json"
        self.catalog_root = root / "lora-catalog"
        self.model_catalog_root = root / "model-catalog"
        self.preview_root = root / "lora-previews"
        self.model_preview_root = root / "model-previews"
        self.task_records_root = root / "tasks"
        self._lock = Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.catalog_root.mkdir(parents=True, exist_ok=True)
        self.model_catalog_root.mkdir(parents=True, exist_ok=True)
        self.preview_root.mkdir(parents=True, exist_ok=True)
        self.model_preview_root.mkdir(parents=True, exist_ok=True)
        self.task_records_root.mkdir(parents=True, exist_ok=True)

    def list_nodes(self) -> list[dict[str, Any]]:
        payload = _read_json(self.nodes_path, {"nodes": []})
        nodes = payload.get("nodes", [])
        return nodes if isinstance(nodes, list) else []

    def primary_device_label(self) -> str:
        nodes = self.list_nodes()
        primary = next(
            (item for item in nodes if item.get("node_id") == PRIMARY_COMPUTE_NODE_ID),
            None,
        )
        if primary is None:
            primary = next(
                (item for item in nodes if item.get("role") == COMPUTE_NODE_ROLE),
                None,
            )
        label = str((primary or {}).get("label", "")).strip()
        return label or DEFAULT_COMPUTE_NODE_LABEL

    def save_node(self, node_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        clean_id = _safe_id(node_id, "node_id")
        role = str(payload.get("role", ""))
        if role not in NODE_ROLES:
            raise RemoteNodeError("设备角色无效")
        smb_mount = str(payload.get("smb_mount", "")).strip()
        if smb_mount and not Path(smb_mount).expanduser().is_absolute():
            raise RemoteNodeError("SMB 挂载路径必须是 Mac 上的绝对路径")
        node = {
            "node_id": clean_id,
            "label": str(payload.get("label", "")).strip()[:160] or clean_id,
            "role": role,
            "host": str(payload.get("host", "")).strip()[:255],
            "smb_mount": smb_mount[:4096],
            "enabled": bool(payload.get("enabled", False)),
            "capabilities": sorted(
                {
                    str(value).strip()[:120]
                    for value in payload.get("capabilities", [])
                    if str(value).strip()
                }
            ),
            "notes": str(payload.get("notes", "")).strip()[:1000],
            "updated_at": _now(),
        }
        with self._lock:
            nodes = self.list_nodes()
            existing = next(
                (index for index, item in enumerate(nodes) if item["node_id"] == clean_id), -1
            )
            if existing >= 0:
                nodes[existing] = node
            else:
                nodes.append(node)
            _write_json(self.nodes_path, {"format": "soda-remote-nodes-v1", "nodes": nodes})
        return node

    def diagnostics(self, node_id: str) -> dict[str, Any]:
        clean_id = _safe_id(node_id, "node_id")
        node = next((item for item in self.list_nodes() if item["node_id"] == clean_id), None)
        if node is None:
            raise RemoteNodeError("设备尚未登记")
        mount_value = str(node.get("smb_mount", ""))
        mount = Path(mount_value).expanduser() if mount_value else None
        mount_exists = bool(mount and mount.is_dir())
        bridge_root = mount / "prompt-hub" if mount else None
        prepared = bool(
            bridge_root
            and bridge_root.is_dir()
            and all((bridge_root / name).is_dir() for name in BRIDGE_DIRECTORIES)
        )
        writable = bool(bridge_root and bridge_root.is_dir() and os.access(bridge_root, os.W_OK))
        worker_status = (
            _read_json(bridge_root / "worker-status.json", {})
            if bridge_root and bridge_root.is_dir()
            else {}
        )
        worker_ready = bool(
            worker_status.get("status") == "ready"
            and worker_status.get("protocol_version") == COMPUTE_PROTOCOL_VERSION
            and worker_status.get("role") == node.get("role")
            and worker_status.get("comfyui_reachable") is True
        )
        compatibility = worker_compatibility(worker_status)
        configured = bool(node.get("host") and mount_value)
        if not configured:
            state = "not_configured"
        elif not mount_exists:
            state = "mount_missing"
        elif not prepared:
            state = "mount_ready_bridge_unprepared"
        elif not writable:
            state = "bridge_read_only"
        else:
            state = "ready"
        return {
            "node": node,
            "state": state,
            "configured": configured,
            "mount_exists": mount_exists,
            "bridge_prepared": prepared,
            "bridge_writable": writable,
            "bridge_root": str(bridge_root) if bridge_root else "",
            "worker_ready": worker_ready,
            "worker_status": worker_status,
            "worker_compatibility": compatibility,
            "credentials_stored": False,
        }

    def prepare_bridge(self, node_id: str) -> dict[str, Any]:
        diagnostic = self.diagnostics(node_id)
        if not diagnostic["mount_exists"]:
            raise RemoteNodeError("SMB 挂载目录不存在，不能建立交付桥")
        bridge_root = Path(str(diagnostic["bridge_root"]))
        bridge_root.mkdir(parents=True, exist_ok=True)
        for name in BRIDGE_DIRECTORIES:
            (bridge_root / name).mkdir(exist_ok=True)
        return self.diagnostics(node_id)

    def submit_task(
        self,
        node_id: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        node = self._required_node(node_id)
        if not node.get("enabled"):
            raise RemoteNodeError("设备尚未启用，不能投递任务")
        contract = compute_contract()
        task_type = str(values.get("task_type", ""))
        payload = values.get("payload", {})
        if not isinstance(payload, dict):
            raise RemoteNodeError("任务 payload 必须是对象")
        task_spec = contract["task_types"].get(task_type)
        if not isinstance(task_spec, dict):
            raise RemoteNodeError("任务类型不在 compute contract 中")
        if task_spec.get("target_role") != node.get("role"):
            raise RemoteNodeError("任务类型与设备角色不匹配")
        declared_capabilities = node.get("capabilities", [])
        if not isinstance(declared_capabilities, list):
            declared_capabilities = []
        if task_type not in declared_capabilities:
            diagnostic = self.diagnostics(node_id)
            worker_status = diagnostic.get("worker_status", {})
            runtime_capabilities = (
                worker_status.get("capabilities", [])
                if diagnostic.get("worker_ready") and isinstance(worker_status, dict)
                else []
            )
            if not isinstance(runtime_capabilities, list) or task_type not in runtime_capabilities:
                raise RemoteNodeError("设备与已自检 Worker 均未声明此任务 capability")
        _reject_task_credentials(payload)
        required = task_spec.get("payload_required", [])
        missing = [str(key) for key in required if not _present(payload.get(str(key)))]
        if missing:
            raise RemoteNodeError(f"任务 payload 缺少字段: {', '.join(missing)}")
        clean_manifest = _normalize_task_manifest(values.get("manifest", []))
        clean_project = _optional_safe_id(str(values.get("project_id", "")), "project_id")
        clean_workspace = _optional_safe_id(str(values.get("workspace_id", "")), "workspace_id")
        clean_run = _optional_safe_id(str(values.get("run_id", "")), "run_id")
        clean_retry = _optional_safe_id(str(values.get("retry_of", "")), "retry_of")
        clean_attempt = max(1, min(int(values.get("attempt", 1)), 1000))
        task_id = _new_task_id()
        envelope = {
            "format": "soda-compute-task-v1",
            "protocol_version": COMPUTE_PROTOCOL_VERSION,
            "task_id": task_id,
            "task_type": task_type,
            "target_role": node["role"],
            "created_at": _now(),
            "payload": payload,
            "manifest": clean_manifest,
            "attempt": clean_attempt,
            "priority": max(-100, min(int(values.get("priority", 0)), 100)),
        }
        envelope.update(
            {
                key: value
                for key, value in (
                    ("project_id", clean_project),
                    ("workspace_id", clean_workspace),
                    ("run_id", clean_run),
                    ("retry_of", clean_retry),
                )
                if value
            }
        )
        bridge_root = self._ready_bridge(node_id)
        record_path = self._task_record_path(node_id, task_id)
        with self._lock:
            _write_json(record_path, envelope)
            _write_json(bridge_root / "outbox" / f"{task_id}.json", envelope)
        return _task_summary(envelope, location="outbox", local=envelope)

    def list_tasks(self, node_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        self._required_node(node_id)
        local_records = self._local_task_records(node_id)
        summaries = {
            task_id: _task_summary(record, location="local", local=record)
            for task_id, record in local_records.items()
        }
        diagnostic = self.diagnostics(node_id)
        bridge_root_value = str(diagnostic.get("bridge_root", ""))
        bridge_root = Path(bridge_root_value) if bridge_root_value else None
        if bridge_root and bridge_root.is_dir():
            for location in TASK_LOCATION_STATUS:
                directory = bridge_root / location
                if not directory.is_dir():
                    continue
                for path in directory.glob("*.json"):
                    exchange = _read_json(path, {})
                    task_id = str(exchange.get("task_id", path.stem))
                    if not SAFE_ID_RE.fullmatch(task_id):
                        continue
                    local = local_records.get(task_id, {})
                    summary = _task_summary(exchange, location=location, local=local)
                    current = summaries.get(task_id)
                    if current is None or TASK_LOCATION_PRIORITY[
                        location
                    ] >= TASK_LOCATION_PRIORITY.get(str(current.get("location", "local")), 0):
                        summaries[task_id] = summary
        return sorted(
            summaries.values(),
            key=lambda item: str(item.get("updated_at") or item.get("created_at", "")),
            reverse=True,
        )[: max(1, min(limit, 1000))]

    def get_task(self, node_id: str, task_id: str) -> dict[str, Any]:
        clean_task = _safe_id(task_id, "task_id")
        local = _read_json(self._task_record_path(node_id, clean_task), {})
        exchanges = []
        diagnostic = self.diagnostics(node_id)
        bridge_root_value = str(diagnostic.get("bridge_root", ""))
        bridge_root = Path(bridge_root_value) if bridge_root_value else None
        if bridge_root and bridge_root.is_dir():
            for location in TASK_LOCATION_STATUS:
                path = bridge_root / location / f"{clean_task}.json"
                if path.is_file():
                    exchanges.append({"location": location, "envelope": _read_json(path, {})})
        if not local and not exchanges:
            raise RemoteNodeError("任务不存在")
        if exchanges:
            exchange = max(
                exchanges,
                key=lambda item: TASK_LOCATION_PRIORITY[str(item["location"])],
            )
            location = str(exchange["location"])
            raw_envelope = exchange["envelope"]
            envelope = raw_envelope if isinstance(raw_envelope, dict) else {}
        else:
            location = "local"
            envelope = local
        return {
            "summary": _task_summary(
                envelope,
                location=location,
                local=local,
            ),
            "local_task": local,
            "exchange": exchanges,
        }

    def retry_task(self, node_id: str, task_id: str) -> dict[str, Any]:
        current = self.get_task(node_id, task_id)
        summary = current["summary"]
        if summary["status"] not in {"failed", "canceled"} and summary.get("result_status") not in {
            "failed",
            "canceled",
        }:
            raise RemoteNodeError("只有失败或取消的任务可以重试")
        original = current["local_task"]
        if not original:
            raise RemoteNodeError("缺少 Mac 本地任务事实副本，不能安全重试")
        return self.submit_task(
            node_id,
            {
                "task_type": str(original["task_type"]),
                "payload": dict(original.get("payload", {})),
                "manifest": list(original.get("manifest", [])),
                "project_id": str(original.get("project_id", "")),
                "workspace_id": str(original.get("workspace_id", "")),
                "run_id": str(original.get("run_id", "")),
                "priority": int(original.get("priority", 0)),
                "attempt": int(original.get("attempt", 1)) + 1,
                "retry_of": str(original["task_id"]),
            },
        )

    def verify_returned_task(self, node_id: str, task_id: str) -> dict[str, Any]:
        clean_task = _safe_id(task_id, "task_id")
        bridge_root = self._ready_bridge(node_id)
        result_path = bridge_root / "inbox" / f"{clean_task}.json"
        if not result_path.is_file():
            raise RemoteNodeError("任务尚未进入 inbox，不能验收")
        local = _read_json(self._task_record_path(node_id, clean_task), {})
        if not local:
            raise RemoteNodeError("缺少 Mac 本地任务事实副本，不能验收")
        result = _read_json(result_path, {})
        errors: list[str] = []
        if result.get("format") != RESULT_FORMAT:
            errors.append("result format 不匹配")
        if result.get("protocol_version") != COMPUTE_PROTOCOL_VERSION:
            errors.append("protocol_version 不匹配")
        if result.get("task_id") != clean_task:
            errors.append("task_id 不匹配")
        if result.get("task_type") != local.get("task_type"):
            errors.append("task_type 不匹配")
        if result.get("status") != "completed":
            errors.append("inbox 结果不是 completed")

        expected_sources = {
            str(item.get("relative_path", "")): str(item.get("sha256", ""))
            for item in local.get("manifest", [])
            if isinstance(item, dict)
        }
        returned_sources = result.get("source_hashes", [])
        if not isinstance(returned_sources, list):
            errors.append("source_hashes 不是列表")
            returned_sources = []
        actual_sources = {
            str(item.get("relative_path", "")): str(item.get("sha256", ""))
            for item in returned_sources
            if isinstance(item, dict)
        }
        if actual_sources != expected_sources:
            errors.append("回显源文件哈希与 Mac manifest 不一致")

        checked_outputs = []
        outputs = result.get("outputs", [])
        if not isinstance(outputs, list) or not outputs:
            errors.append("结果没有输出文件")
            outputs = []
        for raw in outputs:
            try:
                checked_outputs.append(_verify_result_output(bridge_root, clean_task, raw))
            except RemoteNodeError as error:
                errors.append(str(error))
        return {
            "task_id": clean_task,
            "verified": not errors,
            "errors": errors,
            "source_count": len(actual_sources),
            "output_count": len(checked_outputs),
            "outputs": checked_outputs,
        }

    def mark_task_received(
        self,
        node_id: str,
        task_id: str,
        *,
        receipt_kind: str,
    ) -> dict[str, Any]:
        clean_task = _safe_id(task_id, "task_id")
        if receipt_kind not in TASK_RECEIPT_KINDS:
            raise RemoteNodeError("任务接收类型无效")
        current = self.get_task(node_id, clean_task)
        summary = current["summary"]
        local = current["local_task"]
        if not local:
            raise RemoteNodeError("缺少 Mac 本地任务事实副本，不能记录接收状态")
        if summary.get("result_status") != "completed":
            raise RemoteNodeError("Windows 任务尚未成功返回，不能记录接收状态")
        received_at = str(local.get("received_at", "")) or _now()
        updated = {
            **local,
            "received_at": received_at,
            "receipt_kind": receipt_kind,
        }
        with self._lock:
            _write_json(self._task_record_path(node_id, clean_task), updated)
        return {
            **summary,
            "status": "dismissed" if receipt_kind == "ignored" else "completed",
            "received_at": received_at,
            "receipt_kind": receipt_kind,
        }

    def cancel_task(self, node_id: str, task_id: str) -> dict[str, Any]:
        clean_task = _safe_id(task_id, "task_id")
        bridge_root = self._ready_bridge(node_id)
        current = self.get_task(node_id, clean_task)
        summary = current["summary"]
        local = current["local_task"]
        if not local:
            raise RemoteNodeError("缺少 Mac 本地任务事实副本，不能取消")
        if summary["status"] == "queued":
            source = bridge_root / "outbox" / f"{clean_task}.json"
            if source.is_file():
                now = _now()
                result = {
                    "format": RESULT_FORMAT,
                    "protocol_version": COMPUTE_PROTOCOL_VERSION,
                    "task_id": clean_task,
                    "task_type": local.get("task_type", ""),
                    "worker_id": "mac-hub",
                    "status": "canceled",
                    "started_at": now,
                    "finished_at": now,
                    "source_hashes": local.get("manifest", []),
                    "outputs": [],
                    "error": "任务在领取前由 Mac 取消",
                }
                with self._lock:
                    source.unlink(missing_ok=True)
                    _write_json(bridge_root / "failed" / f"{clean_task}.json", result)
                return _task_summary(result, location="failed", local=local)
        if summary["status"] == "running":
            _write_json(
                bridge_root / "processing" / f"{clean_task}.cancel",
                {"task_id": clean_task, "requested_at": _now()},
            )
            return {**summary, "cancel_requested": True}
        raise RemoteNodeError("只有等待领取或执行中的任务可以取消")

    def _required_node(self, node_id: str) -> dict[str, Any]:
        clean_id = _safe_id(node_id, "node_id")
        node = next((item for item in self.list_nodes() if item["node_id"] == clean_id), None)
        if node is None:
            raise RemoteNodeError("设备尚未登记")
        return node

    def _ready_bridge(self, node_id: str) -> Path:
        diagnostic = self.diagnostics(node_id)
        if diagnostic["state"] != "ready":
            raise RemoteNodeError("共享目录尚未就绪，不能投递任务")
        return Path(str(diagnostic["bridge_root"]))

    def _task_record_path(self, node_id: str, task_id: str) -> Path:
        clean_node = _safe_id(node_id, "node_id")
        clean_task = _safe_id(task_id, "task_id")
        return self.task_records_root / clean_node / f"{clean_task}.json"

    def _local_task_records(self, node_id: str) -> dict[str, dict[str, Any]]:
        directory = self.task_records_root / _safe_id(node_id, "node_id")
        if not directory.is_dir():
            return {}
        records = {}
        for path in directory.glob("*.json"):
            payload = _read_json(path, {})
            task_id = str(payload.get("task_id", path.stem))
            if SAFE_ID_RE.fullmatch(task_id):
                records[task_id] = payload
        return records
