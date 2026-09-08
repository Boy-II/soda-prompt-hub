from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.request import Request, urlopen

from prompt_hub.schema_migrations import record_schema_migration

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from prompt_hub.model_connections import ModelConnection, ModelConnectionStore

TAG_TRANSLATIONS_ZH = {
    "1girl": "一名女孩",
    "1woman": "一名成年女性",
    "1boy": "一名男孩",
    "1man": "一名成年男性",
    "solo": "单人",
    "multiple_girls": "多名女性",
    "looking_at_viewer": "看向观众",
    "full_body": "全身",
    "upper_body": "上半身",
    "cowboy_shot": "大腿以上构图",
    "portrait": "肖像",
    "standing": "站立",
    "sitting": "坐姿",
    "lying": "躺姿",
    "smile": "微笑",
    "open_mouth": "张嘴",
    "closed_mouth": "闭嘴",
    "blush": "脸红",
    "long_hair": "长发",
    "short_hair": "短发",
    "medium_hair": "中长发",
    "very_long_hair": "超长发",
    "silver_hair": "银发",
    "white_hair": "白发",
    "grey_hair": "灰发",
    "black_hair": "黑发",
    "brown_hair": "棕发",
    "blonde_hair": "金发",
    "red_hair": "红发",
    "blue_hair": "蓝发",
    "purple_hair": "紫发",
    "pink_hair": "粉发",
    "green_hair": "绿发",
    "blue_eyes": "蓝眼睛",
    "red_eyes": "红眼睛",
    "green_eyes": "绿眼睛",
    "purple_eyes": "紫眼睛",
    "brown_eyes": "棕眼睛",
    "yellow_eyes": "黄眼睛",
    "grey_eyes": "灰眼睛",
    "heterochromia": "异色瞳",
    "dress": "连衣裙",
    "skirt": "裙子",
    "shirt": "衬衫",
    "jacket": "夹克",
    "coat": "外套",
    "gloves": "手套",
    "boots": "靴子",
    "high_heels": "高跟鞋",
    "uniform": "制服",
    "military_uniform": "军装",
    "school_uniform": "校服",
    "swimsuit": "泳装",
    "bikini": "比基尼",
    "lingerie": "内衣",
    "nude": "裸体",
    "breasts": "胸部",
    "large_breasts": "大胸",
    "small_breasts": "小胸",
    "cleavage": "乳沟",
    "nipples": "乳头",
    "indoors": "室内",
    "outdoors": "室外",
    "simple_background": "简洁背景",
    "white_background": "白色背景",
    "black_background": "黑色背景",
    "night": "夜晚",
    "sunset": "日落",
    "backlighting": "逆光",
    "rim_light": "轮廓光",
    "depth_of_field": "景深",
    "from_above": "俯视",
    "from_below": "仰视",
    "from_side": "侧面视角",
    "front_view": "正面视角",
    "profile": "侧脸",
    "masterpiece": "杰作质量",
    "best_quality": "最佳质量",
    "very_aesthetic": "高审美",
    "watermark": "水印",
    "text": "文字",
    "signature": "签名",
    "username": "用户名",
    "lowres": "低分辨率",
    "blurry": "模糊",
    "jpeg_artifacts": "JPEG 压缩痕迹",
}

TOKEN_TRANSLATIONS_ZH = {
    "adult": "成年",
    "aesthetic": "审美",
    "arm": "手臂",
    "arms": "手臂",
    "back": "背面",
    "black": "黑色",
    "blonde": "金色",
    "blue": "蓝色",
    "body": "身体",
    "brown": "棕色",
    "closed": "闭合",
    "coat": "外套",
    "dark": "深色",
    "dress": "连衣裙",
    "eyes": "眼睛",
    "face": "脸部",
    "female": "女性",
    "from": "从",
    "full": "全身",
    "girl": "女孩",
    "green": "绿色",
    "grey": "灰色",
    "hair": "头发",
    "hand": "手",
    "hands": "手",
    "high": "高",
    "jacket": "夹克",
    "light": "光线",
    "long": "长",
    "looking": "看向",
    "male": "男性",
    "medium": "中等",
    "mouth": "嘴",
    "open": "张开",
    "pink": "粉色",
    "purple": "紫色",
    "quality": "质量",
    "red": "红色",
    "short": "短",
    "shirt": "衬衫",
    "side": "侧面",
    "silver": "银色",
    "skirt": "裙子",
    "upper": "上半身",
    "very": "非常",
    "view": "视角",
    "viewer": "观众",
    "white": "白色",
    "yellow": "黄色",
}

_REVERSE_TRANSLATIONS = {value.casefold(): key for key, value in TAG_TRANSLATIONS_ZH.items()}
_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_()'./:+\- ]*$")

