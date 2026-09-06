from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from prompt_hub.database import EntryInput
from prompt_hub.importers import discover_sources

if TYPE_CHECKING:
    from prompt_hub.config import Settings
    from prompt_hub.database import PromptDatabase

MAX_CAPTURE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_REDIRECTS = 3
SAFETY_LEVELS = {"sfw", "suggestive", "adult", "explicit-adult"}
TEXT_TYPES = {
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/xml",
}
IMAGE_TYPES = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


@dataclass(frozen=True, slots=True)
class SitePolicy:
    cache_policy: str
    label: str


SITE_POLICIES = {
    "github.com": SitePolicy("cache_allowed", "GitHub"),
    "raw.githubusercontent.com": SitePolicy("cache_allowed", "GitHub Raw"),
    "civitai.com": SitePolicy("link_only", "Civitai"),
    "www.civitai.com": SitePolicy("link_only", "Civitai"),
    "civitai.red": SitePolicy("link_only", "Civitai 镜像"),
    "www.civitai.red": SitePolicy("link_only", "Civitai 镜像"),
    "openart.ai": SitePolicy("link_only", "OpenArt"),
    "www.openart.ai": SitePolicy("link_only", "OpenArt"),
    "prompthero.com": SitePolicy("link_only", "PromptHero"),
    "www.prompthero.com": SitePolicy("link_only", "PromptHero"),
    "promptomania.com": SitePolicy("link_only", "Promptomania"),
    "www.promptomania.com": SitePolicy("link_only", "Promptomania"),
}


class WebCaptureError(ValueError):
    pass


class WebCaptureNotFoundError(WebCaptureError):
    pass


class SourceProtectedError(WebCaptureError):
    pass


class SourceNotFoundError(WebCaptureError):
    pass


@dataclass(frozen=True, slots=True)
class FetchResult:
    final_url: str
    content_type: str
    body: bytes


FetchFunction = Callable[[str, int], FetchResult]


