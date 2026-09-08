from __future__ import annotations

import hashlib
from http import HTTPStatus
from urllib.request import Request

import pytest
from fastapi.testclient import TestClient

from prompt_hub.api import create_app
from prompt_hub.background_jobs import JobCancelledError
from prompt_hub.tag_completions import (
    TAG_DOWNLOAD_JOB_TYPE,
    TagCompletionError,
    TagCompletionStore,
    WhitelistedRedirectHandler,
    download_danbooru_tags,
)
from prompt_hub.tag_locale import TagLocaleCache


class _FakeResponse:
    def __init__(self, payload: bytes, *, status: int = 200, chunk: int = 1024) -> None:
        self.status = status
        self._payload = payload
        self._chunk = chunk
        self._pos = 0

    @property
    def headers(self) -> dict[str, str]:
        return {"Content-Length": str(len(self._payload))}

    def read(self, n: int = -1) -> bytes:
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
    def __init__(self, payload: bytes, *, chunk: int = 1024) -> None:
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
        self.updates: list[tuple[int, int, str]] = []
        self.cancel_after_bytes = cancel_after_bytes
        self.cancel_requested = False

    def update(self, current: int, total: int, message: str = "") -> None:
        self.updates.append((current, total, message))
        self.raise_if_cancelled()

    def raise_if_cancelled(self) -> None:
        if self.cancel_after_bytes is not None and (
            self.updates and self.updates[-1][0] >= self.cancel_after_bytes
        ):
            self.cancel_requested = True
        if self.cancel_requested:
            raise JobCancelledError


SAMPLE_CSV = (
    "tag,category,post_count,aliases\n"
    '1girl,0,4500000,"1g, 少女, solo_girl"\n'
    '1boy,0,1200000,"1b, 少年"\n'
    'black_hair,0,2500000,"黒髪, 黑发"\n'
    'dress,0,800000,"ドレス, 礼服"\n'
    'black_dress,0,300000,"黑裙子"\n'
    'red_dress,0,150000,"赤ドレス, 红裙子"\n'
    'frieren,4,50000,"フリーレン, 芙莉莲"\n'
    'sousou_no_frieren,3,60000,"葬送のフリーレン"\n'
    'crying,0,200000,"泣き, 流泪"\n'
).encode()


def test_redirect_whitelist_allowed() -> None:
    handler = WhitelistedRedirectHandler()
    req = Request("https://huggingface.co/datasets/test")
    # Redirect within huggingface.co
    res = handler.redirect_request(
        req, None, 302, "Found", {}, "https://huggingface.co/datasets/test2"
    )
    assert res is not None
    # Redirect to cdn-lfs.hf.co
    res_lfs = handler.redirect_request(
        req, None, 302, "Found", {}, "https://cdn-lfs.hf.co/repos/123"
    )
    assert res_lfs is not None


def test_redirect_whitelist_rejected() -> None:
    handler = WhitelistedRedirectHandler()
    req = Request("https://huggingface.co/datasets/test")
    # Redirect to insecure http
    with pytest.raises(TagCompletionError, match="不允许的地址"):
        handler.redirect_request(req, None, 302, "Found", {}, "http://huggingface.co/test")

    # Redirect to external domain
    with pytest.raises(TagCompletionError, match="不允许的地址"):
        handler.redirect_request(req, None, 302, "Found", {}, "https://malicious-site.com/tag.csv")


def test_download_valid(settings) -> None:
    payload = SAMPLE_CSV
    expected_size = len(payload)
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    opener = _FakeOpener(payload, chunk=64)
    context = _Context()

    result = download_danbooru_tags(
        tag_root=settings.tag_completions_root,
        database_path=settings.database_path,
        context=context,
        opener=opener,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
    )

    assert result["installed"] is True
    assert result["total_tags"] == 9
    assert result["size_bytes"] == expected_size
    assert result["sha256"] == expected_sha256

    target_csv = settings.tag_completions_root / "danbooru_tags.csv"
    assert target_csv.is_file()
    assert target_csv.read_bytes() == payload

    store = TagCompletionStore(settings.database_path, settings.tag_completions_root)
    assert store.is_installed() is True
    status = store.status()
    assert status["installed"] is True
    assert status["total_tags"] == 9
    assert status["csv_exists"] is True


def test_download_cancellation_and_resumption(settings) -> None:
    payload = SAMPLE_CSV
    expected_size = len(payload)
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    # Step 1: cancel after receiving >= 80 bytes
    opener1 = _FakeOpener(payload, chunk=32)
    ctx1 = _Context(cancel_after_bytes=80)
    with pytest.raises(JobCancelledError):
        download_danbooru_tags(
            tag_root=settings.tag_completions_root,
            database_path=settings.database_path,
            context=ctx1,
            opener=opener1,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )

    part = settings.tag_completions_root / "danbooru_tags.csv.part"
    assert part.is_file()
    partial_bytes = part.stat().st_size
    assert partial_bytes >= 80

    # Step 2: resume download
    opener2 = _FakeOpener(payload, chunk=64)
    ctx2 = _Context()
    result = download_danbooru_tags(
        tag_root=settings.tag_completions_root,
        database_path=settings.database_path,
        context=ctx2,
        opener=opener2,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
    )

    assert result["installed"] is True
    assert not part.exists()
    target_csv = settings.tag_completions_root / "danbooru_tags.csv"
    assert target_csv.is_file()
    assert target_csv.read_bytes() == payload
    # Verify range header was sent in second request
    assert len(opener2.requests) == 1
    assert opener2.requests[0].get_header("Range") == f"bytes={partial_bytes}-"


