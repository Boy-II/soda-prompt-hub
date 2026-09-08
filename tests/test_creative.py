from __future__ import annotations

import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from prompt_hub.api import create_app
from prompt_hub.creative import (
    CREATIVE_SCHEMA,
    CreativeStore,
    apply_iteration_suggestions,
    compile_prompt,
    export_project,
    iteration_context,
    next_iteration_values,
)
from prompt_hub.local_model import LocalModelError, organize_slots
from prompt_hub.web import INDEX_HTML


def sample_project() -> dict:
    return {
        "title": "黄昏图书馆",
        "brief_zh": "一位调查员在黄昏的旧图书馆寻找线索",
        "safety_mode": "adult",
        "target_profile": "anima",
        "slots": {
            "character": "1girl, silver eyes",
            "outfit": "victorian military uniform, leather gloves",
            "action": "holding an old letter",
            "composition": "medium shot, low angle",
            "scene": "old library, floating dust",
            "lighting": "golden hour, rim light",
            "style": "gothic ink illustration",
        },
        "slot_locks": {"character": True},
        "references": [{"source_id": "clio", "title": "Gothic Ink", "slot": "style"}],
        "generation": {"steps": 28, "seed": 42},
        "test_notes": "先测试半身构图",
    }


def test_creative_store_project_recipe_and_export(settings) -> None:
    store = CreativeStore(settings.database_path)
    store.initialize()
    created = store.create_project(sample_project())

    assert created["project_id"].startswith("project-")
    assert created["slots"]["character"] == "1girl, silver eyes"
    assert created["slot_locks"]["character"] is True
    assert store.list_projects()[0]["title"] == "黄昏图书馆"

    updated = store.update_project(
        created["project_id"],
        {"title": "黄昏档案", "slots": {**created["slots"], "action": "reading"}},
    )
    assert updated["revision"] == 2
    assert updated["slots"]["action"] == "reading"

    recipe = store.save_recipe(created["project_id"], "第一版", favorite=True)
    assert recipe["favorite"] is True
    assert recipe["snapshot"]["outputs"]["anima"]["positive"].startswith("masterpiece")
    stored_recipe = store.get_recipe(recipe["recipe_id"])
    assert stored_recipe is not None
    assert stored_recipe["name"] == "第一版"
    assert store.list_recipes()[0]["project_id"] == created["project_id"]

    exported = export_project(updated)
    assert exported["format"] == "soda-prompt-hub-creative-v1"
    assert exported["outputs"]["krea2"]["profile_id"] == "krea2"
    assert store.get_project("missing") is None
    assert store.get_recipe("missing") is None
    with pytest.raises(KeyError):
        store.update_project("missing", {})
    with pytest.raises(KeyError):
        store.save_recipe("missing", "nope")


def test_creative_store_migrates_legacy_projects_with_lineage(settings) -> None:
    legacy_schema = CREATIVE_SCHEMA.replace(
        "    lineage_json TEXT NOT NULL DEFAULT '{}',\n",
        "",
    )
    with sqlite3.connect(settings.database_path) as connection:
        connection.executescript(legacy_schema)
    store = CreativeStore(settings.database_path)
    store.initialize()
    with store.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(creative_projects)").fetchall()
        }
    assert "lineage_json" in columns
    assert store.create_project(sample_project())["lineage"] == {}


def test_next_iteration_values_builds_v2_and_v3_without_old_results() -> None:
    parent = {
        **sample_project(),
        "project_id": "project-root",
        "generation": {
            "steps": 28,
            "seed": 42,
            "result_images": ["old.png"],
            "result_assets": [{"asset_id": "old"}],
        },
    }
    asset = {"asset_id": "result-v1", "filename": "v1.png"}
    analysis = {
        "model": "vision-model",
        "summary_zh": "构图清晰",
        "observed_slots": {"character": "changed", "lighting": "strong rim light"},
        "improvements": ["加强轮廓光"],
        "reconstructed_prompts": {"anima_positive": "rim light"},
    }
    v2 = next_iteration_values(parent, asset, analysis)
    assert v2["title"] == "黄昏图书馆 · V2"
    assert v2["lineage"]["root_project_id"] == "project-root"
    assert v2["lineage"]["review"]["observed_slots"]["lighting"] == "strong rim light"
    assert v2["generation"] == {"steps": 28, "seed": 42, "result_images": []}

    v3 = next_iteration_values(
        {**v2, "project_id": "project-v2"},
        {"asset_id": "result-v2", "filename": "v2.png"},
        analysis,
    )
    assert v3["title"] == "黄昏图书馆 · V3"
    assert v3["lineage"]["iteration"] == 3
    assert v3["lineage"]["root_project_id"] == "project-root"
    assert v3["lineage"]["parent_project_id"] == "project-v2"


