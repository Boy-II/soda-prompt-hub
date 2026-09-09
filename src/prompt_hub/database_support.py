from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    local_path TEXT NOT NULL,
    commit_hash TEXT NOT NULL DEFAULT '',
    license TEXT NOT NULL DEFAULT 'unknown',
    notes TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    negative_content TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    model_family TEXT NOT NULL DEFAULT '',
    safety TEXT NOT NULL DEFAULT 'sfw',
    language TEXT NOT NULL DEFAULT 'en',
    source_path TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT NOT NULL,
    rating REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_entries_kind ON entries(kind);
CREATE INDEX IF NOT EXISTS idx_entries_source ON entries(source_id);
CREATE INDEX IF NOT EXISTS idx_entries_category ON entries(category);
CREATE INDEX IF NOT EXISTS idx_entries_model_family ON entries(model_family);
CREATE INDEX IF NOT EXISTS idx_entries_safety ON entries(safety);

CREATE TABLE IF NOT EXISTS user_marks (
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    favorite INTEGER NOT NULL DEFAULT 0 CHECK (favorite IN (0, 1)),
    rating INTEGER CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
    note TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_user_marks_favorite ON user_marks(favorite);
CREATE INDEX IF NOT EXISTS idx_user_marks_rating ON user_marks(rating);

CREATE TABLE IF NOT EXISTS oc_imports (
    import_hash TEXT PRIMARY KEY,
    format_name TEXT NOT NULL,
    source_file TEXT NOT NULL,
    character_count INTEGER NOT NULL,
    world_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oc_characters (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    world TEXT NOT NULL DEFAULT '',
    gender TEXT NOT NULL DEFAULT '',
    age TEXT NOT NULL DEFAULT '',
    race TEXT NOT NULL DEFAULT '',
    affiliation TEXT NOT NULL DEFAULT '',
    identity TEXT NOT NULL DEFAULT '',
    residence TEXT NOT NULL DEFAULT '',
    faction TEXT NOT NULL DEFAULT '',
    birthplace TEXT NOT NULL DEFAULT '',
    avatar TEXT NOT NULL DEFAULT '',
    sheet_role TEXT NOT NULL DEFAULT 'pc',
    player_name TEXT NOT NULL DEFAULT '',
    story TEXT NOT NULL DEFAULT '',
    search_text TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    source_file TEXT NOT NULL,
    import_hash TEXT NOT NULL REFERENCES oc_imports(import_hash),
    source_created_at TEXT NOT NULL DEFAULT '',
    source_updated_at TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oc_characters_name ON oc_characters(name);
CREATE INDEX IF NOT EXISTS idx_oc_characters_world ON oc_characters(world);

CREATE TABLE IF NOT EXISTS oc_prompts (
    character_id TEXT NOT NULL REFERENCES oc_characters(character_id) ON DELETE CASCADE,
    prompt_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (character_id, prompt_id)
);

CREATE INDEX IF NOT EXISTS idx_oc_prompts_character ON oc_prompts(character_id);

CREATE TABLE IF NOT EXISTS oc_worlds (
    world_name TEXT PRIMARY KEY,
    world_id TEXT NOT NULL DEFAULT '',
    system TEXT NOT NULL DEFAULT 'generic',
    color TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL,
    source_file TEXT NOT NULL,
    import_hash TEXT NOT NULL REFERENCES oc_imports(import_hash),
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oc_lore (
    world_name TEXT PRIMARY KEY,
    search_text TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    source_file TEXT NOT NULL,
    import_hash TEXT NOT NULL REFERENCES oc_imports(import_hash),
    imported_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
    title,
    content,
    category,
    content='entries',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE VIRTUAL TABLE IF NOT EXISTS oc_characters_fts USING fts5(
    name,
    world,
    search_text,
    content='oc_characters',
    content_rowid='row_id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
    INSERT INTO entries_fts(rowid, title, content, category)
    VALUES (new.id, new.title, new.content, new.category);
END;

CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, content, category)
    VALUES ('delete', old.id, old.title, old.content, old.category);
END;

CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
    INSERT INTO entries_fts(entries_fts, rowid, title, content, category)
    VALUES ('delete', old.id, old.title, old.content, old.category);
    INSERT INTO entries_fts(rowid, title, content, category)
    VALUES (new.id, new.title, new.content, new.category);
END;

CREATE TRIGGER IF NOT EXISTS oc_characters_ai AFTER INSERT ON oc_characters BEGIN
    INSERT INTO oc_characters_fts(rowid, name, world, search_text)
    VALUES (new.row_id, new.name, new.world, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS oc_characters_ad AFTER DELETE ON oc_characters BEGIN
    INSERT INTO oc_characters_fts(oc_characters_fts, rowid, name, world, search_text)
    VALUES ('delete', old.row_id, old.name, old.world, old.search_text);
END;

CREATE TRIGGER IF NOT EXISTS oc_characters_au AFTER UPDATE ON oc_characters BEGIN
    INSERT INTO oc_characters_fts(oc_characters_fts, rowid, name, world, search_text)
    VALUES ('delete', old.row_id, old.name, old.world, old.search_text);
    INSERT INTO oc_characters_fts(rowid, name, world, search_text)
    VALUES (new.row_id, new.name, new.world, new.search_text);
END;
"""

_TOKEN_RE = re.compile(r"[\w-]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class EntryInput:
    source_id: str
    external_id: str
    kind: str
    title: str
    content: str
    negative_content: str = ""
    category: str = ""
    model_family: str = ""
    safety: str = "sfw"
    language: str = "en"
    source_path: str = ""
    source_url: str = ""
    metadata: Mapping[str, Any] | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _to_fts_query(query: str) -> str:
    tokens = _TOKEN_RE.findall(query.strip())
    return " AND ".join(f'"{token}"*' for token in tokens)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = json.loads(result.pop("metadata_json") or "{}")
    if "favorite" in result:
        result["favorite"] = bool(result["favorite"])
    return result


def _search_filters(
    *,
    kind: str,
    source_id: str,
    model_family: str,
    safety: str,
    favorites_only: bool,
    has_visual: bool = False,
    category: str = "",
    hair_color: str = "",
    eye_color: str = "",
) -> tuple[list[str], list[Any]]:
    filters: list[str] = []
    values: list[Any] = []
    for column, value in (
        ("e.kind", kind),
        ("e.source_id", source_id),
        ("e.model_family", model_family),
        ("e.safety", safety),
        ("e.category", category),
    ):
        if value:
            filters.append(f"{column} = ?")
            values.append(value)
    if favorites_only:
        filters.append("COALESCE(um.favorite, 0) = 1")
    if has_visual:
        filters.append(
            "(json_array_length(e.metadata_json, '$.image_paths') > 0 "
            "OR json_extract(e.metadata_json, '$.visual_path') != '' "
            "OR json_extract(e.metadata_json, '$.cached_media_path') != '')"
        )
    for metadata_path, value in (
        ("$.hair_colors", hair_color),
        ("$.eye_colors", eye_color),
    ):
        if value:
            filters.append(
                "EXISTS (SELECT 1 FROM json_each(e.metadata_json, ?) WHERE json_each.value = ?)"
            )
            values.extend((metadata_path, value))
    return filters, values


def _mark_dict(
    *,
    source_id: str,
    external_id: str,
    favorite: bool,
    rating: int | None,
    note: str,
) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "external_id": external_id,
        "favorite": favorite,
        "user_rating": rating,
        "user_note": note,
    }


def _string(value: object, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    text = str(value).strip()
    return text or fallback


def _mapping_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _oc_character_summary(row: sqlite3.Row) -> dict[str, Any]:
    row_data = dict(row)
    story = row_data["story"]
    profile = json.loads(row_data["raw_json"])
    gallery = profile.get("gallery", [])
    return {
        "character_id": row_data["character_id"],
        "name": row_data["name"],
        "world": row_data["world"],
        "gender": row_data["gender"],
        "age": row_data["age"],
        "race": row_data["race"],
        "affiliation": row_data["affiliation"],
        "identity": row_data["identity"],
        "residence": row_data["residence"],
        "faction": row_data["faction"],
        "birthplace": row_data["birthplace"],
        "avatar": row_data["avatar"],
        "sheet_role": row_data["sheet_role"],
        "player_name": row_data["player_name"],
        "story": story,
        "story_excerpt": story[:500],
        "prompt_count": row_data.get("prompt_count", 0),
        "gallery_count": len(gallery) if isinstance(gallery, list) else 0,
        "source_file": row_data["source_file"],
        "source_updated_at": row_data["source_updated_at"],
        "imported_at": row_data["imported_at"],
    }
