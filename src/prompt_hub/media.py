from __future__ import annotations

import tomllib
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from PIL import Image, ImageOps

if TYPE_CHECKING:
    from prompt_hub.config import Settings

_KISEGA_SOURCE_ID = "kisegaeningyou"
_CLIO_SOURCE_ID = "clio-style-preview"
_ANIMADEX_SOURCE_ID = "animadex"
_THUMBNAIL_SIZE = (640, 640)


def build_kisega_thumbnails(settings: Settings) -> tuple[int, int]:
    source_root = settings.git_sources_root / "Kisegaeningyou"
    target_root = settings.thumbnails_root / _KISEGA_SOURCE_ID
    generated = 0
    current = 0
    for source in sorted(source_root.glob("images*/*.png")):
        relative = source.relative_to(source_root)
        target = (target_root / relative).with_suffix(".webp")
        if target.is_file() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            current += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as image:
            thumbnail = ImageOps.exif_transpose(image).convert("RGB")
            thumbnail.thumbnail(_THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
            thumbnail.save(target, "WEBP", quality=82, method=6)
        generated += 1
    return generated, current


def resolve_media_path(
    settings: Settings,
    source_id: str,
    variant: str,
    relative_path: str,
) -> Path | None:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    request = _media_request(settings, source_id, variant, relative)
    if request is None:
        return None
    root, requested, allowed_suffix = request

    resolved_root = root.resolve()
    candidate = (resolved_root / requested).resolve()
    if not candidate.is_relative_to(resolved_root):
        return None
    if candidate.suffix.casefold() != allowed_suffix or not candidate.is_file():
        return None
    return candidate


def _media_request(
    settings: Settings,
    source_id: str,
    variant: str,
    relative: PurePosixPath,
) -> tuple[Path, Path, str] | None:
    if source_id == _KISEGA_SOURCE_ID and variant == "original":
        return settings.git_sources_root / "Kisegaeningyou", Path(*relative.parts), ".png"
    if source_id == _KISEGA_SOURCE_ID and variant == "thumbnail":
        return (
            settings.thumbnails_root / _KISEGA_SOURCE_ID,
            Path(*relative.parts).with_suffix(".webp"),
            ".webp",
        )
    if source_id == _CLIO_SOURCE_ID and variant in {"original", "thumbnail"}:
        return settings.git_sources_root / "clio-style-preview", Path(*relative.parts), ".jpg"
    if source_id == _ANIMADEX_SOURCE_ID and variant in {"original", "thumbnail"}:
        return _animadex_media_request(settings, relative)
    return None


def _animadex_media_request(
    settings: Settings,
    relative: PurePosixPath,
) -> tuple[Path, Path, str] | None:
    source_root = settings.git_sources_root / "AnimaDex"
    if relative.parts[:1] == ("catalogue",):
        return _animadex_data_root(source_root), Path(*relative.parts[1:]), ".webp"
    if relative.parts[:2] == ("samples", "images"):
        return source_root, Path(*relative.parts), ".webp"
    return None


def _animadex_data_root(source_root: Path) -> Path:
    data_root = source_root.parent / "animadex-data"
    config_path = source_root / "config.toml"
    if not config_path.is_file():
        return data_root
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return data_root
    configured = str(raw.get("paths", {}).get("data_dir", "")).strip()
    if not configured:
        return data_root
    candidate = Path(configured).expanduser()
    return candidate if candidate.is_absolute() else (source_root / candidate).resolve()
