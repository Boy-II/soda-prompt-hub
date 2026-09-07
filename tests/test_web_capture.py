from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from prompt_hub.api import create_app
from prompt_hub.database import PromptDatabase
from prompt_hub.web_capture import FetchResult, WebCaptureError, WebCaptureService


def _service(settings, fetcher):
    database = PromptDatabase(settings.database_path)
    database.initialize()
    return WebCaptureService(settings, database, fetcher=fetcher), database


def test_civitai_is_link_only_and_never_fetches(settings) -> None:
    def forbidden_fetcher(_url: str, _limit: int) -> FetchResult:
        message = "link-only capture must not fetch the page"
        raise AssertionError(message)

    service, database = _service(settings, forbidden_fetcher)
    captured = service.capture(
        url="https://civitai.red/models/2885952/linhuier?modelVersionId=3262276",
        title="林悔儿 LoRA",
        note="回到模型页核对推荐提示词",
        safety="adult",
        license_name="unknown",
    )

    assert captured["cache_policy"] == "link_only"
    assert captured["cached"] is False
    assert captured["content_sha256"]
    assert service.list_captures()[0]["title"] == "林悔儿 LoRA"
    result = database.search("林悔儿", limit=5)[0]
    assert result["source_url"] == captured["url"]
    assert result["metadata"]["capture_policy"] == "link_only"


def test_github_text_capture_hashes_and_incrementally_preserves_marks(settings) -> None:
    responses = [
        FetchResult(
            final_url="https://raw.githubusercontent.com/example/prompts/main/README.md",
            content_type="text/markdown; charset=utf-8",
            body=b"# Lighting\ncinematic rim light, dusk library",
        ),
        FetchResult(
            final_url="https://raw.githubusercontent.com/example/prompts/main/README.md",
            content_type="text/markdown",
            body=b"# Lighting\ncinematic rim light, dusk archive, dust motes",
        ),
    ]

    def fetcher(_url: str, _limit: int) -> FetchResult:
        return responses.pop(0)

    service, database = _service(settings, fetcher)
    first = service.capture(
        url="https://raw.githubusercontent.com/example/prompts/main/README.md",
        title="Lighting notes",
        note="我的电影灯光摘录",
        safety="sfw",
        license_name="MIT",
    )
    database.save_mark(
        source_id=first["source_id"],
        external_id=first["external_id"],
        favorite=True,
        rating=5,
        note="实测很好",
    )
    second = service.capture(
        url=first["url"],
        title="Lighting notes v2",
        note="我的电影灯光摘录",
        safety="sfw",
        license_name="MIT",
    )

    assert second["capture_id"] == first["capture_id"]
    assert second["content_sha256"] != first["content_sha256"]
    assert second["cached"] is True
    manifest_path = settings.web_sources_root / first["capture_id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["content_sha256"] == second["content_sha256"]
    result = database.search("dusk archive", limit=5)[0]
    assert result["favorite"] is True
    assert result["user_rating"] == 5
    assert result["user_note"] == "实测很好"


def test_direct_github_image_is_cached_with_safe_media_path(settings) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"safe-test-image"

    def fetcher(_url: str, _limit: int) -> FetchResult:
        return FetchResult(
            final_url="https://raw.githubusercontent.com/example/prompts/main/reference.png",
            content_type="image/png",
            body=png,
        )

    service, database = _service(settings, fetcher)
    captured = service.capture(
        url="https://raw.githubusercontent.com/example/prompts/main/reference.png",
        title="服装视觉参考",
        note="蓝白制服",
        safety="suggestive",
        license_name="unknown",
    )

    assert captured["media_kind"] == "image"
    assert captured["cached_media_path"].endswith("asset.png")
    assert service.resolve_media(captured["capture_id"]).read_bytes() == png
    source = next(
        item for item in database.list_sources() if item["source_id"] == captured["source_id"]
    )
    assert source["visual_count"] == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/prompts",
        "https://localhost/prompts",
        "https://127.0.0.1/prompts",
        "https://user:secret@github.com/prompts",
        "https://github.com:8443/prompts",
        "https://example.com/prompts",
    ],
)
def test_capture_rejects_untrusted_or_unsafe_urls(settings, url) -> None:
    service, _database = _service(settings, lambda *_args: None)
    with pytest.raises(WebCaptureError):
        service.capture(
            url=url,
            title="unsafe",
            note="",
            safety="sfw",
            license_name="unknown",
        )


