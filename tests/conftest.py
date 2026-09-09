from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from prompt_hub.config import Settings

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    library_root = tmp_path / "prompt-library"
    active_settings = Settings(
        library_root=library_root,
        database_path=library_root / "database" / "test.sqlite",
        git_sources_root=library_root / "sources" / "git",
    )
    active_settings.ensure_directories()
    return active_settings


@pytest.fixture
def source_tree(settings: Settings) -> Settings:
    git_root = settings.git_sources_root

    clio = git_root / "clio-style-preview"
    clio.mkdir(parents=True)
    (clio / "styles.json").write_text(
        json.dumps(
            [
                {
                    "name": "Gothic Ink",
                    "prompt": "dark gothic ink illustration, cathedral shadows",
                    "section": "Illustration",
                }
            ]
        ),
        encoding="utf-8",
    )
    clio_gallery = clio / "gallery"
    clio_gallery.mkdir()
    (clio_gallery / "manifest.json").write_text(
        json.dumps(
            {
                "sections": {
                    "krea2": {
                        "images": [
                            {
                                "style": "Gothic Ink",
                                "thumb": "krea2/thumbs/Gothic Ink.jpg",
                            }
                        ]
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    clio_thumb = clio_gallery / "krea2" / "thumbs"
    clio_thumb.mkdir(parents=True)
    Image.new("RGB", (64, 48), "black").save(clio_thumb / "Gothic Ink.jpg")

    krea = git_root / "open-prompts"
    krea.mkdir()
    (krea / "modifiers.json").write_text(
        json.dumps(
            [
                {
                    "name": "styles",
                    "subcategories": [
                        {
                            "name": "gothic",
                            "modifiers": [{"id": 1, "name": "dramatic chiaroscuro"}],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    (krea / "presets.json").write_text(
        json.dumps(
            [
                {
                    "name": "krea",
                    "subcategories": [
                        {
                            "name": "glass",
                            "presets": [{"id": 1, "name": "glossy glass tubes"}],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    wildcards = git_root / "sd-wildcards" / "wildcards"
    wildcards.mkdir(parents=True)
    (wildcards / "dress.txt").write_text("black dress\n# comment\nred gown\n", encoding="utf-8")

    kisega = git_root / "Kisegaeningyou" / "images"
    kisega.mkdir(parents=True)
    (kisega / "adult.png.desc.txt").write_text(
        "black leotard, revealing clothes, underboob,",
        encoding="utf-8",
    )
    (kisega / "safe.png.desc.txt").write_text(
        "suit, glasses, collared shirt,",
        encoding="utf-8",
    )
    Image.new("RGB", (48, 64), "red").save(kisega / "adult.png")
    Image.new("RGB", (48, 64), "blue").save(kisega / "safe.png")

    animadex = git_root / "AnimaDex" / "samples"
    animadex.mkdir(parents=True)
    (animadex / "characters.csv").write_text(
        "character,copyright,trigger,core_tags,count,url\n"
        'test_hero,test_series,"test hero, test series",'
        '"1girl, blue eyes, silver hair, long hair, cape",42,'
        "https://example.com/test-hero\n",
        encoding="utf-8",
    )
    (animadex / "artists.csv").write_text(
        "artist,trigger,count,url\n"
        "test_artist,Test Artist,12,https://example.com/test-artist\n",
        encoding="utf-8",
    )
    character_thumbs = animadex / "images" / "characters" / "thumbs"
    artist_thumbs = animadex / "images" / "artists" / "thumbs"
    copyright_thumbs = animadex / "images" / "copyrights" / "thumbs"
    character_thumbs.mkdir(parents=True)
    artist_thumbs.mkdir(parents=True)
    copyright_thumbs.mkdir(parents=True)
    Image.new("RGB", (48, 64), "silver").save(
        character_thumbs / "test hero, test series.webp",
        "WEBP",
    )
    Image.new("RGB", (48, 64), "purple").save(artist_thumbs / "Test Artist.webp", "WEBP")
    Image.new("RGB", (64, 48), "navy").save(copyright_thumbs / "test_series.webp", "WEBP")
    return settings