def test_iteration_context_and_suggestions_protect_existing_and_locked_slots() -> None:
    parent = sample_project()
    project = {
        **sample_project(),
        "slots": {**sample_project()["slots"], "lighting": ""},
        "lineage": {
            "iteration": 2,
            "parent_iteration": 1,
            "parent_project_id": "project-v1",
            "review": {
                "observed_slots": {
                    "character": "changed character",
                    "outfit": "changed outfit",
                    "lighting": "warm rim light",
                }
            },
        },
    }
    context = iteration_context(project, parent)
    lighting = next(item for item in context["changes"] if item["slot"] == "lighting")
    assert context["parent_available"] is True
    assert context["applicable_slots"] == ["lighting"]
    assert lighting["status"] == "removed"
    assert lighting["applicable"] is True

    applied = apply_iteration_suggestions(project)
    assert applied["slots"]["character"] == parent["slots"]["character"]
    assert applied["slots"]["outfit"] == parent["slots"]["outfit"]
    assert applied["slots"]["lighting"] == "warm rim light"
    assert applied["applied_slots"] == ["lighting"]
    assert "[迭代建议已应用] lighting" in applied["test_notes"]

    missing_parent = iteration_context(project, None)
    assert missing_parent["parent_available"] is False
    assert all(item["status"] == "unknown" for item in missing_parent["changes"])


def test_profiles_keep_adult_intent_and_warn_about_format() -> None:
    project = sample_project()
    anima = compile_prompt(project, "anima")
    assert "victorian military uniform" in anima["positive"]
    assert "nude" not in anima["negative"]
    assert anima["output_language"] == "en"
    assert anima["ready"] is True

    project["safety_mode"] = "sfw"
    project["slots"]["character"] = "银发女性"
    sfw = compile_prompt(project, "anima")
    assert "nsfw" in sfw["negative"]
    assert any("Booru" in warning for warning in sfw["warnings"])
    assert sfw["ready"] is False

    project["safety_mode"] = "suggestive"
    project["slots"]["style"] = ", ".join(f"tag-{index}" for index in range(12))
    krea = compile_prompt(project, "krea2")
    assert "Character: 银发女性" in krea["positive"]
    assert "explicit sexual acts" in krea["negative"]
    assert krea["output_language"] == "mixed"
    assert krea["ready"] is False
    assert any("标签堆叠" in warning for warning in krea["warnings"])

    english_krea = compile_prompt(sample_project(), "krea2")
    assert "Character: 1girl, silver eyes" in english_krea["positive"]
    assert "创作意图" not in english_krea["positive"]
    assert english_krea["output_language"] == "en"
    assert english_krea["ready"] is True

    with pytest.raises(ValueError, match="Unknown creative profile"):
        compile_prompt(project, "missing")


def test_empty_profile_has_actionable_warnings() -> None:
    result = compile_prompt({"slots": {}}, "krea2")
    assert len(result["warnings"]) == 2
    assert result["positive"] == ""
    assert result["ready"] is False


