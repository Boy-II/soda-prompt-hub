from __future__ import annotations

import json
import stat
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import prompt_hub.dataset_workspace as workspace_module
from prompt_hub.api import create_app
from prompt_hub.dataset_workspace import DatasetWorkspaceError, DatasetWorkspaceStore


class _Context:
    def __init__(self, *, cancel_after: int | None = None) -> None:
        self.updates = []
        self.cancel_after = cancel_after

    def update(self, current, total, message="") -> None:
        self.updates.append((current, total, message))

    def raise_if_cancelled(self) -> None:
        if self.cancel_after is not None and len(self.updates) >= self.cancel_after:
            raise workspace_module.JobCancelledError


@pytest.fixture
def store(settings) -> DatasetWorkspaceStore:
    return DatasetWorkspaceStore(settings)


def _make_zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries:
            archive.writestr(name, data)


def _make_symlink_zip(path: Path) -> None:
    info = zipfile.ZipInfo("linked.txt")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, "target.txt")


def _run(store, archive: Path, archive_id: str = "zip-0123456789ab", name: str = "") -> dict:
    payload = {
        "archive_path": str(archive),
        "archive_id": archive_id,
        "filename": "export_20260906_211655.zip",
        "name": name,
    }
    return store.import_archive_job(payload, _Context())


def test_archive_import_flat_zip_registers_workspace(store, tmp_path) -> None:
    archive = tmp_path / "dataset.zip"
    _make_zip(
        archive,
        [
            ("a.jpg", b"jpeg-bytes"),
            ("a.txt", b"a long natural language sentence"),
            ("b.png", b"png-bytes"),
            ("b.txt", b"another caption"),
            ("skip.notes", b"not wanted"),
        ],
    )
    result = _run(store, archive)
    assert result["source_origin"] == "zip_archive"
    assert result["extracted_files"] == 4
    assert result["total_bytes"] > 0
    assert result["skipped"] == [{"name": "skip.notes", "reason": "不支持的文件类型：.notes"}]
    assert result["manifest"] is None
    workspace = result["workspace"]
    assert workspace["source_origin"] == "zip_archive"
    assert workspace["name"] == "export_20260906_211655"
    target = Path(workspace["source_path"])
    assert target.is_relative_to(store.settings.imported_archives_root)
    assert (target / "a.txt").read_text(encoding="utf-8") == "a long natural language sentence"
    assert not archive.exists()
    assert store.find_by_source(target) is not None


def test_archive_import_resumes_skipping_completed_entries(store, tmp_path) -> None:
    first = tmp_path / "first.zip"
    _make_zip(first, [("a.jpg", b"1"), ("a.txt", b"caption")])
    _run(store, first)
    second = tmp_path / "second.zip"
    _make_zip(second, [("a.jpg", b"1"), ("a.txt", b"caption")])
    result = _run(store, second)
    assert result["extracted_files"] == 2
    source = result["workspace"]["source_path"]
    same = store.find_by_source(Path(source))
    assert same is not None
    assert same["workspace_id"] == result["workspace"]["workspace_id"]


def test_archive_import_resume_with_missing_zip_reuses_workspace(store, tmp_path) -> None:
    archive = tmp_path / "gone.zip"
    _make_zip(archive, [("a.jpg", b"1")])
    first = _run(store, archive)
    missing = tmp_path / "missing.zip"
    result = _run(store, missing)
    assert result["resumed"] is True
    assert result["workspace"]["workspace_id"] == first["workspace"]["workspace_id"]
    with pytest.raises(DatasetWorkspaceError, match="已不存在"):
        _run(store, missing, archive_id="zip-ffffffffffff")


def test_archive_rejects_parent_traversal_entries(store, tmp_path) -> None:
    archive = tmp_path / "evil.zip"
    _make_zip(archive, [("ok.jpg", b"1"), ("../evil.txt", b"x")])
    with pytest.raises(DatasetWorkspaceError, match="不安全路径"):
        _run(store, archive)


def test_archive_rejects_absolute_path_entries(store, tmp_path) -> None:
    archive = tmp_path / "evil.zip"
    _make_zip(archive, [("/tmp/evil.txt", b"x")])
    with pytest.raises(DatasetWorkspaceError, match="不安全路径"):
        _run(store, archive)


def test_archive_rejects_symlink_entries(store, tmp_path) -> None:
    archive = tmp_path / "linked.zip"
    _make_symlink_zip(archive)
    with pytest.raises(DatasetWorkspaceError, match="符号链接"):
        _run(store, archive)


