from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

MACOS_ONLY = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="Mac 更新器执行测试需要 macOS 和 /bin/zsh",
)


def test_release_metadata_stays_in_sync() -> None:
    repository = Path(__file__).resolve().parents[1]
    app_release = json.loads((repository / "RELEASE.json").read_text())
    worker_release = json.loads(
        (repository / "deploy" / "windows-worker" / "RELEASE.json").read_text()
    )

    assert app_release["product_version"] == worker_release["worker_version"]
    assert app_release["worker_version"] == worker_release["worker_version"]
    assert app_release["worker_protocol"] == worker_release["protocol_version"]


@MACOS_ONLY
def test_mac_updater_replaces_program_and_preserves_old_snapshot(tmp_path) -> None:
    result, install_root, program_backups, data_backups = _run_updater(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (install_root / "RELEASE.json").is_file()
    assert not (install_root / "old-marker.txt").exists()
    snapshots = list(program_backups.iterdir())
    assert len(snapshots) == 1
    assert (snapshots[0] / "old-marker.txt").read_text() == "keep-old"
    assert len(list(data_backups.iterdir())) == 1


@MACOS_ONLY
def test_mac_updater_restores_old_program_when_new_init_fails(tmp_path) -> None:
    result, install_root, program_backups, data_backups = _run_updater(
        tmp_path,
        fail_init=True,
    )

    assert result.returncode == 1
    assert (install_root / "old-marker.txt").read_text() == "keep-old"
    assert any(path.name.startswith("failed-1.1.0rc1") for path in program_backups.iterdir())
    assert len(list(data_backups.iterdir())) == 1


def _run_updater(
    tmp_path: Path,
    *,
    fail_init: bool = False,
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    repository = Path(__file__).resolve().parents[1]
    install_root = tmp_path / "installed"
    (install_root / "src" / "prompt_hub").mkdir(parents=True)
    (install_root / "pyproject.toml").write_text(
        '[project]\nname = "prompt-hub"\nversion = "1.0.0"\n'
    )
    (install_root / "old-marker.txt").write_text("keep-old")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/bin/sh
if [ "$1" = "run" ] && [ "$3" = "prompt-hub" ] && [ "$4" = "backup" ]; then
  shift 5
  mkdir -p "$1"
  exit 0
fi
if [ "$1" = "run" ] && [ "$3" = "prompt-hub" ] && [ "$4" = "init" ]; then
  [ "${FAKE_UV_FAIL_INIT:-0}" = "1" ] && exit 9
  exit 0
fi
if [ "$1" = "sync" ]; then
  exit 0
fi
exit 0
"""
    )
    fake_uv.chmod(0o755)
    program_backups = tmp_path / "program-backups"
    data_backups = tmp_path / "data-backups"
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "PROMPT_HUB_INSTALL_ROOT": str(install_root),
        "PROMPT_HUB_PROGRAM_BACKUP_ROOT": str(program_backups),
        "PROMPT_HUB_DATA_BACKUP_ROOT": str(data_backups),
        "PROMPT_HUB_UPDATE_ASSUME_YES": "1",
        "PROMPT_HUB_UPDATE_SKIP_START": "1",
        "PROMPT_HUB_UPDATE_SKIP_STOP": "1",
        "PROMPT_HUB_UV_BIN": str(fake_uv),
        "FAKE_UV_FAIL_INIT": "1" if fail_init else "0",
    }
    result = subprocess.run(  # noqa: S603
        ["/bin/zsh", str(repository / "deploy/mac/更新-Soda-Prompt-Hub.command")],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    return result, install_root, program_backups, data_backups
