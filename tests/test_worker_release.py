from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from prompt_hub import __version__


def _assert_windows_batch_newlines(payload: bytes) -> None:
    assert b"\r\n" in payload
    assert b"\n" not in payload.replace(b"\r\n", b"")


def test_windows_batch_sources_use_crlf() -> None:
    repository = Path(__file__).resolve().parents[1]
    worker_root = repository / "deploy" / "windows-worker"

    for path in sorted(worker_root.glob("*.bat")):
        _assert_windows_batch_newlines(path.read_bytes())


def test_windows_worker_release_is_versioned_verified_and_private_free(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[1]
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(repository / "scripts" / "build_windows_worker_release.py"),
            "--repository-root",
            str(repository),
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(result.stdout)
    archive = Path(report["archive"])

    assert report["version"] == __version__
    assert report["protocol_version"] == "soda-compute-bridge-v2"
    assert archive.is_file()
    assert report["archive_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()

    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        root = f"Soda-Prompt-Hub-Windows-Worker-{__version__}/"
        assert f"{root}0-首次配置.bat" in names
        assert f"{root}校验发行包.ps1" in names
        assert f"{root}MANIFEST.sha256" in names
        assert f"{root}LICENSE.txt" in names
        assert f"{root}worker-config.json" not in names
        for name in names:
            if name.endswith(".bat"):
                _assert_windows_batch_newlines(bundle.read(name))
        manifest = bundle.read(f"{root}MANIFEST.sha256").decode("utf-8")
        for line in manifest.splitlines():
            expected, relative = line.split("  ", 1)
            assert hashlib.sha256(bundle.read(f"{root}{relative}")).hexdigest() == expected

        release = json.loads(bundle.read(f"{root}RELEASE.json"))
        assert release["worker_version"] == __version__
        assert release["protocol_version"] == "soda-compute-bridge-v2"


def test_windows_worker_release_normalizes_zip_metadata(tmp_path) -> None:
    repository = Path(__file__).resolve().parents[1]
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            str(repository / "scripts" / "build_windows_worker_release.py"),
            "--repository-root",
            str(repository),
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    archive = tmp_path / f"Soda-Prompt-Hub-Windows-Worker-{__version__}.zip"

    with zipfile.ZipFile(archive) as bundle:
        assert {item.date_time for item in bundle.infolist()} == {(1980, 1, 1, 0, 0, 0)}
