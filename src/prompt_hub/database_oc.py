from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any

from prompt_hub.database_support import (
    _mapping_items,
    _now,
    _oc_character_summary,
    _string,
    _to_fts_query,
)
from prompt_hub.oc_manager import OCImportBundle, character_search_text, lore_search_text


class DatabaseOCMixin:
    def connect(self) -> AbstractContextManager[sqlite3.Connection]:
        raise NotImplementedError

    def import_oc_manager(
        self,
        bundle: OCImportBundle,
        *,
        source_file: str,
        import_hash: str,
    ) -> dict[str, Any]:
        imported_at = _now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO oc_imports (
                    import_hash, format_name, source_file, character_count,
                    world_count, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(import_hash) DO UPDATE SET imported_at=excluded.imported_at
                """,
                (
                    import_hash,
                    bundle.format_name,
                    source_file,
                    len(bundle.characters),
                    len(bundle.worlds),
                    imported_at,
                ),
            )
            for character in bundle.characters:
                self._upsert_oc_character(
                    connection,
                    character,
                    source_file=source_file,
                    import_hash=import_hash,
                    imported_at=imported_at,
                )
            self._upsert_oc_worlds(
                connection,
                bundle,
                source_file=source_file,
                import_hash=import_hash,
                imported_at=imported_at,
            )
            self._upsert_oc_lore(
                connection,
                bundle,
                source_file=source_file,
                import_hash=import_hash,
                imported_at=imported_at,
            )
            connection.commit()
        return {
            "format": bundle.format_name,
            "characters_imported": len(bundle.characters),
            "worlds_imported": len(bundle.worlds),
            "lore_worlds_imported": len(bundle.lore),
            "source_file": source_file,
            "import_hash": import_hash,
            "stats": self.oc_stats(),
        }

    def _upsert_oc_character(
        self,
        connection: sqlite3.Connection,
        character: Mapping[str, Any],
        *,
        source_file: str,
        import_hash: str,
        imported_at: str,
    ) -> None:
        character_id = _string(character.get("id"))
        connection.execute(
            """
            INSERT INTO oc_characters (
                character_id, name, world, gender, age, race, affiliation, identity,
                residence, faction, birthplace, avatar, sheet_role, player_name, story,
                search_text, raw_json, source_file, import_hash, source_created_at,
                source_updated_at, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(character_id) DO UPDATE SET
                name=excluded.name,
                world=excluded.world,
                gender=excluded.gender,
                age=excluded.age,
                race=excluded.race,
                affiliation=excluded.affiliation,
                identity=excluded.identity,
                residence=excluded.residence,
                faction=excluded.faction,
                birthplace=excluded.birthplace,
                avatar=excluded.avatar,
                sheet_role=excluded.sheet_role,
                player_name=excluded.player_name,
                story=excluded.story,
                search_text=excluded.search_text,
                raw_json=excluded.raw_json,
                source_file=excluded.source_file,
                import_hash=excluded.import_hash,
                source_created_at=excluded.source_created_at,
                source_updated_at=excluded.source_updated_at,
                imported_at=excluded.imported_at
            """,
            (
                character_id,
                _string(character.get("name")),
                _string(character.get("world")),
                _string(character.get("gender")),
                _string(character.get("age")),
                _string(character.get("race")),
                _string(character.get("affiliation")),
                _string(character.get("identity")),
                _string(character.get("residence")),
                _string(character.get("faction")),
                _string(character.get("birthplace")),
                _string(character.get("avatar")),
                _string(character.get("sheetRole"), "pc"),
                _string(character.get("playerName")),
                _string(character.get("story")),
                character_search_text(character),
                json.dumps(character, ensure_ascii=False, sort_keys=True),
                source_file,
                import_hash,
                _string(character.get("createdAt")),
                _string(character.get("updatedAt")),
                imported_at,
            ),
        )
        connection.execute("DELETE FROM oc_prompts WHERE character_id = ?", (character_id,))
        for prompt in _mapping_items(character.get("prompts")):
            text = _string(prompt.get("text"))
            if not text:
                continue
            prompt_id = _string(prompt.get("id")) or hashlib.sha256(text.encode()).hexdigest()[:16]
            connection.execute(
                """
                INSERT OR REPLACE INTO oc_prompts (
                    character_id, prompt_id, label, text, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    character_id,
                    prompt_id,
                    _string(prompt.get("label")),
                    text,
                    _string(prompt.get("createdAt")),
                ),
            )

    def _upsert_oc_worlds(
        self,
        connection: sqlite3.Connection,
        bundle: OCImportBundle,
        *,
        source_file: str,
        import_hash: str,
        imported_at: str,
    ) -> None:
        explicit_names: set[str] = set()
        for world in bundle.worlds:
            name = _string(world.get("name"))
            if not name:
                continue
            explicit_names.add(name)
            connection.execute(
                """
                INSERT INTO oc_worlds (
                    world_name, world_id, system, color, raw_json,
                    source_file, import_hash, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(world_name) DO UPDATE SET
                    world_id=excluded.world_id,
                    system=excluded.system,
                    color=excluded.color,
                    raw_json=excluded.raw_json,
                    source_file=excluded.source_file,
                    import_hash=excluded.import_hash,
                    imported_at=excluded.imported_at
                """,
                (
                    name,
                    _string(world.get("id")),
                    _string(world.get("system"), "generic"),
                    _string(world.get("color")),
                    json.dumps(world, ensure_ascii=False, sort_keys=True),
                    source_file,
                    import_hash,
                    imported_at,
                ),
            )
        inferred_names = (
            {_string(character.get("world")) for character in bundle.characters}
            - {""}
            - explicit_names
        )
        for name in inferred_names:
            connection.execute(
                """
                INSERT INTO oc_worlds (
                    world_name, raw_json, source_file, import_hash, imported_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(world_name) DO NOTHING
                """,
                (
                    name,
                    json.dumps({"name": name}, ensure_ascii=False),
                    source_file,
                    import_hash,
                    imported_at,
                ),
            )

    def _upsert_oc_lore(
        self,
        connection: sqlite3.Connection,
        bundle: OCImportBundle,
        *,
        source_file: str,
        import_hash: str,
        imported_at: str,
    ) -> None:
        world_ids = {
            _string(world.get("id")): _string(world.get("name"))
            for world in bundle.worlds
            if _string(world.get("id")) and _string(world.get("name"))
        }
        for raw_world_name, lore in bundle.lore.items():
            world_name = world_ids.get(raw_world_name, raw_world_name).strip()
            if not world_name:
                continue
            connection.execute(
                """
                INSERT INTO oc_lore (
                    world_name, search_text, raw_json, source_file, import_hash, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(world_name) DO UPDATE SET
                    search_text=excluded.search_text,
                    raw_json=excluded.raw_json,
                    source_file=excluded.source_file,
                    import_hash=excluded.import_hash,
                    imported_at=excluded.imported_at
                """,
                (
                    world_name,
                    lore_search_text(lore),
                    json.dumps(lore, ensure_ascii=False, sort_keys=True),
                    source_file,
                    import_hash,
                    imported_at,
                ),
            )

    def search_oc_characters(
        self,
        query: str = "",
        *,
        world: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        safe_limit = min(max(limit, 1), 50)
        world_filter = " AND c.world = ?" if world else ""
        values: list[Any] = [world] if world else []
        with self.connect() as connection:
            rows: list[sqlite3.Row] = []
            fts_query = _to_fts_query(query)
            if fts_query:
                try:
                    rows = connection.execute(
                        f"""
                        SELECT c.*, COUNT(p.prompt_id) AS prompt_count,
                               bm25(oc_characters_fts, 4.0, 2.0, 1.0) AS relevance
                        FROM oc_characters_fts
                        JOIN oc_characters c ON c.row_id = oc_characters_fts.rowid
                        LEFT JOIN oc_prompts p ON p.character_id = c.character_id
                        WHERE oc_characters_fts MATCH ? {world_filter}
                        GROUP BY c.row_id
                        ORDER BY relevance, c.name
                        LIMIT ?
                        """,
                        [fts_query, *values, safe_limit],
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
            if query and not rows:
                like_value = f"%{query.strip()}%"
                rows = connection.execute(
                    f"""
                    SELECT c.*, COUNT(p.prompt_id) AS prompt_count, 999.0 AS relevance
                    FROM oc_characters c
                    LEFT JOIN oc_prompts p ON p.character_id = c.character_id
                    WHERE (c.name LIKE ? OR c.world LIKE ? OR c.search_text LIKE ?)
                    {world_filter}
                    GROUP BY c.row_id
                    ORDER BY c.name
                    LIMIT ?
                    """,
                    [like_value, like_value, like_value, *values, safe_limit],
                ).fetchall()
            elif not query:
                where = "WHERE c.world = ?" if world else ""
                rows = connection.execute(
                    f"""
                    SELECT c.*, COUNT(p.prompt_id) AS prompt_count, 999.0 AS relevance
                    FROM oc_characters c
                    LEFT JOIN oc_prompts p ON p.character_id = c.character_id
                    {where}
                    GROUP BY c.row_id
                    ORDER BY c.imported_at DESC, c.name
                    LIMIT ?
                    """,
                    [*values, safe_limit],
                ).fetchall()
        return [_oc_character_summary(row) for row in rows]

    def get_oc_character(self, character_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM oc_characters WHERE character_id = ?",
                (character_id,),
            ).fetchone()
            if row is None:
                return None
            prompts = connection.execute(
                """
                SELECT prompt_id, label, text, created_at
                FROM oc_prompts WHERE character_id = ? ORDER BY created_at, prompt_id
                """,
                (character_id,),
            ).fetchall()
        result = _oc_character_summary(row)
        result["profile"] = json.loads(row["raw_json"])
        result["prompts"] = [dict(prompt) for prompt in prompts]
        return result

    def get_oc_character_prompts(self, character_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT prompt_id, label, text, created_at
                FROM oc_prompts WHERE character_id = ? ORDER BY created_at, prompt_id
                """,
                (character_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_oc_worlds(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT w.world_name, w.world_id, w.system, w.color,
                       COUNT(DISTINCT c.character_id) AS character_count,
                       CASE WHEN l.world_name IS NULL THEN 0 ELSE 1 END AS has_lore
                FROM oc_worlds w
                LEFT JOIN oc_characters c ON c.world = w.world_name
                LEFT JOIN oc_lore l ON l.world_name = w.world_name
                GROUP BY w.world_name
                ORDER BY w.world_name
                """
            ).fetchall()
        return [{**dict(row), "has_lore": bool(row["has_lore"])} for row in rows]

    def search_oc_lore(
        self,
        query: str = "",
        *,
        world: str = "",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        filters = []
        values: list[Any] = []
        if world:
            filters.append("l.world_name = ?")
            values.append(world)
        if query:
            filters.append("(l.world_name LIKE ? OR l.search_text LIKE ?)")
            like_value = f"%{query.strip()}%"
            values.extend((like_value, like_value))
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT l.*, w.world_id, w.system
                FROM oc_lore l
                LEFT JOIN oc_worlds w ON w.world_name = l.world_name
                {where}
                ORDER BY l.world_name
                LIMIT ?
                """,
                [*values, min(max(limit, 1), 20)],
            ).fetchall()
        return [
            {
                "world_name": row["world_name"],
                "world_id": row["world_id"] or "",
                "system": row["system"] or "generic",
                "lore": json.loads(row["raw_json"]),
                "source_file": row["source_file"],
                "imported_at": row["imported_at"],
            }
            for row in rows
        ]

    def oc_stats(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                "characters": connection.execute("SELECT COUNT(*) FROM oc_characters").fetchone()[
                    0
                ],
                "worlds": connection.execute("SELECT COUNT(*) FROM oc_worlds").fetchone()[0],
                "prompts": connection.execute("SELECT COUNT(*) FROM oc_prompts").fetchone()[0],
                "lore_worlds": connection.execute("SELECT COUNT(*) FROM oc_lore").fetchone()[0],
                "imports": connection.execute("SELECT COUNT(*) FROM oc_imports").fetchone()[0],
            }