def test_archive_rejects_too_many_entries(store, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "ARCHIVE_MAX_ENTRIES", 3)
    archive = tmp_path / "many.zip"
    _make_zip(archive, [(f"{number}.jpg", b"x") for number in range(5)])
    with pytest.raises(DatasetWorkspaceError, match="条目过多"):
        _run(store, archive)


def test_archive_rejects_total_size_overflow(store, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workspace_module, "ARCHIVE_MAX_TOTAL_BYTES", 10)
    archive = tmp_path / "big.zip"
    _make_zip(archive, [("a.jpg", b"1" * 8), ("b.jpg", b"2" * 8)])
    with pytest.raises(DatasetWorkspaceError, match="总大小"):
        _run(store, archive)


def test_archive_rejects_unicode_normalization_collisions(store, tmp_path) -> None:
    archive = tmp_path / "clash.zip"
    _make_zip(archive, [("caf\u00e9.txt", b"a"), ("cafe\u0301.txt", b"b")])
    with pytest.raises(DatasetWorkspaceError, match="归一化"):
        _run(store, archive)


def test_archive_manifest_suggestion_and_tolerance(store, tmp_path) -> None:
    manifest = {
        "source": "tutu_annotation_export",
        "export_type": "natural_language",
        "schema_version": 1,
        "assets": [],
    }
    archive = tmp_path / "with-manifest.zip"
    _make_zip(
        archive,
        [
            ("a.jpg", b"1"),
            ("a.txt", b"caption"),
            ("tutu_prompt_manifest.json", json.dumps(manifest).encode()),
        ],
    )
    result = _run(store, archive)
    assert result["manifest"]["source"] == "tutu_annotation_export"
    assert result["manifest"]["suggested_profile"] == "krea2"

    broken = tmp_path / "broken.zip"
    _make_zip(broken, [("a.jpg", b"1"), ("broken_manifest.json", b"{not json")])
    result = _run(store, broken, archive_id="zip-111111111111")
    assert result["manifest"] is None
    assert result["extracted_files"] == 2


def test_archive_rejects_control_characters_in_names(store, tmp_path) -> None:
    archive = tmp_path / "ctrl.zip"
    _make_zip(archive, [("bad\x01name.txt", b"x")])
    with pytest.raises(DatasetWorkspaceError, match="控制字符"):
        _run(store, archive)


def _wait_for_job(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in {"completed", "failed", "canceled"}:
            return job
        time.sleep(0.02)
    message = f"job {job_id} did not finish"
    raise AssertionError(message)


def test_import_zip_api_roundtrip_with_cleanup(settings, tmp_path) -> None:
    archive = tmp_path / "upload.zip"
    _make_zip(archive, [("a.jpg", b"1"), ("a.txt", b"caption")])
    raw = archive.read_bytes()
    with TestClient(create_app(settings)) as client:
        created = client.post(
            "/api/dataset-workspaces/import-zip",
            params={"filename": "upload.zip", "name": "zip 数据集"},
            content=raw,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert created.status_code == 202
        job_id = created.json()["job"]["job_id"]
        finished = _wait_for_job(client, job_id)
        assert finished["status"] == "completed"
        workspace = finished["result"]["workspace"]
        assert workspace["source_origin"] == "zip_archive"
        assert workspace["name"] == "zip 数据集"
        source = Path(workspace["source_path"])
        assert source.is_dir()

        listed = client.get("/api/dataset-workspaces").json()
        assert any(item["workspace_id"] == workspace["workspace_id"] for item in listed)

        removed = client.delete(f"/api/dataset-workspaces/{workspace['workspace_id']}")
        assert removed.status_code == 200
        assert removed.json()["archive_copy_removed"] is True
        assert removed.json()["source_untouched"] is False
        assert not source.exists()
        assert archive.exists()


def test_import_zip_api_rejects_oversize_and_nonzip(settings, monkeypatch) -> None:
    monkeypatch.setattr("prompt_hub.workspace_routes.MAX_ARCHIVE_UPLOAD_BYTES", 16)
    with TestClient(create_app(settings)) as client:
        oversized = client.post(
            "/api/dataset-workspaces/import-zip",
            params={"filename": "big.zip"},
            content=b"x" * 32,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert oversized.status_code == 413
        assert "上限" in oversized.json()["detail"]
        wrong_name = client.post(
            "/api/dataset-workspaces/import-zip",
            params={"filename": "notes.rar"},
            content=b"x",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert wrong_name.status_code == 422
        empty = client.post(
            "/api/dataset-workspaces/import-zip",
            params={"filename": "empty.zip"},
            content=b"",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert empty.status_code == 422
