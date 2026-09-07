from __future__ import annotations

import hashlib
import json
import time
from http.client import HTTPMessage
from urllib.request import Request

import numpy as np
import pytest
from fastapi.testclient import TestClient

from prompt_hub.api import create_app
from prompt_hub.background_jobs import JobCancelledError
from prompt_hub.local_visual import (
    MODEL_FILENAME,
    VisualModelDescriptor,
    VisualModelError,
    detect_custom_visual_model,
    validate_custom_visual_model,
)
from prompt_hub.visual_model import (
    DOWNLOAD_JOB_TYPE,
    DOWNLOAD_URL,
    VisualModelConfigStore,
    WhitelistedRedirectHandler,
    assert_https_download_url,
    download_bundled_visual_model,
    scan_onnx_candidates,
)


class _FakeResponse:
    def __init__(self, payload: bytes, *, status: int = 200, chunk: int = 10) -> None:
        self.status = status
        self._payload = payload
        self._chunk = chunk
        self._pos = 0

    @property
    def headers(self):
        return {"Content-Length": str(len(self._payload))}

    def read(self, n: int = -1):
        if self._pos >= len(self._payload):
            return b""
        limit = min(n, self._chunk) if n and n > 0 else self._chunk
        chunk = self._payload[self._pos : self._pos + limit]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class _FakeOpener:
    def __init__(self, payload: bytes, *, chunk: int = 10) -> None:
        self.payload = payload
        self.chunk = chunk
        self.requests: list[Request] = []

    def open(self, request: Request, timeout: float | None = None):
        del timeout
        self.requests.append(request)
        range_header = request.get_header("Range") or ""
        offset = 0
        if range_header.startswith("bytes=") and range_header.endswith("-"):
            start = range_header[6:-1]
            if start.isdigit():
                offset = int(start)
        if offset >= len(self.payload):
            return _FakeResponse(b"", status=416)
        if offset:
            return _FakeResponse(self.payload[offset:], status=206, chunk=self.chunk)
        return _FakeResponse(self.payload, status=200, chunk=self.chunk)


class _Context:
    def __init__(self, *, cancel_after_bytes: int | None = None) -> None:
        self.updates = []
        self.cancel_after_bytes = cancel_after_bytes
        self.cancel_requested = False

    def update(self, current, total, message="") -> None:
        self.updates.append((current, total, message))
        self.raise_if_cancelled()

    def raise_if_cancelled(self) -> None:
        if self.cancel_after_bytes is not None and (
            self.updates and self.updates[-1][0] >= self.cancel_after_bytes
        ):
            self.cancel_requested = True
        if self.cancel_requested:
            raise JobCancelledError


class _Input:
    name = "pixel_values"


class _Session:
    def __init__(self, outputs: list[np.ndarray]) -> None:
        self.outputs = outputs
        self.tensor = None

    def get_inputs(self):
        return [_Input()]

    def run(self, _names, values):
        self.tensor = values["pixel_values"]
        return self.outputs


def _payload(seed: bytes = b"tiny-model-bytes") -> bytes:
    return seed * 3


def test_download_bundled_model_writes_validated_model(tmp_path) -> None:
    model_root = tmp_path / "model"
    raw = _payload()
    digest = hashlib.sha256(raw).hexdigest()
    context = _Context()
    result = download_bundled_visual_model(
        model_root,
        {},
        context,
        expected_size=len(raw),
        expected_sha256=digest,
        opener=_FakeOpener(raw),
    )
    assert result["installed"] is True
    assert result["size_bytes"] == len(raw)
    assert result["sha256"] == digest
    assert (model_root / MODEL_FILENAME).read_bytes() == raw
    assert not (model_root / f"{MODEL_FILENAME}.part").exists()
    info = json.loads((model_root / "model-info.json").read_text(encoding="utf-8"))
    assert info["sha256"] == digest
    assert context.updates
    assert context.updates[-1][0] == len(raw)


