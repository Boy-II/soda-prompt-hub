from __future__ import annotations

# standalone-bundle: omit-start
import argparse
import json
import os
import sys
from pathlib import Path

from prompt_hub.windows_worker_core import ComfyUIClient, WindowsWorker, WorkerLock
from prompt_hub.windows_worker_support import (
    MAX_LORA_PREVIEW_BYTES,
    WORKER_BUILD_SHA256,
    LoraRootConfig,
    ModelRootConfig,
    WorkerConfig,
    WorkerError,
    _civitai_source_url,
    _copy_lora_previews,
    _find_lora_previews,
    _find_model_previews,
    _model_family,
    _release_channel,
)

# standalone-bundle: omit-end

__all__ = [
    "MAX_LORA_PREVIEW_BYTES",
    "WORKER_BUILD_SHA256",
    "ComfyUIClient",
    "LoraRootConfig",
    "ModelRootConfig",
    "WindowsWorker",
    "WorkerConfig",
    "WorkerError",
    "WorkerLock",
    "_civitai_source_url",
    "_copy_lora_previews",
    "_find_lora_previews",
    "_find_model_previews",
    "_model_family",
    "_release_channel",
    "build_parser",
    "main",
    "worker_lock_path",
]


def worker_lock_path(config: WorkerConfig) -> Path:
    if sys.platform == "win32":
        local_value = os.environ.get("LOCALAPPDATA")
        local_root = Path(local_value) if local_value else Path.cwd()
        return local_root / "PromptHub" / f"{config.worker_id}.lock"
    return config.bridge_root / "worker.lock"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prompt Hub Windows worker")
    parser.add_argument("--config", default="worker-config.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true")
    mode.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = WorkerConfig.load(Path(args.config).resolve())
        worker = WindowsWorker(config)
        with WorkerLock(worker_lock_path(config)):
            if args.self_test:
                print(json.dumps(worker.self_test(), ensure_ascii=False, indent=2))
                return 0
            if args.once:
                worker.recover_processing()
                return 0 if worker.run_once() else 3
            worker.run_forever()
    except KeyboardInterrupt:
        print("\nworker 已停止。", flush=True)
        return 0
    except WorkerError as error:
        print(f"worker 无法启动：{error}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
