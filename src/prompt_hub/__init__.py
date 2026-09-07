"""Prompt Hub local knowledge service."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("prompt-hub")
except PackageNotFoundError:  # pragma: no cover - source folder without an installed package
    __version__ = "0+unknown"