def test_download_rejects_sha256_mismatch_and_keeps_no_files(tmp_path) -> None:
    model_root = tmp_path / "model"
    raw = _payload()
    with pytest.raises(VisualModelError, match="SHA-256"):
        download_bundled_visual_model(
            model_root,
            {},
            _Context(),
            expected_size=len(raw),
            expected_sha256="0" * 64,
            opener=_FakeOpener(raw),
        )
    assert not (model_root / MODEL_FILENAME).exists()
    assert not (model_root / f"{MODEL_FILENAME}.part").exists()
    assert not (model_root / "model-info.json").exists()


def test_download_rejects_size_mismatch_and_keeps_no_files(tmp_path) -> None:
    model_root = tmp_path / "model"
    raw = _payload()
    digest = hashlib.sha256(raw).hexdigest()
    with pytest.raises(VisualModelError, match="大小不符"):
        download_bundled_visual_model(
            model_root,
            {},
            _Context(),
            expected_size=len(raw) + 1,
            expected_sha256=digest,
            opener=_FakeOpener(raw),
        )
    assert not (model_root / MODEL_FILENAME).exists()
    assert not (model_root / f"{MODEL_FILENAME}.part").exists()
    assert not (model_root / "model-info.json").exists()


def test_download_cancel_keeps_partial_file_and_retry_resumes(tmp_path) -> None:
    model_root = tmp_path / "model"
    raw = _payload(b"resumable") * 2
    digest = hashlib.sha256(raw).hexdigest()
    interrupted = _Context(cancel_after_bytes=10)
    with pytest.raises(JobCancelledError):
        download_bundled_visual_model(
            model_root,
            {},
            interrupted,
            expected_size=len(raw),
            expected_sha256=digest,
            opener=_FakeOpener(raw, chunk=10),
        )
    part = model_root / f"{MODEL_FILENAME}.part"
    assert part.is_file()
    saved = part.stat().st_size
    assert 0 < saved < len(raw)

    resuming = _FakeOpener(raw, chunk=10)
    result = download_bundled_visual_model(
        model_root,
        {},
        _Context(),
        expected_size=len(raw),
        expected_sha256=digest,
        opener=resuming,
    )
    assert result["installed"] is True
    assert resuming.requests[-1].get_header("Range") == f"bytes={saved}-"
    assert (model_root / MODEL_FILENAME).read_bytes() == raw
    assert not part.exists()


def test_download_url_must_be_https_within_host_whitelist() -> None:
    assert_https_download_url(DOWNLOAD_URL)
    assert_https_download_url("https://cdn-lfs.hf.co/xenova/clip-vit-base-patch32/file.onnx")
    assert_https_download_url("https://sub.huggingface.co/file.onnx")
    for bad in (
        "http://huggingface.co/file.onnx",
        "https://evil.example/file.onnx",
        "https://hf.co.evil.example/file.onnx",
        "https://huggingface.co.evil.example/file.onnx",
        "https://not-the-hub-hf.co/file.onnx",
    ):
        with pytest.raises(VisualModelError, match="不允许"):
            assert_https_download_url(bad)


def test_download_redirects_to_unapproved_hosts_are_rejected() -> None:
    handler = WhitelistedRedirectHandler()
    request = Request(DOWNLOAD_URL)
    for bad in ("https://evil.example/file.onnx", "http://cdn-lfs.hf.co/file.onnx"):
        with pytest.raises(VisualModelError, match="不允许"):
            handler.redirect_request(request, None, 302, "Found", HTTPMessage(), bad)
    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        HTTPMessage(),
        "https://cdn-lfs.hf.co/xenova/clip-vit-base-patch32/file.onnx",
    )
    assert redirected is not None
    assert redirected.full_url == "https://cdn-lfs.hf.co/xenova/clip-vit-base-patch32/file.onnx"


def test_custom_model_rejected_when_runtime_dimension_differs(tmp_path, monkeypatch) -> None:
    model_path = tmp_path / "custom.onnx"
    model_path.write_bytes(b"fake-onnx-bytes")
    session = _Session([np.asarray([[1.0] * 384], dtype=np.float32)])
    monkeypatch.setattr("prompt_hub.local_visual._create_session", lambda _path: session)
    with pytest.raises(VisualModelError, match="维投影"):
        validate_custom_visual_model(
            model_path,
            model_id="custom-clip",
            model_revision="v1",
            dimension=512,
            input_size=224,
        )