TAG_LOCALE_COMPONENT = "tag_locale"
TAG_LOCALE_SCHEMA_VERSION = 1
TAG_LOCALE_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS tag_locale_cache (
    tag TEXT PRIMARY KEY COLLATE NOCASE,
    zh TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
TRANSLATION_SYSTEM_PROMPT = (
    "You translate English Danbooru image tags into Simplified Chinese. "
    "Reply with only the Chinese translation itself. No explanation, no "
    "prefix or suffix, no punctuation."
)
TRANSLATION_TIMEOUT = 60
TRANSLATION_MAX_TOKENS = 60
MAX_TRANSLATION_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TRANSLATION_LENGTH = 60

_logger = logging.getLogger(__name__)


class TagLocaleError(ValueError):
    pass


class TagLocaleCache:
    """SQLite-backed cache of tag -> Simplified Chinese translations.

    The key is the original English tag text, so a translation never goes
    stale just because the tagging prompt changed.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.executescript(TAG_LOCALE_CACHE_SCHEMA)
            record_schema_migration(
                connection,
                TAG_LOCALE_COMPONENT,
                TAG_LOCALE_SCHEMA_VERSION,
                "Initialize tag translation cache",
            )
            connection.commit()

    def get(self, tag: str) -> str | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT zh FROM tag_locale_cache WHERE tag = ?",
                (tag,),
            ).fetchone()
        return str(row["zh"]) if row else None

    def set(self, tag: str, zh: str) -> None:
        with closing(self.connect()) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO tag_locale_cache (tag, zh, updated_at) VALUES (?, ?, ?)",
                (tag, zh, _now()),
            )


def resolve_canonical_tag(value: str) -> str:
    clean = value.strip()
    if not clean:
        return ""
    translated = _REVERSE_TRANSLATIONS.get(clean.casefold())
    if translated:
        return translated
    if _contains_cjk(clean):
        message = f"无法确认中文标签“{clean}”对应的标准英文 tag"
        raise TagLocaleError(message)
    if not _TAG_PATTERN.fullmatch(clean):
        message = f"标签包含不支持的字符: {clean}"
        raise TagLocaleError(message)
    return clean


def localize_tag(
    value: str,
    *,
    language: str = "zh",
    cache: TagLocaleCache | None = None,
    translator: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    canonical = resolve_canonical_tag(value)
    zh, known = _lookup_zh(canonical, cache=cache, translator=translator)
    display = canonical if language == "en" else f"{zh} ({canonical})" if zh else canonical
    return {
        "tag": canonical,
        "en": canonical,
        "zh": zh,
        "display": display,
        "known": known,
    }


def localize_tags(
    values: Iterable[str],
    *,
    language: str = "zh",
    cache: TagLocaleCache | None = None,
    translator: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    if language not in {"zh", "en"}:
        message = "标签显示语言只支持 zh 或 en"
        raise TagLocaleError(message)
    result = []
    seen: set[str] = set()
    for value in values:
        localized = localize_tag(
            str(value),
            language=language,
            cache=cache,
            translator=translator,
        )
        canonical = str(localized["tag"])
        key = canonical.casefold()
        if not canonical or key in seen:
            continue
        seen.add(key)
        result.append(localized)
    return result


def tag_catalog(*, language: str = "zh") -> list[dict[str, Any]]:
    """Return curated common tags while retaining their canonical English values."""
    return localize_tags(sorted(TAG_TRANSLATIONS_ZH), language=language)


def make_model_translator(connections: ModelConnectionStore | None) -> Callable[[str], str]:
    """Return a tag->zh translator backed by the existing custom API interface.

    The translator never raises: any failure yields an empty string so the
    tagging flow keeps running with the original English tag.
    """

    def translate(tag: str) -> str:
        return translate_tag_with_model(tag, connections=connections)

    return translate


def translate_tag_with_model(tag: str, *, connections: ModelConnectionStore | None) -> str:
    connection = _pick_connection(connections)
    if connection is None:
        return ""
    payload = {
        "model": connection.model_name,
        "messages": [
            {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
            {"role": "user", "content": tag},
        ],
        "temperature": 0.0,
        "max_tokens": TRANSLATION_MAX_TOKENS,
        "stream": False,
    }
    try:
        response = _post_chat_completion(connection, payload)
        content = str(response["choices"][0]["message"]["content"])
        return _clean_translation(content, tag)
    except Exception as error:  # noqa: BLE001
        _logger.warning("Tag translation failed for %r: %s", tag, error)
        return ""


CAPTION_TRANSLATION_SYSTEM_PROMPT = (
    "You translate English image captions into Chinese. "
    "Reply with only the Chinese translation itself. "
    "Keep it one paragraph. No explanation, no prefix, no quotes."
)
# 一段自然语言说明比单个标签长得多。用标签的 60 token 上限会被截断。
CAPTION_TRANSLATION_MAX_TOKENS = 1200
MAX_CAPTION_LENGTH = 12000
CAPTION_TOO_LONG_MESSAGE = "说明文字过长。无法翻译"


def translate_caption_with_model(
    caption: str,
    *,
    connections: ModelConnectionStore | None,
) -> str:
    """把一整段英文说明翻成中文。供人工对照。

    不走标签快取 快取的键是标签。把整段句子塞进去会污染它。而且同一段
    说明几乎不会重复出现。快取也没有价值。

    失败一律回空字串并记 log。中文对照是辅助信息。拿不到就不显示。
    不能让审核流程因为翻译服务出问题而中断。
    """
    text = caption.strip()
    if not text:
        return ""
    if len(text) > MAX_CAPTION_LENGTH:
        message = "说明文字过长。无法翻译"
        raise TagLocaleError(message)
    connection = _pick_connection(connections)
    if connection is None:
        return ""
    payload = {
        "model": connection.model_name,
        "messages": [
            {"role": "system", "content": CAPTION_TRANSLATION_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        "temperature": 0.0,
        "max_tokens": CAPTION_TRANSLATION_MAX_TOKENS,
        "stream": False,
    }
    try:
        response = _post_chat_completion(connection, payload)
        content = str(response["choices"][0]["message"]["content"]).strip()
    except Exception as error:  # noqa: BLE001
        _logger.warning("Caption translation failed: %s", error)
        return ""
    # 模型偶尔会把原文原样回来。那不是翻译。宁可不显示。
    return "" if content == text else content


def _pick_connection(connections: ModelConnectionStore | None) -> ModelConnection | None:
    """优先用使用者在模型服务里指定的那个。

    没有指定时退回第一个启用的连线——那是设定这个选项之前的行为。
    但「刚好排第一」不该是隐含的决定 启用顺序一变。翻译就会无声换模型。
    """
    if connections is None:
        return None
    try:
        chosen = connections.get_caption_assist()
        if chosen is not None:
            return chosen
        rows = connections.list_connections()
    except (ValueError, OSError):
        return None
    return rows[0] if rows else None


def _post_chat_completion(connection: ModelConnection, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{connection.base_url.rstrip('/')}/chat/completions"
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if connection.api_key:
        headers["Authorization"] = f"Bearer {connection.api_key}"
    request = Request(url, data=data, method="POST", headers=headers)  # noqa: S310
    with urlopen(request, timeout=TRANSLATION_TIMEOUT) as response:  # noqa: S310
        body = response.read(MAX_TRANSLATION_RESPONSE_BYTES + 1)
    if len(body) > MAX_TRANSLATION_RESPONSE_BYTES:
        message = "本地模型翻译响应过大"
        raise TagLocaleError(message)
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        message = "本地模型翻译响应格式错误"
        raise TagLocaleError(message)
    return parsed


def _clean_translation(raw: str, tag: str) -> str:
    text = raw.strip().strip("\"'「」『』“”`").strip()
    if not text or len(text) > MAX_TRANSLATION_LENGTH:
        return ""
    if text.casefold() == tag.casefold():
        return ""
    if not _contains_cjk(text):
        return ""
    return text


def _lookup_zh(
    canonical: str,
    *,
    cache: TagLocaleCache | None,
    translator: Callable[[str], str] | None,
) -> tuple[str, bool]:
    zh, known = _translate_zh(canonical)
    if known:
        return zh, True
    cached = _cache_get(cache, canonical)
    if cached:
        return cached, True
    if translator is not None:
        translated = _safe_translate(translator, canonical)
        if translated:
            _cache_set(cache, canonical, translated)
            return translated, True
    return "", False


def _cache_get(cache: TagLocaleCache | None, tag: str) -> str | None:
    if cache is None:
        return None
    try:
        return cache.get(tag)
    except (sqlite3.Error, OSError):
        _logger.warning("Tag translation cache read failed for %r", tag, exc_info=True)
        return None


def _cache_set(cache: TagLocaleCache | None, tag: str, zh: str) -> None:
    if cache is None:
        return
    try:
        cache.set(tag, zh)
    except (sqlite3.Error, OSError):
        _logger.warning("Tag translation cache write failed for %r", tag, exc_info=True)


def _safe_translate(translator: Callable[[str], str], tag: str) -> str:
    try:
        return translator(tag)
    except Exception as error:  # noqa: BLE001
        _logger.warning("Tag translation provider failed for %r: %s", tag, error)
        return ""


def _translate_zh(canonical: str) -> tuple[str, bool]:
    normalized = canonical.casefold().replace(" ", "_")
    exact = TAG_TRANSLATIONS_ZH.get(normalized)
    if exact:
        return exact, True
    tokens = [token for token in normalized.split("_") if token]
    translated = [TOKEN_TRANSLATIONS_ZH.get(token, "") for token in tokens]
    if tokens and all(translated):
        return "".join(translated), True
    return "", False


def _contains_cjk(value: str) -> bool:
    return any("\u3400" <= character <= "\u9fff" for character in value)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
