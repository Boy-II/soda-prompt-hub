from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from prompt_hub import tag_locale
from prompt_hub import tag_locale as tag_locale_module
from prompt_hub.api import create_app
from prompt_hub.tag_locale import (
    TagLocaleCache,
    TagLocaleError,
    localize_tag,
    localize_tags,
    make_model_translator,
    tag_catalog,
    translate_tag_with_model,
)


def test_tag_locale_keeps_canonical_english_and_switches_display() -> None:
    chinese = localize_tag("silver_hair", language="zh")
    assert chinese == {
        "tag": "silver_hair",
        "en": "silver_hair",
        "zh": "银发",
        "display": "银发 (silver_hair)",
        "known": True,
    }
    assert localize_tag("silver_hair", language="en")["display"] == "silver_hair"
    assert localize_tag("blue eyes", language="zh")["zh"] == "蓝眼睛"
    assert localize_tag("custom_artist_style", language="zh")["display"] == "custom_artist_style"


def test_tag_locale_resolves_known_chinese_but_rejects_unknown_chinese() -> None:
    localized = localize_tags(["银发", "solo", "银发"], language="zh")
    assert [item["tag"] for item in localized] == ["silver_hair", "solo"]
    with pytest.raises(TagLocaleError, match="无法确认中文标签"):
        localize_tag("自创标签", language="zh")


def test_tag_catalog_exposes_chinese_choices_with_canonical_ids() -> None:
    catalog = tag_catalog(language="zh")
    silver_hair = next(item for item in catalog if item["en"] == "silver_hair")
    watermark = next(item for item in catalog if item["en"] == "watermark")
    assert silver_hair["display"] == "银发 (silver_hair)"
    assert watermark["zh"] == "水印"
    assert all(item["tag"].isascii() for item in catalog)


def test_tag_locale_api(settings) -> None:
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/tags/localize",
            json={"tags": ["1girl", "blue_eyes", "full_body"], "language": "zh"},
        )
        assert response.status_code == 200
        items = response.json()["items"]
        assert [item["en"] for item in items] == ["1girl", "blue_eyes", "full_body"]
        assert [item["zh"] for item in items] == ["一名女孩", "蓝眼睛", "全身"]

        catalog = client.get("/api/tags/catalog?language=zh")
        assert catalog.status_code == 200
        assert any(
            item["en"] == "silver_hair" and item["zh"] == "银发" for item in catalog.json()["items"]
        )

        invalid = client.post(
            "/api/tags/localize",
            json={"tags": ["无法映射的中文"], "language": "zh"},
        )
        assert invalid.status_code == 422


class _FakeConnection:
    def __init__(self, *, base_url: str, model_name: str, api_key: str = "") -> None:
        self.base_url = base_url
        self.model_name = model_name
        self.api_key = api_key


class _FakeConnections:
    def __init__(
        self,
        connection: _FakeConnection | None = None,
        assist: _FakeConnection | None = None,
    ) -> None:
        self._connection = connection
        self._assist = assist

    def list_connections(self) -> list[_FakeConnection]:
        return [self._connection] if self._connection else []

    def get_caption_assist(self) -> _FakeConnection | None:
        return self._assist


def test_manual_table_wins_over_cache(tmp_path) -> None:
    cache = TagLocaleCache(tmp_path / "locale.sqlite")
    cache.initialize()
    cache.set("silver_hair", "机器翻译的错误中文")
    localized = localize_tag("silver_hair", language="zh", cache=cache)
    assert localized["zh"] == "银发"


def test_cache_hit_does_not_call_model(tmp_path) -> None:
    cache = TagLocaleCache(tmp_path / "locale.sqlite")
    cache.initialize()
    cache.set("antler_girl", "鹿角少女")

    def translator(tag: str) -> str:
        message = f"model should not be called for cached tag: {tag}"
        raise AssertionError(message)

    localized = localize_tag("antler_girl", language="zh", cache=cache, translator=translator)
    assert localized["zh"] == "鹿角少女"
    assert localized["known"] is True


def test_model_failure_returns_original_not_exception(tmp_path) -> None:
    cache = TagLocaleCache(tmp_path / "locale.sqlite")
    cache.initialize()

    def translator(tag: str) -> str:
        del tag
        message = "model unreachable"
        raise RuntimeError(message)

    localized = localize_tag("antler_girl", language="zh", cache=cache, translator=translator)
    assert localized["zh"] == ""
    assert localized["known"] is False
    assert localized["display"] == "antler_girl"