def test_custom_model_rejected_when_session_load_fails(tmp_path, monkeypatch) -> None:
    model_path = tmp_path / "broken.onnx"
    model_path.write_bytes(b"broken")

    def fail(_path):
        message = "corrupted protobuf"
        raise RuntimeError(message)

    monkeypatch.setattr("prompt_hub.local_visual._create_session", fail)
    with pytest.raises(VisualModelError, match="无法载入"):
        validate_custom_visual_model(
            model_path,
            model_id="custom-clip",
            model_revision="v1",
            dimension=512,
            input_size=224,
        )


def test_custom_model_missing_file_is_rejected(tmp_path) -> None:
    with pytest.raises(VisualModelError, match="不存在"):
        validate_custom_visual_model(
            tmp_path / "missing.onnx",
            model_id="custom-clip",
            model_revision="v1",
            dimension=512,
            input_size=224,
        )


def test_custom_model_accepted_when_runtime_output_matches(tmp_path, monkeypatch) -> None:
    model_path = tmp_path / "custom.onnx"
    model_path.write_bytes(b"fake-onnx-bytes")
    session = _Session([np.asarray([[3.0, 4.0] + [0.0] * 382], dtype=np.float32)])
    monkeypatch.setattr("prompt_hub.local_visual._create_session", lambda _path: session)
    descriptor = validate_custom_visual_model(
        model_path,
        model_id="custom-clip",
        model_revision="v1",
        dimension=384,
        input_size=224,
    )
    assert descriptor.model_id == "custom-clip"
    assert descriptor.dimension == 384
    assert descriptor.path == model_path
    assert descriptor.sha256 == hashlib.sha256(model_path.read_bytes()).hexdigest()
    assert descriptor.bundled is False
    assert session.tensor.shape == (1, 3, 224, 224)


def test_config_store_custom_roundtrip_and_corrupt_fallback(tmp_path) -> None:
    path = tmp_path / "visual-model.json"
    store = VisualModelConfigStore(path)
    store.initialize()
    assert store.mode() == "bundled"
    assert store.custom() is None

    descriptor = VisualModelDescriptor(
        model_id="my-clip",
        model_revision="v2",
        dimension=384,
        path=tmp_path / "m.onnx",
        input_size=224,
        sha256="a" * 64,
    )
    record = store.enable_custom(descriptor)
    assert record["mode"] == "custom"
    assert record["custom"]["model_id"] == "my-clip"
    assert record["custom"]["dimension"] == 384
    assert record["custom"]["path"] == str(tmp_path / "m.onnx")
    assert record["custom"]["sha256"] == "a" * 64
    assert record["format"] == "soda-visual-model-v1"

    reopened = VisualModelConfigStore(path)
    assert reopened.mode() == "custom"
    assert reopened.resolve_descriptor(tmp_path / "bundled").model_id == "my-clip"
    reopened.disable_custom()
    assert reopened.mode() == "bundled"
    assert reopened.resolve_descriptor(tmp_path / "bundled").bundled is True
    with pytest.raises(VisualModelError, match="不存在"):
        reopened.disable_custom()

    path.write_text(
        '{"format": "soda-visual-model-v1", "mode": "custom", "custom": {"dimension": 0}}',
        encoding="utf-8",
    )
    with pytest.raises(VisualModelError, match="配置无效"):
        reopened.resolve_descriptor(tmp_path / "bundled")
    path.write_text("not json at all", encoding="utf-8")
    assert reopened.resolve_descriptor(tmp_path / "bundled").bundled is True


