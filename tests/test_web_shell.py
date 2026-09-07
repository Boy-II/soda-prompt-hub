from importlib.resources import files

from prompt_hub.web import INDEX_HTML, render_index_html


def test_web_shell_assembles_packaged_assets() -> None:
    asset_root = files("prompt_hub").joinpath("web_assets")
    template = asset_root.joinpath("index.html").read_text(encoding="utf-8")
    styles = asset_root.joinpath("base.css").read_text(encoding="utf-8")
    script = asset_root.joinpath("base.js").read_text(encoding="utf-8")

    assert "__PROMPT_HUB_BASE_STYLES__" in template
    assert "__PROMPT_HUB_BASE_SCRIPT__" in template
    assert "__PROMPT_HUB_BASE_STYLES__" not in INDEX_HTML
    assert "__PROMPT_HUB_BASE_SCRIPT__" not in INDEX_HTML
    assert styles in INDEX_HTML
    assert script in INDEX_HTML

    creative_styles = asset_root.joinpath("creative.css").read_text(encoding="utf-8")
    creative_script = asset_root.joinpath("creative.js").read_text(encoding="utf-8")
    assert creative_styles in INDEX_HTML
    assert creative_script in INDEX_HTML


def test_web_shell_keeps_device_name_escaping() -> None:
    rendered = render_index_html('</script><script>alert("x")</script>')

    assert "__PROMPT_HUB_DEVICE_NAME_HTML__" not in rendered
    assert "__PROMPT_HUB_DEVICE_NAME_JSON__" not in rendered
    assert "&lt;/script&gt;" in rendered
    assert r"\u003c/script\u003e\u003cscript\u003ealert" in rendered