def test_creative_api_flow(settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "prompt_hub.api.list_local_models",
        lambda: [{"id": "gemma-4-12b-it-heretic", "name": "Gemma Heretic", "loaded": True}],
    )
    app = create_app(settings)
    with TestClient(app) as client:
        created_response = client.post("/api/creative/projects", json=sample_project())
        assert created_response.status_code == 201
        created = created_response.json()
        project_id = created["project_id"]

        assert client.get("/api/creative/projects").json()[0]["project_id"] == project_id
        assert client.get(f"/api/creative/projects/{project_id}").status_code == 200
        assert client.get("/api/creative/projects/missing").status_code == 404

        update = client.put(
            f"/api/creative/projects/{project_id}",
            json={"brief_zh": "更新后的想法", "target_profile": "krea2"},
        )
        assert update.status_code == 200
        assert update.json()["target_profile"] == "krea2"
        assert client.put("/api/creative/projects/missing", json={"title": "x"}).status_code == 404

        compiled = client.post(
            "/api/creative/compile",
            json={**sample_project(), "profile_id": "krea2"},
        )
        assert compiled.status_code == 200
        assert compiled.json()["profile_id"] == "krea2"

        recipe = client.post(
            "/api/creative/recipes",
            json={"project_id": project_id, "name": "可用版本", "favorite": True},
        )
        assert recipe.status_code == 201
        assert client.get("/api/creative/recipes").json()[0]["name"] == "可用版本"
        assert (
            client.post(
                "/api/creative/recipes",
                json={"project_id": "missing", "name": "x"},
            ).status_code
            == 404
        )

        exported = client.get(f"/api/creative/projects/{project_id}/export")
        assert exported.status_code == 200
        assert set(exported.json()["outputs"]) == {"anima", "krea2"}
        assert client.get("/api/creative/projects/missing/export").status_code == 404
        assert client.get("/api/local-models").json()["models"][0]["id"].startswith("gemma")


def test_local_assist_preserves_locked_slots(monkeypatch) -> None:
    def fake_request(_url, **_kwargs):
        return {
            "choices": [
                {
                    "message": {
                        "content": "```json\n"
                        + json.dumps(
                            {
                                "character": "changed",
                                "outfit": "black coat",
                                "action": "walking",
                            }
                        )
                        + "\n```"
                    }
                }
            ]
        }

    monkeypatch.setattr("prompt_hub.local_model._request_json", fake_request)
    result = organize_slots(
        brief="黄昏的调查员",
        slots={"character": "silver-haired investigator"},
        locks={"character": True},
        model="local-model",
        target_profile="anima",
    )
    assert result["suggested_slots"]["character"] == "silver-haired investigator"
    assert result["suggested_slots"]["outfit"] == "black coat"
    assert result["locked_slots"] == ["character"]