def test_visual_model_custom_api_roundtrip(settings, monkeypatch) -> None:
    settings.models_root.mkdir(parents=True, exist_ok=True)
    model_path = settings.models_root / "custom.onnx"
    model_path.write_bytes(b"fake-onnx-bytes")
    session = _Session([np.asarray([[1.0] * 512], dtype=np.float32)])
    monkeypatch.setattr("prompt_hub.local_visual._create_session", lambda _path: session)
    with TestClient(create_app(settings)) as client:
        initial = client.get("/api/visual-index/status").json()
        assert initial["mode"] == "bundled"
        assert initial["custom"] is None
        assert initial["download_job"] is None

        enabled = client.post(
            "/api/visual-index/model/custom",
            json={
                "path": str(model_path),
                "model_id": "my-clip",
                "model_revision": "v3",
                "dimension": 512,
                "input_size": 224,
            },
        )
        assert enabled.status_code == 200
        assert enabled.json()["reindex_required"] is True
        status = client.get("/api/visual-index/status").json()
        assert status["mode"] == "custom"
        assert status["model"]["model_id"] == "my-clip"
        assert status["model"]["dimension"] == 512

        missing = client.post(
            "/api/visual-index/model/custom",
            json={
                "path": str(settings.models_root / "missing.onnx"),
                "model_id": "x",
                "model_revision": "v1",
                "dimension": 512,
                "input_size": 224,
            },
        )
        assert missing.status_code == 404

        deleted = client.delete("/api/visual-index/model/custom")
        assert deleted.status_code == 200
        assert client.get("/api/visual-index/status").json()["mode"] == "bundled"
        again = client.delete("/api/visual-index/model/custom")
        assert again.status_code == 404


def test_visual_model_custom_api_rejects_dimension_mismatch(settings, monkeypatch) -> None:
    settings.models_root.mkdir(parents=True, exist_ok=True)
    model_path = settings.models_root / "custom.onnx"
    model_path.write_bytes(b"fake-onnx-bytes")
    session = _Session([np.asarray([[1.0] * 384], dtype=np.float32)])
    monkeypatch.setattr("prompt_hub.local_visual._create_session", lambda _path: session)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/visual-index/model/custom",
            json={
                "path": str(model_path),
                "model_id": "my-clip",
                "model_revision": "v1",
                "dimension": 512,
                "input_size": 224,
            },
        )
        assert response.status_code == 422
        assert "维投影" in response.json()["detail"]
        status = client.get("/api/visual-index/status").json()
        assert status["mode"] == "bundled"
        assert status["model"]["available"] is False


def test_visual_model_download_api_fails_cleanly_without_network(settings, monkeypatch) -> None:
    raw = b"tiny"
    monkeypatch.setattr(
        "prompt_hub.visual_model.build_opener",
        lambda *_args, **_kwargs: _FakeOpener(raw),
    )
    bundled_root = settings.models_root / "clip" / "clip-vit-base-patch32"
    with TestClient(create_app(settings)) as client:
        created = client.post("/api/visual-index/model/download")
        assert created.status_code == 202
        job = created.json()["job"]
        assert job["job_type"] == DOWNLOAD_JOB_TYPE

        deadline = time.monotonic() + 3
        final = None
        while time.monotonic() < deadline:
            final = client.get(f"/api/jobs/{job['job_id']}").json()
            if final["status"] in {"failed", "canceled", "completed"}:
                break
            time.sleep(0.01)
        assert final is not None
        assert final["status"] == "failed"
        assert "大小不符" in final["error"]

        status = client.get("/api/visual-index/status").json()
        assert status["download_job"]["job_id"] == job["job_id"]
        assert not (bundled_root / MODEL_FILENAME).exists()
        assert not (bundled_root / f"{MODEL_FILENAME}.part").exists()
        assert not (bundled_root / "model-info.json").exists()


def test_scan_onnx_candidates_returns_only_onnx_files(tmp_path) -> None:
    models = tmp_path / "models"
    sub = models / "clip"
    sub.mkdir(parents=True)
    (sub / "model.onnx").write_bytes(b"onnx")
    (sub / "model.bin").write_bytes(b"bin")
    (models / "top.ONNX").write_bytes(b"onnx2")  # case-insensitive
    hidden = models / ".hidden"
    hidden.mkdir()
    (hidden / "secret.onnx").write_bytes(b"skip")

    results = scan_onnx_candidates(models)
    filenames = [item["filename"] for item in results]
    assert "model.onnx" in filenames
    assert "top.ONNX" in filenames
    assert "model.bin" not in filenames
    assert "secret.onnx" not in filenames  # hidden dir skipped
    for item in results:
        assert "path" in item
        assert "size_bytes" in item
        assert "directory" in item