def test_capture_rejects_untrusted_redirect_and_oversized_content(settings) -> None:
    def redirected(_url: str, _limit: int) -> FetchResult:
        return FetchResult(
            final_url="https://example.com/private",
            content_type="text/plain",
            body=b"prompt",
        )

    service, _database = _service(settings, redirected)
    with pytest.raises(WebCaptureError, match="重定向"):
        service.capture(
            url="https://raw.githubusercontent.com/example/prompts/main/a.txt",
            title="redirect",
            note="",
            safety="sfw",
            license_name="unknown",
        )


def test_web_capture_api_saves_link_only_source_and_exposes_page_marker(settings) -> None:
    with TestClient(create_app(settings)) as client:
        saved = client.post(
            "/api/web-captures",
            json={
                "url": "https://civitai.com/models/123/example",
                "title": "Civitai 提示词参考",
                "note": "回看示例图和触发词",
                "safety": "adult",
                "license_name": "unknown",
            },
        )
        assert saved.status_code == 201
        assert saved.json()["cache_policy"] == "link_only"
        captures = client.get("/api/web-captures")
        assert captures.status_code == 200
        assert captures.json()[0]["title"] == "Civitai 提示词参考"
        media = client.get(f"/api/web-captures/{saved.json()['capture_id']}/media")
        assert media.status_code == 404
        html = client.get("/").text
        assert 'id="sourceCaptureForm"' in html
        assert "source-delete-btn" in html
        assert "capture-delete-btn" in html

    def oversized(_url: str, limit: int) -> FetchResult:
        return FetchResult(
            final_url="https://raw.githubusercontent.com/example/prompts/main/a.txt",
            content_type="text/plain",
            body=b"x" * (limit + 1),
        )

    service, _database = _service(settings, oversized)
    with pytest.raises(WebCaptureError, match="过大"):
        service.capture(
            url="https://raw.githubusercontent.com/example/prompts/main/a.txt",
            title="oversized",
            note="",
            safety="sfw",
            license_name="unknown",
        )


def test_delete_git_preset_source_rejected(source_tree) -> None:
    app = create_app(source_tree)
    with TestClient(app) as client:
        res = client.delete("/api/sources/clio-style-preview")
        assert res.status_code == 400
        assert "内置资料库不能在页面删除" in res.json()["detail"]

        res2 = client.delete("/api/sources/krea-open-prompts")
        assert res2.status_code == 400
        assert "内置资料库不能在页面删除" in res2.json()["detail"]

        clio_dir = source_tree.git_sources_root / "clio-style-preview"
        assert clio_dir.is_dir()
        assert (clio_dir / "styles.json").exists()


def test_delete_nonexistent_source_returns_404(source_tree) -> None:
    app = create_app(source_tree)
    with TestClient(app) as client:
        res = client.delete("/api/sources/web-nonexistent")
        assert res.status_code == 404
        assert "不存在" in res.json()["detail"]