def test_local_models_unavailable_is_graceful(settings, monkeypatch) -> None:
    def unavailable():
        message = "offline"
        raise LocalModelError(message)

    monkeypatch.setattr("prompt_hub.api.list_local_models", unavailable)
    monkeypatch.setattr(
        "prompt_hub.api.organize_slots",
        lambda **_kwargs: (_ for _ in ()).throw(LocalModelError("not loaded")),
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/local-models").json()["available"] is False
        response = client.post(
            "/api/creative/assist",
            json={"brief": "test", "model": "missing", "target_profile": "anima"},
        )
        assert response.status_code == 503


def test_external_model_ui_keeps_existing_creative_actions() -> None:
    for removed_marker in (
        'id="externalModelSettings"',
        'id="externalModelBaseUrl"',
        'id="externalModelApiKey"',
        'id="discoverExternalModels"',
        'id="saveExternalModel"',
        'id="cancelExternalModelEdit"',
        "function editExternalModel",
    ):
        assert removed_marker not in INDEX_HTML

    for marker in (
        'id="openModelEndpointSettings"',
        "模型接入",
        'data-remote-view="endpoints"',
        'id="remoteEndpointsPanel"',
        'id="endpointApiKey"',
        'id="discoverEndpointModels"',
        'id="saveEndpointTop"',
        'id="saveEndpointBottom"',
        'id="endpointModelToolbar"',
        'id="endpointModelSearch"',
        'id="endpointModelSelectionCount"',
        'id="endpointDiscoveredModels" tabindex="0"',
        "max-height: min(52vh,560px)",
        "remote-endpoint-model-footer",
        'data-endpoint-model-select="all"',
        'data-endpoint-model-select="none"',
        "data-endpoint-model-name",
        "function updateEndpointModel",
        "function markEndpointDirty",
        "function endpointConfigurationIsVerified",
        "请先成功拉取模型列表",
        "请至少勾选一个模型",
        "端点信息有变化",
        "/api/model-endpoints",
        "function discoverEndpointModels",
        "state.discoveredEndpointModels.map(model=>({name:model.name,label:model.label||'',enabled:Boolean(model.enabled),supports_vision:Boolean(model.supports_vision)}))",
        "function runCreativeSourcing",
        "function uploadResultImage",
        "function exportDataset",
        "function analyzeResultAsset",
        'id="datasetTaggerMode"',
        'id="datasetTaggerModel"',
        'id="datasetTaggerHint"',
        'id="datasetWd14Thresholds"',
        'id="wd14TaggerMode"',
        'id="wd14TaggerModel"',
        'id="wd14TaggerHint"',
        'id="wd14Thresholds"',
        "function updateDatasetTaggerMode",
        "function updateWd14TaggerMode",
        "tagger==='model'",
        "$('#newCreativeProject').addEventListener",
        "$('#sendWorkflow').addEventListener",
    ):
        assert marker in INDEX_HTML

    for removed_marker in (
        'id="datasetGeneralThreshold"',
        'id="datasetCharacterThreshold"',
        'id="wd14GeneralThreshold"',
        'id="wd14CharacterThreshold"',
        "general_threshold:Number(",
        "character_threshold:Number(",
        "WD14 阈值必须在 0 到 1 之间",
    ):
        assert removed_marker not in INDEX_HTML

    # 校准值要显示。但不可写死在 HTML 里。
    # 切换 PROMPT_HUB_TAGGER_MODEL 后写死的字串会说谎。
    # 两个页面都必须有显示位并向 /api/tagger-config 取值。
    assert 'id="wd14Calibration"' in INDEX_HTML
    assert 'id="datasetWd14Calibration"' in INDEX_HTML
    assert INDEX_HTML.count("/api/tagger-config") >= 2
    assert "deepghs/idolsankaku-swinv2-tagger-v1" not in INDEX_HTML

    assert "可手工填写模型名称" not in INDEX_HTML

    assert "document.querySelector(`[data-endpoint-model-enabled=" not in INDEX_HTML
    assert "document.querySelector(`[data-endpoint-model-vision=" not in INDEX_HTML
    assert "document.querySelector(`[data-endpoint-model-label=" not in INDEX_HTML


def test_every_referenced_element_id_exists_in_page() -> None:
    """A $('#id') lookup returning null throws and kills every later listener."""
    rendered_ids = set(re.findall(r'id="([A-Za-z0-9_-]+)"', INDEX_HTML))
    referenced_ids = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", INDEX_HTML))
    assert referenced_ids
    assert not referenced_ids - rendered_ids


def _script_blocks() -> list[str]:
    return re.findall(r"<script>(.*?)</script>", INDEX_HTML, re.DOTALL)


def test_named_event_handlers_are_defined() -> None:
    """A listener naming an undefined function throws and kills the whole block."""
    blocks = _script_blocks()
    shared = set(re.findall(r"(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)", blocks[0]))
    for block in blocks:
        referenced = set(
            re.findall(r"addEventListener\(\s*'[^']+'\s*,\s*([A-Za-z_$][\w$]*)\s*[,)]", block)
        )
        declared = set(re.findall(r"(?:function|const|let|var)\s+([A-Za-z_$][\w$]*)", block))
        assert not referenced - declared - shared


def test_caption_rules_ui_renders_from_contract_not_hardcoded_enums() -> None:
    """模式与开关必须从后端契约来。前端硬编 enum 就会跟后端各走各的。"""
    assert "/api/dataset-workspaces/caption-modes" in INDEX_HTML
    assert 'id="datasetCaptionOptionList"' in INDEX_HTML

    # 两个容器在源码里必须是空的。内容由 JS 从契约填。
    # 一旦有人把选项写死进 HTML。这里就会失败。
    assert '<select id="datasetCaptionMode"></select>' in INDEX_HTML
    assert '<div class="dataset-caption-options" id="datasetCaptionOptionList"></div>' in INDEX_HTML

    # 开关 id 是契约的词汇。不该出现在页面源码里
    for option_id in ("avoid_meta_phrases", "depth_of_field", "content_rating", "plain_words"):
        assert option_id not in INDEX_HTML


def test_caption_settings_reach_both_queues() -> None:
    """WD14 与 Krea 2 两个队列都要带上打标规则。

    上一轮的缺陷正是只有模型分支套用了设置。WD14 分支整组漏掉。
    """
    assert INDEX_HTML.count("...captionSettings()") == 2


def test_krea2_draft_has_on_demand_chinese_reference() -> None:
    """逐张确认时按需翻译。不是打开就自动发请求。"""
    assert 'id="datasetDetailKrea2Locale"' in INDEX_HTML
    assert 'id="datasetDetailKrea2Translate"' in INDEX_HTML
    assert "/api/captions/localize" in INDEX_HTML
    # 换图要清掉上一张的译文。否则会被当成这张的意思
    assert "$('#datasetDetailKrea2Locale').value='';" in INDEX_HTML


def test_caption_switches_are_toggles_not_checkboxes() -> None:
    """进阶开关用滑动开关呈现。原生勾选框在这里一次要看 12 个。"""
    assert '.dataset-caption-rules input[type="checkbox"]' in INDEX_HTML
    assert "appearance: none" in INDEX_HTML
    assert "translateX(15px)" in INDEX_HTML


def test_trigger_word_is_optional() -> None:
    """触发词非必填。留空只提示后果。不挡下队列。"""
    assert "captionSettingsError" not in INDEX_HTML


def test_caption_presets_round_trip_through_local_storage() -> None:
    """设置要能存下来重复套用。存在浏览器本机。不写进工作区。"""
    assert "soda-caption-presets" in INDEX_HTML
    for element_id in (
        "datasetCaptionPreset",
        "datasetCaptionPresetName",
        "datasetCaptionPresetSave",
        "datasetCaptionPresetDelete",
    ):
        assert f'id="{element_id}"' in INDEX_HTML
    # 读不出来要当作没有预设。不能让整个规则面板跟着挂掉
    assert "function readCaptionPresets()" in INDEX_HTML


def test_revision_box_sits_under_the_translate_button() -> None:
    """修正意见跟着草稿走。两种用法共用一个输入框。"""
    assert 'id="datasetDetailKrea2Revision"' in INDEX_HTML
    assert 'id="datasetDetailKrea2Revise"' in INDEX_HTML
    assert "/api/captions/revise" in INDEX_HTML

    translate_at = INDEX_HTML.index('id="datasetDetailKrea2Translate"')
    revision_at = INDEX_HTML.index('id="datasetDetailKrea2Revision"')
    assert translate_at < revision_at


def test_revision_overwrites_draft_and_drops_stale_translation() -> None:
    """改写后旧译文对应的是改写前的草稿。留着会对不上。"""
    revise_block = INDEX_HTML[INDEX_HTML.index("datasetDetailKrea2Revise').addEventListener") :][
        :1400
    ]
    assert "$('#datasetDetailKrea2Draft').value=result.revised;" in revise_block
    assert "$('#datasetDetailKrea2Locale').value='';" in revise_block
    # 失败分支不可以碰草稿
    failure = revise_block[revise_block.index("catch(error)") :]
    assert "datasetDetailKrea2Draft').value=" not in failure


def test_endpoint_editor_says_whether_it_will_add_or_overwrite() -> None:
    """表单存过一次之后会停在编辑状态。

    只写「保存」的话。改成另一个服务的位址再存就会盖掉前一个而毫无提示。
    """
    assert "更新「${editing}」" in INDEX_HTML
    assert "'新增服务'" in INDEX_HTML
    assert 'id="newEndpoint"' in INDEX_HTML


def test_choosing_a_preset_starts_a_new_endpoint() -> None:
    """选预设代表要配置另一个服务。不该沿用上一次的编辑目标。"""
    handler_at = INDEX_HTML.index("#remoteEndpointPresets').addEventListener")
    handler = INDEX_HTML[handler_at : handler_at + 400]
    assert "clearEndpointForm()" in handler
    assert handler.index("clearEndpointForm()") < handler.index("endpointProvider")


def test_detail_navigation_uses_up_and_down_keys() -> None:
    """逐张检查用上下键换图。左右键留给游标。"""
    handler_at = INDEX_HTML.index("#datasetDetail').addEventListener('keydown'")
    handler = INDEX_HTML[handler_at : handler_at + 700]
    assert "ArrowUp" in handler
    assert "ArrowDown" in handler
    assert "ArrowLeft" not in handler
    assert "ArrowRight" not in handler


def test_arrow_keys_inside_editable_fields_do_not_change_image() -> None:
    """对话框里有五个可输入栏位。

    在里面按方向键是要移动游标或换行。换掉图片会让人正在打的字消失。
    """
    handler_at = INDEX_HTML.index("#datasetDetail').addEventListener('keydown'")
    handler = INDEX_HTML[handler_at : handler_at + 700]
    for tag in ("TEXTAREA", "INPUT", "SELECT"):
        assert tag in handler
    assert "isContentEditable" in handler