def test_scan_onnx_candidates_empty_when_dir_missing(tmp_path) -> None:
    assert scan_onnx_candidates(tmp_path / "nonexistent") == []


def test_scan_onnx_candidates_stays_inside_models_root(tmp_path) -> None:
    models = tmp_path / "models"
    models.mkdir()
    (models / "local.onnx").write_bytes(b"onnx")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "escape.onnx").write_bytes(b"onnx")
    (models / "linked").symlink_to(outside, target_is_directory=True)

    results = scan_onnx_candidates(models)
    filenames = [item["filename"] for item in results]
    assert "local.onnx" in filenames
    assert "escape.onnx" not in filenames


class _DetectSession:
    def __init__(self, outputs, *, shape=None) -> None:
        self.outputs = outputs
        self._shape = shape or [1, 3, 224, 224]

    def get_inputs(self):
        class _InputMeta:
            name = "pixel_values"
            shape = self._shape

        return [_InputMeta()]

    def run(self, _names, _values):
        return self.outputs


def test_detect_auto_derives_dimension_and_input_size(tmp_path) -> None:
    model_path = tmp_path / "test.onnx"
    model_path.write_bytes(b"fake-onnx")
    session = _DetectSession(
        [np.asarray([[1.0, 2.0] + [0.0] * 382], dtype=np.float32)],
        shape=[1, 3, 336, 336],
    )
    result = detect_custom_visual_model(model_path, session_factory=lambda _p: session)
    assert result["dimension"] == 384
    assert result["input_size"] == 336
    assert result["input_size_fallback"] is False
    assert result["model_id"] == "test"
    assert len(result["sha256"]) == 64
    assert result["model_revision"] == result["sha256"][:16]


def test_detect_falls_back_to_default_input_size_on_dynamic_shape(tmp_path) -> None:
    model_path = tmp_path / "dynamic.onnx"
    model_path.write_bytes(b"fake")
    session = _DetectSession(
        [np.asarray([[1.0] * 512], dtype=np.float32)],
        shape=[1, 3, "batch", "height"],  # dynamic dims
    )
    result = detect_custom_visual_model(model_path, session_factory=lambda _p: session)
    assert result["input_size"] == 224
    assert result["input_size_fallback"] is True


def test_detect_rejects_model_with_no_embedding_output(tmp_path) -> None:
    model_path = tmp_path / "bad.onnx"
    model_path.write_bytes(b"fake")
    # 3D output, not 2D
    session = _DetectSession([np.asarray([[[1.0, 2.0, 3.0]]], dtype=np.float32)])
    with pytest.raises(VisualModelError, match="嵌入向量"):
        detect_custom_visual_model(model_path, session_factory=lambda _p: session)


def test_detect_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(VisualModelError, match="不存在"):
        detect_custom_visual_model(tmp_path / "missing.onnx")


def test_detect_picks_largest_dimension_output(tmp_path) -> None:
    model_path = tmp_path / "multi.onnx"
    model_path.write_bytes(b"multi-output")
    session = _DetectSession(
        [
            np.asarray([[1.0] * 128], dtype=np.float32),
            np.asarray([[2.0] * 768], dtype=np.float32),
        ]
    )
    result = detect_custom_visual_model(model_path, session_factory=lambda _p: session)
    assert result["dimension"] == 768


