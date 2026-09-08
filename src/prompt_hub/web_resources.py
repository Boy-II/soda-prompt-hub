from importlib.resources import files

_ASSET_ROOT = files("prompt_hub").joinpath("web_assets")


def read_web_asset(name: str) -> str:
    return _ASSET_ROOT.joinpath(name).read_text(encoding="utf-8")
