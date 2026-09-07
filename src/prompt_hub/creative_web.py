from __future__ import annotations

from prompt_hub.creative_web_layout import CREATIVE_HTML
from prompt_hub.web_resources import read_web_asset

__all__ = ["CREATIVE_HTML", "CREATIVE_SCRIPT", "CREATIVE_STYLES"]

CREATIVE_STYLES = f"""
<style>
{read_web_asset("creative.css")}</style>
"""

CREATIVE_SCRIPT = f"""
<script>
{read_web_asset("creative.js")}</script>
"""