def test_detect_api_roundtrip(settings, monkeypatch) -> None:
    settings.models_root.mkdir(parents=True, exist_ok=True)
    model_path = settings.models_root / "custom.onnx"
    model_path.write_bytes(b"fake-onnx-bytes")
    session = _Session([np.asarray([[1.0] * 512], dtype=np.float32)])
    monkeypatch.setattr("prompt_hub.local_visual._create_session", lambda _path: session)
    with TestClient(create_app(settings)) as client:
        detect_resp = client.post(
            "/api/visual-index/model/detect",
            json={"path": str(model_path)},
        )
        assert detect_resp.status_code == 200
        detected = detect_resp.json()
        assert detected["dimension"] == 512
        assert detected["model_id"] == "custom"
        assert len(detected["sha256"]) == 64

        enable_resp = client.post(
            "/api/visual-index/model/enable-detected",
            json={
                "path": detected["path"],
                "dimension": detected["dimension"],
                "input_size": detected["input_size"],
                "sha256": detected["sha256"],
            },
        )
        assert enable_resp.status_code == 200
        assert enable_resp.json()["reindex_required"] is True
        assert enable_resp.json()["mode"] == "custom"
        status = client.get("/api/visual-index/status").json()
        assert status["mode"] == "custom"
        assert status["model"]["model_id"] == "custom"


def test_detect_api_rejects_missing_file(settings) -> None:
    with TestClient(create_app(settings)) as client:
        resp = client.post(
            "/api/visual-index/model/detect",
            json={"path": str(settings.models_root / "missing.onnx")},
        )
        assert resp.status_code == 404


def test_detect_api_rejects_file_outside_models_root(settings) -> None:
    outside = settings.library_root / "outside.onnx"
    outside.write_bytes(b"not-allowed")

    with TestClient(create_app(settings)) as client:
        resp = client.post(
            "/api/visual-index/model/detect",
            json={"path": str(outside)},
        )

    assert resp.status_code == 422
    assert "模型目录" in resp.json()["detail"]


def test_custom_api_rejects_file_outside_models_root(settings) -> None:
    outside = settings.library_root / "outside.onnx"
    outside.write_bytes(b"not-allowed")

    with TestClient(create_app(settings)) as client:
        resp = client.post(
            "/api/visual-index/model/custom",
            json={
                "path": str(outside),
                "model_id": "outside",
                "model_revision": "v1",
                "dimension": 512,
                "input_size": 224,
            },
        )

    assert resp.status_code == 422
    assert "模型目录" in resp.json()["detail"]


def test_detect_api_rejects_symlink_inside_models_root(settings) -> None:
    settings.models_root.mkdir(parents=True, exist_ok=True)
    outside = settings.library_root / "outside.onnx"
    outside.write_bytes(b"not-allowed")
    linked = settings.models_root / "linked.onnx"
    linked.symlink_to(outside)

    with TestClient(create_app(settings)) as client:
        resp = client.post(
            "/api/visual-index/model/detect",
            json={"path": str(linked)},
        )

    assert resp.status_code == 422
    assert "符号链接" in resp.json()["detail"]


def test_enable_detected_rejects_file_changed_after_detection(settings, monkeypatch) -> None:
    settings.models_root.mkdir(parents=True, exist_ok=True)
    model_path = settings.models_root / "changed.onnx"
    model_path.write_bytes(b"first-version")
    session = _Session([np.asarray([[1.0] * 512], dtype=np.float32)])
    monkeypatch.setattr("prompt_hub.local_visual._create_session", lambda _path: session)

    with TestClient(create_app(settings)) as client:
        detected = client.post(
            "/api/visual-index/model/detect",
            json={"path": str(model_path)},
        ).json()
        model_path.write_bytes(b"second-version")
        resp = client.post(
            "/api/visual-index/model/enable-detected",
            json={
                "path": detected["path"],
                "dimension": detected["dimension"],
                "input_size": detected["input_size"],
                "sha256": detected["sha256"],
            },
        )

    assert resp.status_code == 422
    assert "检测后发生变化" in resp.json()["detail"]


def test_candidates_api_returns_onnx_files(settings) -> None:
    onnx_dir = settings.models_root / "test"
    onnx_dir.mkdir(parents=True, exist_ok=True)
    (onnx_dir / "model.onnx").write_bytes(b"fake")
    (onnx_dir / "readme.txt").write_bytes(b"text")
    with TestClient(create_app(settings)) as client:
        resp = client.get("/api/visual-index/model/candidates")
        assert resp.status_code == 200
        data = resp.json()
        assert "models_root" in data
        filenames = [c["filename"] for c in data["candidates"]]
        assert "model.onnx" in filenames
        assert "readme.txt" not in filenames