class WebCaptureService:
    def __init__(
        self,
        settings: Settings,
        database: PromptDatabase,
        *,
        fetcher: FetchFunction | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.fetcher = fetcher or _fetch_with_checked_redirects

    def capture(
        self,
        *,
        url: str,
        title: str,
        note: str,
        safety: str,
        license_name: str,
    ) -> dict[str, Any]:
        canonical_url, policy = validate_capture_url(url)
        clean_title = title.strip()[:300]
        clean_note = note.strip()[:6000]
        clean_license = license_name.strip()[:160] or "unknown"
        if safety not in SAFETY_LEVELS:
            raise WebCaptureError("资料分级无效")

        capture_id = f"web-{hashlib.sha256(canonical_url.encode()).hexdigest()[:24]}"
        capture_root = self.settings.web_sources_root / capture_id
        capture_root.mkdir(parents=True, exist_ok=True)
        content = ""
        media_kind = "none"
        cached_media_path = ""
        content_sha256 = ""
        content_type = ""
        final_url = canonical_url
        cached = False

        if policy.cache_policy == "cache_allowed":
            fetched = self.fetcher(canonical_url, MAX_CAPTURE_BYTES)
            try:
                final_url, final_policy = validate_capture_url(fetched.final_url)
            except WebCaptureError as error:
                raise WebCaptureError("网页重定向到了不可信站点") from error
            if final_policy.cache_policy != "cache_allowed":
                raise WebCaptureError("网页重定向到了不允许缓存的站点")
            if len(fetched.body) > MAX_CAPTURE_BYTES:
                raise WebCaptureError("网页内容过大，未保存")
            content_type = fetched.content_type.partition(";")[0].strip().lower()
            content_sha256 = hashlib.sha256(fetched.body).hexdigest()
            if content_type in IMAGE_TYPES:
                media_kind = "image"
                suffix = IMAGE_TYPES[content_type]
                media_path = capture_root / f"asset{suffix}"
                media_path.write_bytes(fetched.body)
                cached_media_path = media_path.relative_to(
                    self.settings.web_sources_root
                ).as_posix()
                content = clean_note or clean_title
            elif content_type in TEXT_TYPES:
                media_kind = "text"
                decoded = _decode_text(fetched.body, fetched.content_type)
                extracted_title, content = _extract_text(decoded, content_type)
                if not clean_title:
                    clean_title = extracted_title[:300]
                (capture_root / "content.txt").write_text(content, encoding="utf-8")
            else:
                raise WebCaptureError(f"不支持缓存这种内容类型：{content_type or 'unknown'}")
            cached = True
        else:
            content_sha256 = hashlib.sha256(
                json.dumps(
                    {
                        "url": canonical_url,
                        "title": clean_title,
                        "note": clean_note,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            content = clean_note or clean_title

        if not clean_title:
            clean_title = _fallback_title(canonical_url)
        now = datetime.now(UTC).isoformat(timespec="seconds")
        source_id = f"web-{_slug(urlsplit(canonical_url).hostname or 'source')}"
        external_id = capture_id
        manifest = {
            "schema": "soda-prompt-hub-web-capture-v1",
            "capture_id": capture_id,
            "source_id": source_id,
            "external_id": external_id,
            "url": canonical_url,
            "final_url": final_url,
            "title": clean_title,
            "note": clean_note,
            "safety": safety,
            "license": clean_license,
            "site_label": policy.label,
            "cache_policy": policy.cache_policy,
            "cached": cached,
            "media_kind": media_kind,
            "content_type": content_type,
            "content_sha256": content_sha256,
            "cached_media_path": cached_media_path,
            "captured_at": now,
        }
        (capture_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        self.database.upsert_source(
            source_id=source_id,
            name=f"网页摘录 · {policy.label}",
            source_type="web_capture",
            url=f"https://{urlsplit(canonical_url).hostname}",
            local_path=str(capture_root.parent),
            commit_hash=content_sha256,
            license_name=clean_license,
            notes=f"{policy.cache_policy} · 用户保存的网页资料",
        )
        searchable_content = "\n\n".join(value for value in (clean_note, content) if value).strip()
        self.database.upsert_entry(
            EntryInput(
                source_id=source_id,
                external_id=external_id,
                kind="visual" if media_kind == "image" else "prompt",
                title=clean_title,
                content=searchable_content,
                category="web-capture",
                safety=safety,
                language="mixed",
                source_path=str(capture_root / "manifest.json"),
                source_url=canonical_url,
                metadata={
                    "capture_id": capture_id,
                    "capture_policy": policy.cache_policy,
                    "cached": cached,
                    "media_kind": media_kind,
                    "content_sha256": content_sha256,
                    "cached_media_path": cached_media_path,
                    "captured_at": now,
                    "license": clean_license,
                },
            )
        )
        return manifest

    def list_captures(self) -> list[dict[str, Any]]:
        captures = []
        if not self.settings.web_sources_root.exists():
            return captures
        for manifest_path in self.settings.web_sources_root.glob("web-*/manifest.json"):
            try:
                value = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("schema") == "soda-prompt-hub-web-capture-v1":
                captures.append(value)
        return sorted(captures, key=lambda item: str(item.get("captured_at", "")), reverse=True)

    def resolve_media(self, capture_id: str) -> Path:
        if not re.fullmatch(r"web-[0-9a-f]{24}", capture_id):
            raise WebCaptureError("网页资料编号无效")
        root = (self.settings.web_sources_root / capture_id).resolve()
        manifest_path = root / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WebCaptureError("网页资料不存在") from error
        relative = str(manifest.get("cached_media_path", ""))
        candidate = (self.settings.web_sources_root / relative).resolve()
        if not relative or not candidate.is_file() or not candidate.is_relative_to(root):
            raise WebCaptureError("网页视觉资料不存在")
        expected = str(manifest.get("content_sha256", ""))
        actual = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if actual != expected:
            raise WebCaptureError("网页视觉资料完整性校验失败")
        return candidate

    def delete_source(
        self,
        source_id: str,
        *,
        purge_marks: bool = False,
        preset_source_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        preset_ids = (
            preset_source_ids
            if preset_source_ids is not None
            else {spec.source_id for spec in discover_sources(self.settings)}
        )
        if source_id in preset_ids:
            raise SourceProtectedError("内置资料库不能在页面删除")

        source = self.database.get_source(source_id)
        if source is None:
            raise SourceNotFoundError(f"资料来源不存在：{source_id}")
        if source.get("source_type") == "git":
            raise SourceProtectedError("内置资料库不能在页面删除")

        target_dirs = _collect_source_cache_dirs(self.settings, self.database, source_id)
        deleted_files = _remove_cache_dirs(
            target_dirs,
            self.settings.web_sources_root.resolve(),
            self.settings.git_sources_root.resolve(),
        )

        db_res = self.database.delete_source(source_id, purge_marks=purge_marks)
        name = source.get("name") or source_id
        deleted_entries = db_res["deleted_entries"]
        retained = db_res["retained_marks"]
        purged = db_res["purged_marks"]
        mark_desc = (
            f"彻底清除 {purged} 条人工标记" if purge_marks else f"安全保留 {retained} 条人工标记"
        )
        message = (
            f"已删除来源「{name}」及 {deleted_entries} 条条目，"
            f"清除 {deleted_files} 个缓存文件，{mark_desc}。"
        )
        return {
            "source_id": source_id,
            "deleted": True,
            "deleted_entries": deleted_entries,
            "deleted_files": deleted_files,
            "retained_marks": retained,
            "purged_marks": purged,
            "message": message,
        }

    def delete_capture(
        self,
        capture_id: str,
        *,
        purge_marks: bool = False,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"web-[0-9a-f]{24}", capture_id):
            raise WebCaptureError("网页资料编号无效")

        capture_root = (self.settings.web_sources_root / capture_id).resolve()
        manifest_path = capture_root / "manifest.json"

        source_id = ""
        if manifest_path.is_file():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    source_id = str(data.get("source_id", ""))
            except (OSError, json.JSONDecodeError):
                pass

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT source_id FROM entries WHERE external_id = ?",
                (capture_id,),
            ).fetchone()
            if row and not source_id:
                source_id = str(row[0])

        if not capture_root.exists() and not row:
            raise WebCaptureNotFoundError(f"网页资料不存在：{capture_id}")

        deleted_files = 0
        web_root = self.settings.web_sources_root.resolve()
        git_root = self.settings.git_sources_root.resolve()

        if (
            capture_root.is_dir()
            and capture_root.is_relative_to(web_root)
            and capture_root != web_root
            and not capture_root.is_relative_to(git_root)
        ):
            deleted_files = sum(1 for item in capture_root.rglob("*") if item.is_file())
            shutil.rmtree(capture_root, ignore_errors=True)

        if row:
            db_res = self.database.delete_entry(
                source_id=source_id or str(row[0]),
                external_id=capture_id,
                purge_marks=purge_marks,
            )
        else:
            db_res = {
                "deleted_entries": 0,
                "retained_marks": 0,
                "purged_marks": 0,
                "source_deleted": False,
            }

        retained = db_res["retained_marks"]
        purged = db_res["purged_marks"]
        mark_desc = (
            f"彻底清除 {purged} 条人工标记" if purge_marks else f"安全保留 {retained} 条人工标记"
        )
        return {
            "capture_id": capture_id,
            "source_id": source_id,
            "deleted": True,
            "deleted_entries": db_res["deleted_entries"],
            "deleted_files": deleted_files,
            "retained_marks": retained,
            "purged_marks": purged,
            "source_deleted": db_res.get("source_deleted", False),
            "message": f"已删除网页资料及对应缓存文件，{mark_desc}。",
        }


def _collect_source_cache_dirs(
    settings: Settings,
    database: PromptDatabase,
    source_id: str,
) -> set[Path]:
    target_dirs: set[Path] = set()
    if settings.web_sources_root.exists():
        for manifest_path in settings.web_sources_root.glob("web-*/manifest.json"):
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("source_id") == source_id:
                    target_dirs.add(manifest_path.parent.resolve())
            except (OSError, json.JSONDecodeError):
                continue

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT external_id FROM entries WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        for row in rows:
            candidate = (settings.web_sources_root / str(row[0])).resolve()
            if candidate.is_dir():
                target_dirs.add(candidate)
    return target_dirs


def _remove_cache_dirs(directories: set[Path], web_root: Path, git_root: Path) -> int:
    deleted_files = 0
    for directory in sorted(directories):
        if not directory.is_relative_to(web_root) or directory == web_root:
            continue
        if directory.is_relative_to(git_root):
            continue
        deleted_files += sum(1 for item in directory.rglob("*") if item.is_file())
        shutil.rmtree(directory, ignore_errors=True)
    return deleted_files


def validate_capture_url(url: str) -> tuple[str, SitePolicy]:
    raw = url.strip()
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as error:
        raise WebCaptureError("网页地址无效") from error
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme.lower() != "https":
        raise WebCaptureError("只允许保存 HTTPS 网页")
    if parsed.username or parsed.password or port not in {None, 443}:
        raise WebCaptureError("网页地址包含不安全的账号或端口")
    policy = SITE_POLICIES.get(host)
    if policy is None:
        raise WebCaptureError("这个站点尚未加入安全白名单")
    canonical = urlunsplit(("https", host, parsed.path or "/", parsed.query, ""))
    return canonical, policy


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ) -> None:
        return None


def _fetch_with_checked_redirects(url: str, limit: int) -> FetchResult:
    opener = build_opener(_NoRedirect)
    current = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        validate_capture_url(current)
        request = Request(
            current,
            headers={"User-Agent": "SodaPromptHub/0.1 web-capture"},
        )
        try:
            with opener.open(request, timeout=20) as response:
                content_length = response.headers.get("Content-Length", "")
                if content_length.isdigit() and int(content_length) > limit:
                    raise WebCaptureError("网页内容过大，未保存")
                body = response.read(limit + 1)
                if len(body) > limit:
                    raise WebCaptureError("网页内容过大，未保存")
                return FetchResult(
                    final_url=response.geturl(),
                    content_type=response.headers.get("Content-Type", ""),
                    body=body,
                )
        except HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise WebCaptureError(f"网页读取失败：HTTP {error.code}") from error
            location = error.headers.get("Location", "")
            if not location or redirect_count >= MAX_REDIRECTS:
                raise WebCaptureError("网页重定向次数过多") from error
            current = validate_capture_url(urljoin(current, location))[0]
        except URLError as error:
            raise WebCaptureError(f"网页连接失败：{error.reason}") from error
    raise WebCaptureError("网页重定向次数过多")  # pragma: no cover


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.parts: list[str] = []
        self._ignored = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "svg", "noscript"}:
            self._ignored += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg", "noscript"} and self._ignored:
            self._ignored -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        clean = " ".join(data.split())
        if not clean or self._ignored:
            return
        if self._in_title:
            self.title = f"{self.title} {clean}".strip()
        else:
            self.parts.append(clean)


def _extract_text(value: str, content_type: str) -> tuple[str, str]:
    if content_type not in {"text/html", "application/xhtml+xml"}:
        return "", value[:MAX_TEXT_CHARS].strip()
    parser = _TextExtractor()
    parser.feed(value[: MAX_TEXT_CHARS * 2])
    return parser.title, "\n".join(parser.parts)[:MAX_TEXT_CHARS].strip()


def _decode_text(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type, flags=re.IGNORECASE)
    encoding = match.group(1) if match else "utf-8"
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _fallback_title(url: str) -> str:
    parsed = urlsplit(url)
    return Path(parsed.path).name or parsed.hostname or "网页资料"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80]
