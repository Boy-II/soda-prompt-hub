from __future__ import annotations

import csv
import hashlib
import re
import sqlite3
from http import HTTPStatus
from pathlib import Path
from threading import Lock
from typing import IO, TYPE_CHECKING, Any, override
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, OpenerDirector, Request, build_opener

from prompt_hub.schema_migrations import record_schema_migration
from prompt_hub.tag_locale import localize_tag

if TYPE_CHECKING:
    from collections.abc import Mapping
    from http.client import HTTPMessage

    from prompt_hub.background_jobs import JobContext

TAG_DATA_REVISION = "fdf2772213f13d46bff60fc5ebd876e1a811a053"
TAG_DATA_URL = (
    "https://huggingface.co/datasets/newtextdoc1111/danbooru-tag-csv/resolve/"
    f"{TAG_DATA_REVISION}/danbooru_tags.csv"
)
TAG_DATA_FILENAME = "danbooru_tags.csv"
TAG_DATA_EXPECTED_SIZE = 1565830
TAG_DATA_EXPECTED_SHA256 = "a48e1e63b81e8e4fc3091c660fd763e9d05e19beea3b98d1ee78d00ed10ac9d3"
TAG_DOWNLOAD_JOB_TYPE = "tag_completions_download"
TAG_CHUNK_BYTES = 512 * 1024
TAG_DOWNLOAD_TIMEOUT = 60
TAG_USER_AGENT = "SodaPromptHub/1.0 tag-completions-installer"
MAX_DISPLAY_ALIASES = 6
_HAN_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_KANA_HANGUL_PATTERN = re.compile(r"[\u3040-\u30ff\uac00-\ud7af]")
TAG_COMPONENT_NAME = "danbooru_tags"
TAG_SCHEMA_VERSION = 1

_ALLOWED_ROOT_HOSTS = ("huggingface.co",)
_ALLOWED_HOST_SUFFIXES = (".hf.co", ".huggingface.co")