def test_model_translation_is_cached(tmp_path) -> None:
    cache = TagLocaleCache(tmp_path / "locale.sqlite")
    cache.initialize()
    calls: list[str] = []

    def translator(tag: str) -> str:
        calls.append(tag)
        return "鹿角少女"

    first = localize_tag("antler_girl", language="zh", cache=cache, translator=translator)
    assert first["zh"] == "鹿角少女"
    assert cache.get("antler_girl") == "鹿角少女"

    second = localize_tag("antler_girl", language="zh", cache=cache, translator=translator)
    assert second["zh"] == "鹿角少女"
    assert calls == ["antler_girl"]


def test_translate_tag_with_model_uses_connection(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(connection: object, payload: dict[str, object]) -> dict[str, object]:
        captured["connection"] = connection
        captured["payload"] = payload
        return {"choices": [{"message": {"content": "鹿角少女"}}]}

    monkeypatch.setattr(tag_locale_module, "_post_chat_completion", fake_post)
    connection = _FakeConnection(
        base_url="http://127.0.0.1:1234/v1", model_name="qwen", api_key="k"
    )
    connections = _FakeConnections(connection)
    result = translate_tag_with_model("antler_girl", connections=connections)
    assert result == "鹿角少女"
    assert captured["connection"] is connection
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "qwen"
    assert payload["messages"][1]["content"] == "antler_girl"


def test_translate_tag_with_model_no_connection_returns_empty() -> None:
    assert translate_tag_with_model("antler_girl", connections=_FakeConnections()) == ""
    assert translate_tag_with_model("antler_girl", connections=None) == ""


def test_translate_tag_with_model_cleans_garbage(monkeypatch) -> None:
    def fake_post(connection: object, payload: dict[str, object]) -> dict[str, object]:
        del connection, payload
        return {"choices": [{"message": {"content": "antler_girl"}}]}

    monkeypatch.setattr(tag_locale_module, "_post_chat_completion", fake_post)
    result = translate_tag_with_model(
        "antler_girl",
        connections=_FakeConnections(_FakeConnection(base_url="http://x", model_name="m")),
    )
    assert result == ""


def test_make_model_translator_never_raises(monkeypatch, tmp_path) -> None:
    def fake_post(connection: object, payload: dict[str, object]) -> dict[str, object]:
        del connection, payload
        message = "connection refused"
        raise OSError(message)

    monkeypatch.setattr(tag_locale_module, "_post_chat_completion", fake_post)
    cache = TagLocaleCache(tmp_path / "locale.sqlite")
    cache.initialize()
    translator = make_model_translator(
        _FakeConnections(_FakeConnection(base_url="http://x", model_name="m"))
    )
    localized = localize_tag("antler_girl", language="zh", cache=cache, translator=translator)
    assert localized["zh"] == ""
    assert localized["display"] == "antler_girl"
    assert cache.get("antler_girl") is None


class TestCaptionTranslation:
    """Krea 2 草稿是整段自然语言。与标签走不同的路。"""

    def test_does_not_touch_the_tag_cache(self, tmp_path, monkeypatch) -> None:
        """整段说明不该写进标签快取——键是标签。句子会污染它。"""
        cache = TagLocaleCache(tmp_path / "locale.sqlite")
        cache.initialize()

        monkeypatch.setattr(
            tag_locale,
            "_post_chat_completion",
            lambda *_: {"choices": [{"message": {"content": "一位女性站在窗边。"}}]},
        )
        text = tag_locale.translate_caption_with_model(
            "A woman standing by the window.",
            connections=_FakeConnections(_local_connection()),
        )

        assert text == "一位女性站在窗边。"
        assert cache.get("A woman standing by the window.") is None

    def test_failure_returns_empty_instead_of_raising(self, monkeypatch) -> None:
        """翻译服务出问题不该让逐张审核停下来。"""
        refused = OSError("connection refused")

        def explode(*_):
            raise refused

        monkeypatch.setattr(tag_locale, "_post_chat_completion", explode)
        assert (
            tag_locale.translate_caption_with_model(
                "anything", connections=_FakeConnections(_local_connection())
            )
            == ""
        )

    def test_echoed_source_is_not_a_translation(self, monkeypatch) -> None:
        """模型把原文原样回来时宁可不显示。也不要假装翻好了。"""
        monkeypatch.setattr(
            tag_locale,
            "_post_chat_completion",
            lambda *_: {"choices": [{"message": {"content": "A woman."}}]},
        )
        assert (
            tag_locale.translate_caption_with_model(
                "A woman.", connections=_FakeConnections(_local_connection())
            )
            == ""
        )

    def test_no_connection_returns_empty(self) -> None:
        assert tag_locale.translate_caption_with_model("A woman.", connections=None) == ""


def _local_connection() -> _FakeConnection:
    return _FakeConnection(base_url="http://127.0.0.1:1234/v1", model_name="qwen")