def test_download_size_mismatch(settings) -> None:
    payload = SAMPLE_CSV
    opener = _FakeOpener(payload)
    ctx = _Context()
    with pytest.raises(TagCompletionError, match="文件大小不符"):
        download_danbooru_tags(
            tag_root=settings.tag_completions_root,
            database_path=settings.database_path,
            context=ctx,
            opener=opener,
            expected_size=len(payload) + 100,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
    part = settings.tag_completions_root / "danbooru_tags.csv.part"
    assert not part.exists()


def test_download_sha256_mismatch(settings) -> None:
    payload = SAMPLE_CSV
    opener = _FakeOpener(payload)
    ctx = _Context()
    with pytest.raises(TagCompletionError, match="SHA-256 校验失败"):
        download_danbooru_tags(
            tag_root=settings.tag_completions_root,
            database_path=settings.database_path,
            context=ctx,
            opener=opener,
            expected_size=len(payload),
            expected_sha256="0" * 64,
        )
    part = settings.tag_completions_root / "danbooru_tags.csv.part"
    assert not part.exists()


def test_tag_store_uninstalled_state(settings) -> None:
    store = TagCompletionStore(settings.database_path, settings.tag_completions_root)
    store.initialize()

    assert store.is_installed() is False
    status = store.status()
    assert status["installed"] is False
    assert status["total_tags"] == 0
    assert status["csv_exists"] is False

    query_res = store.query("1girl", limit=10)
    assert query_res["installed"] is False
    assert query_res["total_tags"] == 0
    assert query_res["results"] == []


def test_tag_store_import_and_query_ranking(settings) -> None:
    store = TagCompletionStore(settings.database_path, settings.tag_completions_root)
    store.initialize()

    csv_path = settings.tag_completions_root / "danbooru_tags.csv"
    csv_path.write_bytes(SAMPLE_CSV)
    count = store.import_csv(csv_path)
    assert count == 9
    assert store.is_installed() is True
    assert store.get_total_tags() == 9

    # 1. Empty query returns empty results with installed=True
    empty_res = store.query("")
    assert empty_res["installed"] is True
    assert empty_res["total_tags"] == 9
    assert empty_res["results"] == []

    # 2. Tag prefix match: "1g" -> "1girl"
    res_1g = store.query("1g")
    assert len(res_1g["results"]) >= 1
    assert res_1g["results"][0]["tag"] == "1girl"
    assert res_1g["results"][0]["matched_alias"] is None
    assert res_1g["results"][0]["category"] == 0
    assert res_1g["results"][0]["post_count"] == 4500000

    # 3. Tag prefix vs Substring ranking: "dress"
    # "dress" is prefix of "dress" (exact prefix)
    # "black_dress" and "red_dress" have "dress" as substring (not prefix)
    res_dress = store.query("dress")
    tags_in_order = [r["tag"] for r in res_dress["results"]]
    assert tags_in_order[0] == "dress"  # prefix match tier comes first
    # Substring matches sorted by post_count DESC: black_dress (300k) > red_dress (150k)
    assert "black_dress" in tags_in_order
    assert "red_dress" in tags_in_order
    idx_black = tags_in_order.index("black_dress")
    idx_red = tags_in_order.index("red_dress")
    assert idx_black < idx_red

    # 4. Chinese alias match: "少女" -> "1girl"
    res_girl_zh = store.query("少女")
    assert len(res_girl_zh["results"]) == 1
    assert res_girl_zh["results"][0]["tag"] == "1girl"
    assert res_girl_zh["results"][0]["matched_alias"] == "少女"

    # 5. Chinese alias match: "芙莉莲" -> "frieren" (category 4)
    res_frieren = store.query("芙莉莲")
    assert len(res_frieren["results"]) == 1
    assert res_frieren["results"][0]["tag"] == "frieren"
    assert res_frieren["results"][0]["category"] == 4
    assert res_frieren["results"][0]["matched_alias"] == "芙莉莲"

    # 6. Japanese alias match: "黒髪" -> "black_hair"
    res_hair_jp = store.query("黒髪")
    assert len(res_hair_jp["results"]) == 1
    assert res_hair_jp["results"][0]["tag"] == "black_hair"
    assert res_hair_jp["results"][0]["matched_alias"] == "黒髪"


def test_tag_store_limit_capping(settings) -> None:
    store = TagCompletionStore(settings.database_path, settings.tag_completions_root)
    store.initialize()
    csv_path = settings.tag_completions_root / "danbooru_tags.csv"
    csv_path.write_bytes(SAMPLE_CSV)
    store.import_csv(csv_path)

    # Limit=2 returns at most 2 items
    res_2 = store.query("dress", limit=2)
    assert len(res_2["results"]) == 2

    # Negative limit is clamped to 1
    res_neg = store.query("dress", limit=-5)
    assert len(res_neg["results"]) == 1

    # Over 50 limit is capped at 50
    res_large = store.query("1", limit=999)
    assert len(res_large["results"]) <= 50


def test_api_tag_completion_routes(settings) -> None:
    # 1. Uninstalled app
    with TestClient(create_app(settings)) as client:
        status_res = client.get("/api/tag-completions/status")
        assert status_res.status_code == HTTPStatus.OK
        assert status_res.json()["installed"] is False

        query_res = client.get("/api/tag-completions?q=girl")
        assert query_res.status_code == HTTPStatus.OK
        assert query_res.json()["installed"] is False
        assert query_res.json()["results"] == []

        # 2. Trigger download job
        dl_res = client.post("/api/tag-completions/download")
        assert dl_res.status_code == HTTPStatus.ACCEPTED
        job_data = dl_res.json()
        assert "job" in job_data
        assert job_data["job"]["job_type"] == TAG_DOWNLOAD_JOB_TYPE

    # 3. Manually populate CSV & import to test installed API response
    store = TagCompletionStore(settings.database_path, settings.tag_completions_root)
    csv_path = settings.tag_completions_root / "danbooru_tags.csv"
    csv_path.write_bytes(SAMPLE_CSV)
    store.import_csv(csv_path)

    with TestClient(create_app(settings)) as client:
        status_installed = client.get("/api/tag-completions/status").json()
        assert status_installed["installed"] is True
        assert status_installed["total_tags"] == 9

        tag_res = client.get("/api/tag-completions?q=1g&limit=5").json()
        assert tag_res["installed"] is True
        assert len(tag_res["results"]) >= 1
        assert tag_res["results"][0]["tag"] == "1girl"


def test_import_keeps_tag_named_tag(settings, tmp_path) -> None:
    """Danbooru has a real tag literally named "tag"; only the header may be skipped."""
    csv_path = tmp_path / "tags.csv"
    csv_path.write_text(
        "tag,category,count,alias\n1girl,0,100,girl\ntag,0,978,\nsolo,0,50,\n",
        encoding="utf-8",
    )
    store = TagCompletionStore(settings.database_path, settings.tag_completions_root)
    store.initialize()
    assert store.import_csv(csv_path) == 3
    assert any(item["tag"] == "tag" for item in store.query("tag", limit=10)["items"])


def test_same_tag_renders_the_same_however_it_matched(settings, tmp_path) -> None:
    """Typing a tag name and typing one of its aliases must yield identical detail."""
    csv_path = tmp_path / "tags.csv"
    csv_path.write_text(
        'tag,category,count,alias\n1girl,0,4974288,"女孩,少女,女の子"\n',
        encoding="utf-8",
    )
    store = TagCompletionStore(settings.database_path, settings.tag_completions_root)
    store.initialize()
    store.import_csv(csv_path)

    by_name = store.query("1girl", limit=5)["items"][0]
    by_alias = store.query("女孩", limit=5)["items"][0]

    assert by_name["tag"] == by_alias["tag"] == "1girl"
    for field in ("translation_zh", "aliases", "category", "post_count"):
        assert by_name[field] == by_alias[field]
    assert set(by_name["aliases"][:2]) == {"女孩", "少女"}
    assert by_name["aliases"][-1] == "女の子"


def test_candidates_expose_space_separated_insert_text(settings, tmp_path) -> None:
    """Prompts in this library use spaces; Danbooru tags use underscores."""
    csv_path = tmp_path / "tags.csv"
    csv_path.write_text(
        "tag,category,count,alias\nlarge_breasts,0,900,\nsolo,0,50,\n",
        encoding="utf-8",
    )
    store = TagCompletionStore(settings.database_path, settings.tag_completions_root)
    store.initialize()
    store.import_csv(csv_path)

    for query in ("large_breasts", "large breasts"):
        item = store.query(query, limit=5)["items"][0]
        assert item["tag"] == "large_breasts"
        assert item["display_tag"] == "large breasts"


def test_completions_use_locale_cache_for_translation(settings, tmp_path) -> None:
    """Cached translations surface in candidates without calling any model."""
    cache = TagLocaleCache(settings.database_path)
    cache.initialize()
    cache.set("antler_girl", "鹿角少女")
    csv_path = tmp_path / "tags.csv"
    csv_path.write_text(
        "tag,category,count,alias\nantler_girl,0,900,\n",
        encoding="utf-8",
    )
    store = TagCompletionStore(
        settings.database_path,
        settings.tag_completions_root,
        locale_cache=cache,
    )
    store.initialize()
    store.import_csv(csv_path)

    item = store.query("antler_girl", limit=5)["items"][0]
    assert item["tag"] == "antler_girl"
    assert item["translation_zh"] == "鹿角少女"
