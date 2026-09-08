from importlib.resources import files

from prompt_hub.web import INDEX_HTML, render_index_html


def test_web_shell_assembles_packaged_assets() -> None:
    asset_root = files("prompt_hub").joinpath("web_assets")
    template = asset_root.joinpath("index.html").read_text(encoding="utf-8")
    styles = asset_root.joinpath("base.css").read_text(encoding="utf-8")
    script = asset_root.joinpath("base.js").read_text(encoding="utf-8")
    i18n_script = asset_root.joinpath("i18n.js").read_text(encoding="utf-8")

    assert "__PROMPT_HUB_BASE_STYLES__" in template
    assert "__PROMPT_HUB_BASE_SCRIPT__" in template
    assert "__PROMPT_HUB_I18N_SCRIPT__" in template
    assert "__PROMPT_HUB_BASE_STYLES__" not in INDEX_HTML
    assert "__PROMPT_HUB_BASE_SCRIPT__" not in INDEX_HTML
    assert "__PROMPT_HUB_I18N_SCRIPT__" not in INDEX_HTML
    assert styles in INDEX_HTML
    assert script in INDEX_HTML
    assert i18n_script in INDEX_HTML

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


def test_web_shell_exposes_persistent_interface_language_switcher() -> None:
    assert 'id="uiLanguageSelect"' in INDEX_HTML
    assert '<option value="zh-CN" data-i18n-ignore>简体中文</option>' in INDEX_HTML
    assert '<option value="zh-TW" data-i18n-ignore>繁體中文</option>' in INDEX_HTML
    assert '<option value="en" data-i18n-ignore>English</option>' in INDEX_HTML
    assert "soda-prompt-hub-ui-language" in INDEX_HTML
    assert "window.promptHubI18n" in INDEX_HTML
    assert "window.location.reload()" in INDEX_HTML
    assert "attributeFilter:['placeholder', 'aria-label', 'title', 'alt']" in INDEX_HTML
    assert "traditionalProtectedPatterns" in INDEX_HTML


def test_interface_language_preserves_user_defined_device_name() -> None:
    assert "data-remote-device-name data-i18n-ignore" in INDEX_HTML


def test_pr11_dataset_copy_has_three_language_entries() -> None:
    assert '"说明文字设置": "Caption Settings"' in INDEX_HTML
    assert '"训练内容": "Training Content"' in INDEX_HTML
    assert '"图片类型": "Image Type"' in INDEX_HTML
    assert '"二次元与插画": "Anime & Illustration"' in INDEX_HTML
    assert '"真人与摄影": "People & Photography"' in INDEX_HTML