DANBOORU_TAGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS danbooru_tags (
    tag TEXT PRIMARY KEY COLLATE NOCASE,
    category INTEGER NOT NULL,
    post_count INTEGER NOT NULL,
    aliases_raw TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_danbooru_tags_count ON danbooru_tags(post_count DESC);

CREATE TABLE IF NOT EXISTS danbooru_tag_aliases (
    tag TEXT NOT NULL REFERENCES danbooru_tags(tag) ON DELETE CASCADE,
    alias TEXT NOT NULL COLLATE NOCASE,
    PRIMARY KEY (tag, alias)
);

CREATE INDEX IF NOT EXISTS idx_danbooru_tag_aliases_alias ON danbooru_tag_aliases(alias);
"""


class TagCompletionError(ValueError):
    pass


def _host_allowed(host: str) -> bool:
    clean = host.lower().rstrip(".")
    return clean in _ALLOWED_ROOT_HOSTS or any(
        clean.endswith(suffix) for suffix in _ALLOWED_HOST_SUFFIXES
    )


def assert_https_download_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not _host_allowed(parsed.hostname or ""):
        msg = f"下载地址不允许：{parsed.scheme}://{parsed.netloc}"
        raise TagCompletionError(msg)


class WhitelistedRedirectHandler(HTTPRedirectHandler):
    @override
    def redirect_request(
        self,
        req: Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> Request | None:
        parsed = urlsplit(newurl)
        if parsed.scheme != "https" or not _host_allowed(parsed.hostname or ""):
            detail = f"下载被重定向到不允许的地址：{parsed.scheme}://{parsed.netloc}"
            raise TagCompletionError(detail)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(TAG_CHUNK_BYTES)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _attach_tag_details(
    connection: sqlite3.Connection,
    candidates: list[dict[str, Any]],
) -> None:
    """Give every candidate the same tag-derived details, whatever matched it.

    A tag must look identical whether the user typed its name or one of its
    aliases, so display data is derived from the tag itself and never from the
    query. Chinese wording comes only from the curated tag_locale table; raw
    aliases stay labelled as aliases because they mix Japanese and Korean and
    are not translations.
    """
    if not candidates:
        return
    names = [str(item["tag"]) for item in candidates]
    placeholders = ",".join("?" for _ in names)
    grouped: dict[str, list[str]] = {}
    rows = connection.execute(
        f"SELECT tag, alias FROM danbooru_tag_aliases WHERE tag IN ({placeholders})",  # noqa: S608
        names,
    ).fetchall()
    for row in rows:
        alias = str(row["alias"]).strip()
        if alias:
            grouped.setdefault(str(row["tag"]), []).append(alias)
    for item in candidates:
        tag_name = str(item["tag"])
        localized = localize_tag(tag_name)
        item["display_tag"] = tag_name.replace("_", " ")
        item["translation_zh"] = str(localized["zh"]) if localized["known"] else ""
        ordered = sorted(grouped.get(tag_name, []), key=_alias_sort_key)
        item["aliases"] = ordered[:MAX_DISPLAY_ALIASES]


def _alias_sort_key(alias: str) -> tuple[int, str]:
    """Surface Han-only aliases first; they are the readable ones for this UI.

    This only orders the list. Han-only text can still be Japanese, so aliases
    are never presented as translations.
    """
    has_han = bool(_HAN_PATTERN.search(alias))
    has_kana_or_hangul = bool(_KANA_HANGUL_PATTERN.search(alias))
    if has_han and not has_kana_or_hangul:
        return (0, alias)
    if has_han or has_kana_or_hangul:
        return (1, alias)
    return (2, alias)


class TagCompletionStore:
    def __init__(
        self,
        database_path: Path,
        tag_root: Path,
        *,
        opener: OpenerDirector | None = None,
    ) -> None:
        self.database_path = database_path
        self.tag_root = tag_root
        self.opener = opener
        self._lock = Lock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(DANBOORU_TAGS_SCHEMA)
            record_schema_migration(
                connection,
                TAG_COMPONENT_NAME,
                TAG_SCHEMA_VERSION,
                "Initialize Danbooru tags schema",
            )
            connection.commit()

    def status(self) -> dict[str, Any]:
        self.initialize()
        with self.connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM danbooru_tags").fetchone()
            tag_count = int(row[0]) if row else 0
        file_exists = (self.tag_root / TAG_DATA_FILENAME).is_file()
        return {
            "installed": tag_count > 0,
            "tag_count": tag_count,
            "total_tags": tag_count,
            "file_exists": file_exists,
            "csv_exists": file_exists,
            "revision": TAG_DATA_REVISION,
            "source_url": TAG_DATA_URL,
            "license": "MIT",
        }

    def is_installed(self) -> bool:
        return bool(self.status()["installed"])

    def get_total_tags(self) -> int:
        return int(self.status()["tag_count"])

    def query(self, q: str, limit: int = 20) -> dict[str, Any]:
        max_limit = min(max(limit, 1), 50)
        if not self.is_installed():
            return {
                "installed": False,
                "query": q,
                "items": [],
                "results": [],
                "total_tags": 0,
                "tag_count": 0,
                "message": "Danbooru 标签数据尚未安装，请在创作台安装标签库",
            }

        raw = q.strip()
        if not raw:
            total = self.get_total_tags()
            return {
                "installed": True,
                "query": q,
                "items": [],
                "results": [],
                "total_tags": total,
                "tag_count": total,
            }

        norm = raw.replace(" ", "_").lower()
        seen_tags: set[str] = set()
        candidates: list[dict[str, Any]] = []

        with self.connect() as connection:
            # Tier 1: Tag prefix match
            prefix_rows = connection.execute(
                """
                SELECT tag, category, post_count
                FROM danbooru_tags
                WHERE tag LIKE ?
                ORDER BY post_count DESC
                LIMIT ?
                """,
                (f"{norm}%", max_limit),
            ).fetchall()

            for row in prefix_rows:
                tag_name = str(row["tag"])
                seen_tags.add(tag_name.lower())
                candidates.append(
                    {
                        "tag": tag_name,
                        "category": int(row["category"]),
                        "count": int(row["post_count"]),
                        "post_count": int(row["post_count"]),
                        "matched_alias": None,
                    }
                )

            # Tier 2: Tag substring match
            remaining = max_limit - len(candidates)
            if remaining > 0:
                substr_rows = connection.execute(
                    """
                    SELECT tag, category, post_count
                    FROM danbooru_tags
                    WHERE tag LIKE ? AND tag NOT LIKE ?
                    ORDER BY post_count DESC
                    LIMIT ?
                    """,
                    (f"%{norm}%", f"{norm}%", remaining),
                ).fetchall()
                for row in substr_rows:
                    tag_name = str(row["tag"])
                    if tag_name.lower() not in seen_tags:
                        seen_tags.add(tag_name.lower())
                        candidates.append(
                            {
                                "tag": tag_name,
                                "category": int(row["category"]),
                                "count": int(row["post_count"]),
                                "post_count": int(row["post_count"]),
                                "matched_alias": None,
                            }
                        )

            # Tier 3: Alias match
            remaining = max_limit - len(candidates)
            if remaining > 0:
                alias_rows = connection.execute(
                    """
                    SELECT a.tag, a.alias, t.category, t.post_count,
                           CASE WHEN a.alias LIKE ? THEN 1 ELSE 2 END AS alias_prio
                    FROM danbooru_tag_aliases a
                    JOIN danbooru_tags t ON t.tag = a.tag
                    WHERE a.alias LIKE ?
                    ORDER BY alias_prio ASC, t.post_count DESC
                    LIMIT ?
                    """,
                    (f"{raw}%", f"%{raw}%", remaining * 4),
                ).fetchall()
                for row in alias_rows:
                    tag_name = str(row["tag"])
                    matched_alias = str(row["alias"])
                    if tag_name.lower() not in seen_tags:
                        seen_tags.add(tag_name.lower())
                        candidates.append(
                            {
                                "tag": tag_name,
                                "category": int(row["category"]),
                                "count": int(row["post_count"]),
                                "post_count": int(row["post_count"]),
                                "matched_alias": matched_alias,
                            }
                        )
                        if len(candidates) >= max_limit:
                            break

            _attach_tag_details(connection, candidates)

        return {
            "installed": True,
            "query": q,
            "items": candidates,
            "results": candidates,
            "total_tags": self.get_total_tags(),
            "tag_count": self.get_total_tags(),
        }

    def import_csv(
        self,
        csv_path: Path,
        *,
        context: JobContext | None = None,
    ) -> int:
        self.initialize()
        tag_rows: list[tuple[str, int, int, str]] = []
        alias_rows: list[tuple[str, str]] = []

        with csv_path.open("r", encoding="utf-8", errors="replace") as stream:
            reader = csv.reader(stream)
            header = next(reader, None)
            if header is not None and (not header or header[0].strip().lower() != "tag"):
                stream.seek(0)
                reader = csv.reader(stream)
            for row in reader:
                if not row or len(row) < 3:
                    continue
                tag = row[0].strip()
                if not tag:
                    continue
                try:
                    category = int(row[1].strip())
                    count = int(row[2].strip())
                except ValueError:
                    continue
                aliases_raw = row[3].strip() if len(row) > 3 else ""
                tag_rows.append((tag, category, count, aliases_raw))

                if aliases_raw:
                    for alias_item in aliases_raw.split(","):
                        clean_alias = alias_item.strip()
                        if clean_alias and clean_alias.lower() != tag.lower():
                            alias_rows.append((tag, clean_alias))

        if context is not None:
            context.raise_if_cancelled()

        with self.connect() as connection:
            connection.execute("DELETE FROM danbooru_tag_aliases")
            connection.execute("DELETE FROM danbooru_tags")
            connection.executemany(
                """
                INSERT OR REPLACE INTO danbooru_tags (
                    tag, category, post_count, aliases_raw
                ) VALUES (?, ?, ?, ?)
                """,
                tag_rows,
            )
            connection.executemany(
                """
                INSERT OR IGNORE INTO danbooru_tag_aliases (
                    tag, alias
                ) VALUES (?, ?)
                """,
                alias_rows,
            )
            connection.commit()

        return len(tag_rows)

    def download_job(
        self,
        payload: Mapping[str, Any],
        context: JobContext,
    ) -> dict[str, Any]:
        del payload
        return download_danbooru_tags(
            tag_root=self.tag_root,
            database_path=self.database_path,
            context=context,
            opener=self.opener,
        )


def download_danbooru_tags(
    tag_root: Path,
    database_path: Path,
    context: JobContext,
    *,
    url: str = TAG_DATA_URL,
    expected_size: int = TAG_DATA_EXPECTED_SIZE,
    expected_sha256: str = TAG_DATA_EXPECTED_SHA256,
    opener: OpenerDirector | None = None,
) -> dict[str, Any]:
    tag_root.mkdir(parents=True, exist_ok=True)
    target = tag_root / TAG_DATA_FILENAME
    part = tag_root / f"{TAG_DATA_FILENAME}.part"
    assert_https_download_url(url)
    handle = opener or build_opener(WhitelistedRedirectHandler())
    offset = part.stat().st_size if part.is_file() else 0

    if offset >= expected_size and part.is_file():
        return _finalize_and_import(
            part=part,
            target=target,
            database_path=database_path,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
            context=context,
        )

    request = Request(
        url,
        headers={
            "User-Agent": TAG_USER_AGENT,
            "Range": f"bytes={offset}-",
        },
    )
    try:
        with handle.open(request, timeout=TAG_DOWNLOAD_TIMEOUT) as response:
            status = getattr(response, "status", HTTPStatus.OK)
            accepted = (
                HTTPStatus.OK,
                HTTPStatus.PARTIAL_CONTENT,
                HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE,
            )
            if status not in accepted:
                msg = f"标签数据下载失败：HTTP {status}"
                raise TagCompletionError(msg)
            headers = dict(getattr(response, "headers", {}) or {})
            content_length = headers.get("Content-Length", headers.get("content-length", ""))
            if status == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE:
                return _finalize_and_import(
                    part=part,
                    target=target,
                    database_path=database_path,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                    context=context,
                )
            if status == HTTPStatus.OK:
                offset = 0
                part.unlink(missing_ok=True)
            mode = "ab" if offset else "wb"
            expected_total = int(content_length) if str(content_length).isdigit() else expected_size
            with part.open(mode) as sink:
                while True:
                    context.raise_if_cancelled()
                    chunk = response.read(TAG_CHUNK_BYTES)
                    if not chunk:
                        break
                    sink.write(chunk)
                    offset += len(chunk)
                    offset_kb = offset // 1024
                    total_kb = expected_size // 1024
                    progress_msg = f"正在下载标签库（已下载 {offset_kb} KB / {total_kb} KB）…"
                    context.update(offset, expected_total, progress_msg)
    except HTTPError as error:
        msg = f"标签数据下载失败：服务返回 HTTP {error.code}"
        raise TagCompletionError(msg) from error
    except (URLError, OSError) as error:
        msg = f"标签数据下载中断：{error}"
        raise TagCompletionError(msg) from error

    context.update(offset, expected_size, "下载完成，正在校验完整性……")
    return _finalize_and_import(
        part=part,
        target=target,
        database_path=database_path,
        expected_size=expected_size,
        expected_sha256=expected_sha256,
        context=context,
    )


def _finalize_and_import(
    part: Path,
    target: Path,
    database_path: Path,
    expected_size: int,
    expected_sha256: str,
    context: JobContext,
) -> dict[str, Any]:
    if not part.is_file():
        raise TagCompletionError("标签数据下载未产生任何文件")
    size = part.stat().st_size
    if size != expected_size:
        part.unlink(missing_ok=True)
        msg = f"标签文件大小不符：下载得到 {size} 字节，期望 {expected_size} 字节"
        raise TagCompletionError(msg)
    digest = _sha256_file(part)
    if digest != expected_sha256:
        part.unlink(missing_ok=True)
        raise TagCompletionError("标签文件 SHA-256 校验失败，已清除不完整下载")

    part.replace(target)
    context.update(expected_size, expected_size, "正在将标签数据导入数据库……")
    store = TagCompletionStore(database_path, target.parent)
    tag_count = store.import_csv(target, context=context)
    context.update(expected_size, expected_size, f"标签数据导入成功，共 {tag_count} 个标签")
    return {
        "installed": True,
        "tag_count": tag_count,
        "total_tags": tag_count,
        "size_bytes": expected_size,
        "sha256": expected_sha256,
    }
