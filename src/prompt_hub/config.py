from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TaggerModelConfig:
    id: str
    model_name: str
    relative_root: Path
    general_threshold: float
    character_threshold: float = 0.85


TAGGER_MODELS: dict[str, TaggerModelConfig] = {
    "idolsankaku-swinv2-tagger-v1": TaggerModelConfig(
        id="idolsankaku-swinv2-tagger-v1",
        model_name="deepghs/idolsankaku-swinv2-tagger-v1",
        relative_root=Path("tagger") / "idolsankaku-swinv2-tagger-v1",
        # Thresholds are model calibration, not global knobs; using the WD
        # community threshold on this model reads someone else's scale.
        general_threshold=0.3094,
    ),
    "wd-swinv2-tagger-v3": TaggerModelConfig(
        id="wd-swinv2-tagger-v3",
        model_name="SmilingWolf/wd-swinv2-tagger-v3",
        relative_root=Path("wd14") / "wd-swinv2-tagger-v3",
        general_threshold=0.35,
    ),
}
DEFAULT_TAGGER_MODEL_ID = "idolsankaku-swinv2-tagger-v1"


def _default_library_root() -> Path:
    """Choose a public default without disconnecting existing personal installs."""
    home = Path.home()
    public_root = home / "Documents" / "Soda Prompt Hub" / "prompt-library"
    legacy_root = home / "Documents" / "Codex" / "soda-person" / "prompt-library"

    if public_root.exists():
        return public_root
    if legacy_root.exists():
        return legacy_root
    return public_root


@dataclass(frozen=True, slots=True)
class Settings:
    library_root: Path
    database_path: Path
    git_sources_root: Path

    @classmethod
    def from_environment(cls) -> Settings:
        default_root = _default_library_root()
        library_root = Path(os.environ.get("PROMPT_HUB_LIBRARY_ROOT", default_root)).expanduser()
        database_path = Path(
            os.environ.get(
                "PROMPT_HUB_DATABASE",
                library_root / "database" / "prompt-library.sqlite",
            )
        ).expanduser()
        return cls(
            library_root=library_root,
            database_path=database_path,
            git_sources_root=library_root / "sources" / "git",
        )

    def ensure_directories(self) -> None:
        directories = (
            self.database_path.parent,
            self.git_sources_root,
            self.library_root / "sources" / "web",
            self.library_root / "sources" / "api",
            self.library_root / "sources" / "imports",
            self.oc_imports_root,
            self.library_root / "normalized",
            self.library_root / "private" / "personal-prompts",
            self.library_root / "private" / "adult-prompts",
            self.library_root / "private" / "characters",
            self.library_root / "test-results",
            self.result_images_root,
            self.comfy_results_root,
            self.thumbnails_root,
            self.library_root / "exports",
            self.dataset_exports_root,
            self.dataset_workspaces_root,
            self.project_dataset_sources_root,
            self.lora_projects_root,
            self.embedding_index_root,
            self.remote_nodes_root,
            self.workflow_profiles_root,
            self.imported_archives_root,
            self.tag_completions_root,
        )
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def tag_completions_root(self) -> Path:
        return self.library_root / "tags" / "danbooru"

    @property
    def thumbnails_root(self) -> Path:
        return self.library_root / "thumbnails"

    @property
    def oc_imports_root(self) -> Path:
        return self.library_root / "sources" / "imports" / "oc-manager"

    @property
    def web_sources_root(self) -> Path:
        return self.library_root / "sources" / "web"

    @property
    def result_images_root(self) -> Path:
        return self.library_root / "test-results" / "prompt-hub"

    @property
    def comfy_results_root(self) -> Path:
        return self.library_root / "test-results" / "comfyui-imports"

    @property
    def dataset_exports_root(self) -> Path:
        return self.library_root / "exports" / "datasets"

    @property
    def dataset_workspaces_root(self) -> Path:
        return self.library_root / "datasets" / "workspaces"

    @property
    def imported_archives_root(self) -> Path:
        return self.library_root / "datasets" / "imported-archives"

    @property
    def project_dataset_sources_root(self) -> Path:
        return self.library_root / "datasets" / "project-sources"

    @property
    def lora_projects_root(self) -> Path:
        return self.library_root / "lora-projects"

    @property
    def embedding_index_root(self) -> Path:
        return self.library_root / "indexes" / "embeddings"

    @property
    def remote_nodes_root(self) -> Path:
        return self.library_root / "remote-nodes"

    @property
    def workflow_profiles_root(self) -> Path:
        return self.library_root / "workflow-profiles"

    @property
    def models_root(self) -> Path:
        default_root = self.library_root.parent / "models"
        return Path(os.environ.get("PROMPT_HUB_MODELS_ROOT", default_root)).expanduser()

    @property
    def wd14_model_config(self) -> TaggerModelConfig:
        selected = os.environ.get("PROMPT_HUB_TAGGER_MODEL", DEFAULT_TAGGER_MODEL_ID)
        try:
            return TAGGER_MODELS[selected]
        except KeyError as error:
            allowed = ", ".join(sorted(TAGGER_MODELS))
            message = f"Unsupported tagger model: {selected}. Choose one of: {allowed}"
            raise ValueError(message) from error

    @property
    def wd14_model_root(self) -> Path:
        return self.models_root / self.wd14_model_config.relative_root

    @property
    def wd14_model_name(self) -> str:
        return self.wd14_model_config.model_name

    @property
    def wd14_general_threshold(self) -> float:
        return self.wd14_model_config.general_threshold

    @property
    def wd14_character_threshold(self) -> float:
        return self.wd14_model_config.character_threshold
