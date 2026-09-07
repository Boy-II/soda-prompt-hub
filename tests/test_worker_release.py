from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from prompt_hub import __version__


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
        manifest = bundle.read(f"{root}MANIFEST.sha256").decode("utf-8")
        for line in manifest.splitlines():
            expected, relative = line.split("  ", 1)
            assert hashlib.sha256(bundle.read(f"{root}{relative}")).hexdigest() == expected

        release = json.loads(bundle.read(f"{root}RELEASE.json"))
        assert release["worker_version"] == __version__
        assert release["protocol_version"] == "soda-compute-bridge-v2"