def test_delete_custom_source_with_marks_retention_and_purge(settings) -> None:
    app = create_app(settings)
    database = PromptDatabase(settings.database_path)
    with TestClient(app) as client:
        saved = client.post(
            "/api/web-captures",
            json={
                "url": "https://civitai.com/models/999/test-model",
                "title": "测试模型网页资料",
                "note": "测试检索关键词 unique_keyword_alpha",
                "safety": "sfw",
                "license_name": "MIT",
            },
        )
        assert saved.status_code == 201
        capture = saved.json()
        capture_id = capture["capture_id"]
        source_id = capture["source_id"]

        capture_dir = settings.web_sources_root / capture_id
        test_file = capture_dir / "extra.txt"
        test_file.write_text("extra cached file", encoding="utf-8")

        database.save_mark(
            source_id=source_id,
            external_id=capture_id,
            favorite=True,
            rating=5,
            note="人工优质标记",
        )

        assert len(database.search("unique_keyword_alpha")) == 1

        res = client.delete(f"/api/sources/{source_id}")
        assert res.status_code == 200
        data = res.json()
        assert data["deleted"] is True
        assert data["deleted_entries"] == 1
        assert data["deleted_files"] >= 2
        assert data["retained_marks"] == 1
        assert data["purged_marks"] == 0

        assert database.get_source(source_id) is None
        assert len(database.search("unique_keyword_alpha")) == 0
        assert not capture_dir.exists()

        with database.connect() as conn:
            marks = conn.execute(
                "SELECT * FROM user_marks WHERE source_id = ?",
                (source_id,),
            ).fetchall()
            assert len(marks) == 1

        # Re-add to test purge_marks=True
        saved2 = client.post(
            "/api/web-captures",
            json={
                "url": "https://civitai.com/models/999/test-model",
                "title": "测试模型网页资料2",
                "note": "重新添加 unique_keyword_alpha",
                "safety": "sfw",
                "license_name": "MIT",
            },
        )
        assert saved2.status_code == 201

        res_purge = client.delete(f"/api/sources/{source_id}?purge_marks=true")
        assert res_purge.status_code == 200
        data_purge = res_purge.json()
        assert data_purge["purged_marks"] == 1
        assert data_purge["retained_marks"] == 0

        with database.connect() as conn:
            marks_purged = conn.execute(
                "SELECT * FROM user_marks WHERE source_id = ?",
                (source_id,),
            ).fetchall()
            assert len(marks_purged) == 0


def test_delete_single_web_capture(settings) -> None:
    app = create_app(settings)
    database = PromptDatabase(settings.database_path)
    with TestClient(app) as client:
        res1 = client.post(
            "/api/web-captures",
            json={
                "url": "https://civitai.com/models/111/one",
                "title": "摘录一",
                "note": "笔记一 unique_token_one",
                "safety": "sfw",
                "license_name": "MIT",
            },
        )
        res2 = client.post(
            "/api/web-captures",
            json={
                "url": "https://civitai.com/models/222/two",
                "title": "摘录二",
                "note": "笔记二 unique_token_two",
                "safety": "sfw",
                "license_name": "MIT",
            },
        )
        cap1 = res1.json()
        cap2 = res2.json()
        source_id = cap1["source_id"]
        assert cap2["source_id"] == source_id

        del1 = client.delete(f"/api/web-captures/{cap1['capture_id']}")
        assert del1.status_code == 200
        assert del1.json()["source_deleted"] is False
        assert not (settings.web_sources_root / cap1["capture_id"]).exists()
        assert (settings.web_sources_root / cap2["capture_id"]).exists()
        assert len(database.search("unique_token_one")) == 0
        assert len(database.search("unique_token_two")) == 1

        src = database.get_source(source_id)
        assert src is not None
        assert src["entry_count"] == 1

        del2 = client.delete(f"/api/web-captures/{cap2['capture_id']}")
        assert del2.status_code == 200
        assert del2.json()["source_deleted"] is True
        assert not (settings.web_sources_root / cap2["capture_id"]).exists()
        assert len(database.search("unique_token_two")) == 0
        assert database.get_source(source_id) is None

        del_missing = client.delete(f"/api/web-captures/{cap1['capture_id']}")
        assert del_missing.status_code == 404

        del_invalid = client.delete("/api/web-captures/invalid-id")
        assert del_invalid.status_code == 422
