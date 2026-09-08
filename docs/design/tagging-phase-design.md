# 打標階段設計

LoRA 訓練集的第二階段：打標。承接 `Pic_dataset_tool` 的篩選成果。

日期：2026-09-08
狀態：已確認，待實作
實作位置：**`soda-prompt-hub`**（不是本專案）

## 分工

```
Pic_dataset_tool     匯入 → 分析 → 人工篩選 → 排除 → 裁切
                                ↓ 交出圖片清單 + 裁切框
soda-prompt-hub      打標 → 人工複核 → 縮放 → 匯出 dataset
```

`Pic_dataset_tool` 不建打標 UI。它唯一要新增的是「把篩選結果交給
`soda-prompt-hub`」——建立 dataset workspace、送出圖片清單與裁切框。

兩個專案的流程不同，各自保留，透過本機 HTTP 溝通（都只聽 127.0.0.1）。

## 匯出移到最後

原本 `Pic_dataset_tool` 的匯出是流程終點。現在改為：**打標完成後才匯出**，
圖片與 `.txt` 同時產出。縮放（長邊上限、16 倍數對齊）也移到那時候做。

連帶結論：標註綁 `content_hash`，不綁匯出後的編號。重跑分析、換尺寸重匯、
出 v1 v2 v3 對照，標註都不必重做。

## 目標模型

| | Anima | Krea 2 |
|---|---|---|
| 架構 | Cosmos-Predict2-2B 衍生 + Qwen-Image VAE | 12B MMDiT + Qwen-Image VAE |
| 文字編碼器 | Qwen3-0.6B-Base | Qwen3-VL-4B-Instruct |
| 標註型態 | **danbooru tag** | **自然語言** |

**兩者都是 LLM 級編碼器，沒有 CLIP 的 75 token 天花板。**300 token 完全可用。

初版規劃曾以 CLIP 的 75 token 為前提警告「300 會被截斷」——那個警告對這兩個
目標不成立。SD1.5／SDXL 才受此限制（kohya-ss 官方文件建議「非必要就用 75」，
因為底模是在 75 token 下預訓練的）。

兩種標註**並存**，匯出時擇一——同一批篩選成果不重做，但標註成本分開付。

## 打標模式

模式名稱＝這個 LoRA 要學的東西，**因此那部分不寫進 caption**（不描述的常數會被
trigger word 吸收）。

| 模式 | 省略 | 描述 |
|---|---|---|
| A 通用 | 無 | 全部 |
| B 肖像 | 臉部五官 | 體型、動作、場景、光線、構圖、服裝 |
| C 服裝 | 服裝 | 人物、場景、光線、構圖、動作 |
| D 風格 | 畫風、色調、光線 | 主體、服裝、場景、構圖、動作 |

初版有五個模式，「人物」與「肖像」的界線說不清楚而砍掉一個。

**這條原則沒有實驗證據。**研究找不到任何針對 LoRA 的「省略常數 vs 全部描述」
對照實驗，它是各家指南反覆轉述形成的共識。唯一被實測討論過的是「要不要 caption」
本身（有人主張純 trigger word 無 caption 更好，資深使用者反駁說那樣訓出來很僵硬）。

## Trigger word

開關＋輸入框，**輸入框的標籤隨模式改變**：

| 模式 | 輸入框標籤 | 範例 |
|---|---|---|
| A 通用 | 隱藏 | — |
| B 肖像 | 人物稱呼 | `miru` |
| C 服裝 | 服裝名稱 | `redqipao` |
| D 風格 | 風格名稱 | `XXXX style` |

同一個輸入框在不同模式下代表不同東西，用固定文案會誤導。

### 兩種標註的插入方式不同

```
自然語言   替換主詞
           「一名女性坐在椅子上」→「miru 坐在椅子上」

danbooru   前置 trigger tag，但保留結構性標籤
           miru, 1girl, solo, sitting, white dress, indoors
                 ↑ 不因為有 miru 就刪掉
```

`1girl`／`solo` 是結構性標籤，模型靠它們理解畫面有幾個人，刪掉會壞事。

## 媒材標籤：兩個目標都要寫

caption 要包含 `photo`／`realistic` 這類媒材標籤。

初版建議「不寫」，理由是「媒材是雜訊，不該跟角色綁定」。**那個推論方向搞反了。**

`clio-style-preview`（KREA 2 Turbo 的 397 個風格提示詞庫）的 README 說
「keep it medium-silent — every style claims its own medium」，但那講的是
**推論時的 subject 文字**，不是訓練時的 caption。兩者方向相反：

- **推論時**不提媒材，因為媒材由 style 提示詞決定
- **訓練時**要提媒材，**這樣媒材才會跟 trigger word 分離**，之後 style 提示詞才蓋得過去

若訓練時省略 `photo`，照片感會被 trigger word 吸收成角色的固有屬性，
之後想用插畫風格畫這個人會被 LoRA 本身的照片感拖住。**寫進去是為了讓它可以被覆蓋。**

Krea 2 **不是寫實模型，是風格模型**——媒材完全由提示詞決定。使用者實測：
「krea2 用 danbooru tag 不標質量詞會直接產生二次元風格」。Anima 更明確，
官方文件寫著它「不是為寫實而生」。所以兩個目標都需要媒材標籤。

## 進階開關

12 個（原本 14 個：`加入 tag 標籤輔助` 已刪——在 tag 模式下是套套邏輯；`指定人物稱呼` 已提升為核心設定，見下）。

**依模式動態顯示**，tag 模式下隱藏這三個：

| 開關 | 為何在 tag 模式下無意義 |
|---|---|
| 避免元短語 | tag 沒有句子，談不上「此圖像顯示」這種贅語 |
| 避免模糊描述 | 同上 |
| 不使用委婉艱深詞彙 | 同上 |

擺一個按了沒反應的開關，比沒有那個開關更糟——使用者會以為設定生效了。

其餘 9 個兩種模式都適用（danbooru 有對應 tag：`from above`、`depth_of_field`、
`rating:explicit`）。

原列表的 `指定人物稱呼` 已提升為核心設定（見上節），不再是普通開關——
它是整個「省略常數」策略的支點。

## Tagger：換成真人照片訓練的模型

**現況**：`soda-prompt-hub` 用 `SmilingWolf/wd-swinv2-tagger-v3`。

**問題**：所有 WD tagger（v2、v3 全系列）**只在 Danbooru 訓練，那是動漫／插畫資料**。
使用者的素材全是真人寫真。沒有任何 model card 量化過在真實照片上的衰減，
但 DeepGHS 團隊**專門為此另訓了一套**——一個團隊為同一件事另做一套模型，
本身就是最清楚的訊號。

**改用**：`deepghs/idolsankaku-swinv2-tagger-v1`

```
idolsankaku-swinv2-tagger-v1    F1 0.6161   P=R 閾值 0.3094   87.5M 參數
idolsankaku-eva02-large-v1      F1 0.6017   P=R 閾值 0.4985   0.3B 參數
```

model card 明寫 "trained on a human annotated dataset of real world photos"，
資料來源是 IdolSankaku（真人／寫真圖板）。**與 WD v3 的推論程式碼相容**，
drop-in 替換：同樣的 ONNX、同樣的 `selected_tags.csv`、同樣的閾值機制。

選 swinv2 而非 eva02-large 的理由：**小的分數反而高**（0.6161 vs 0.6017），
參數只有 1/3.5。同團隊同資料集同評測，沒理由為更差的分數多載 3.5 倍的模型。

### 閾值必須跟著改

```
現行        general 0.35     ← WD 的社群慣例
idolsankaku-swinv2 的 P=R    0.3094
```

換模型不換閾值，等於用別人的刻度讀自己的儀表。

**不設使用次數下限**——Anima 的編碼器是 Qwen3-0.6B，自然語言與冷門標籤都理解得了。

### 不建議的選項

`Bercraft/wd-v1-4-vit-tagger-v2` 是 SmilingWolf 2023 年 v2 的**原樣轉載**
（其頁面寫著 "Duplicated from"），權重完全相同、無任何改進，且比現用的 v3 更舊。

## Tag 詞彙表與中文對照

**現況**：`tag_completions.py` 已下載 `newtextdoc1111/danbooru-tag-csv`（釘死 revision），
`tag_locale.py` 有手工維護的 `TAG_TRANSLATIONS_ZH` **86 條**。

86 條遠不夠——每個專案會產出幾百個不同的 tag，六個專案加起來可能上千。

**做法**：按需翻譯 + 快取。遇到沒有中文的 tag 才呼叫 區網內的本機模型
翻一次，存進 SQLite，之後永遠命中。

不預先批量翻譯的理由：詞彙表有 187k 個 tag，99% 用不到；而且很多 tag 的中文
需要語境判斷，批量機翻品質很差。

### `PYU224/tagdb-updater` 沒有中文

使用者原本指定從這個 repo 拉取並「補充中文別名」。**它沒有中文欄位**——
只有日文（覆蓋率 68.2%），而且 README 自承一個已知 bug：Danbooru 的
`other_names` 偶爾混進中文／韓文別名被誤判成日文（實際抓到 `simple background`
的「日文」寫著簡體的「简单背景」）。

其餘規格良好：MIT、每週自動更新、`dist/` 直接提供預產檔案、單檔 `curl` 可取、
Danbooru 原生分類編號（0=general、1=artist、3=copyright、4=character、5=meta）、
每個 tag 附使用次數。

風險：repo 很年輕（2026-07-24 建立）、2 星、單一維護者。好在 MIT 且輸出是靜態
CSV，最壞情況自行 fork。

## 輸出

**kohya 標準的同名 sidecar**：

```
dataset/
  角色名_krea2_v1/
    001.jpg
    001.txt            ← 自然語言
    manifest.json
  角色名_anima_v1/
    001.jpg            ← 同一批圖，重複一份
    001.txt            ← danbooru tag
    manifest.json
```

**各自完整一份，圖片重複。**磁碟便宜，而「一個資料夾丟進去就能跑」的價值遠高於
省那 300MB。而且兩個目標的長邊上限可能不同，圖片本來就未必一樣。

`manifest.json` 記錄完整脈絡：原始檔名、`content_hash`、兩種標註、打標模式、
進階開關狀態、目標模型、trigger word。`.txt` 只有最終結果，出問題無從追溯是
哪個設定產生的；manifest 讓結果可重現。

## 人工複核

**全部生成後逐張改**，批次大小可選（例如 50 張一批）。只有單張完全不滿意時
才單獨重新生成。

英文可編輯、中文對照唯讀且隨輸入即時更新。使用者校對的是英文——**校對什麼就是
輸出什麼**，沒有「改中文再機器翻譯成英文」那個會漂移的中間層。

`soda-prompt-hub` 已有 `caption_status: draft / reviewed` 與 `/review` 端點。

## 已存在、不需重做

| 需求 | 已實作於 `soda-prompt-hub` |
|---|---|
| 打標型態 anima／krea2 | `CaptionProfile = Literal["anima", "krea2"]` |
| Krea2 自然語言生成 | `local_model.draft_krea2_caption()` |
| Anima tag 格式化 | `normalize_tag_draft()` |
| 英文輸出強制 | `"最终 caption 必须使用英文"` 驗證 |
| 是否覆蓋現有資料 | `overwrite_existing` |
| 人工複核狀態 | `caption_status`、`/review` |
| 匯出與版本 | `/export`、`/exports`、`/preflight`、快照 |
| tag 自動補全 | `tag_completions.py` |
| 中文對照 | `tag_locale.localize_tags()` |
| 批次 tag | `/bulk-tags/apply`、`/bulk-tags/preview` |
| 自定義 API 介面 | 已支援多種，不再使用 LM Studio |

`local_model.py` 的 `DEFAULT_LM_STUDIO_URL = "http://127.0.0.1:1234/v1"` 是過時
預設值——實際走自定義 API 介面指向使用者自己設定的模型連線。

## 已刪除的需求

| 項目 | 原因 |
|---|---|
| 過濾強度設置 | 使用者決定不需要 |
| 打標模式「人物」 | 與「肖像」界線說不清 |
| 進階開關「加入 tag 標籤輔助」 | tag 模式下是套套邏輯 |
| tag 使用次數下限 | Anima 的 Qwen3-0.6B 編碼器理解得了冷門標籤 |

## 步驟 1 的「清洗」不是新功能

使用者流程圖上的「清洗」＝縮小過大圖片 + 人工排除。兩者都已存在：排除已完成，
縮小在匯出時做。匯出移到最後之後，縮小自然落在正確位置。

不需要在打標前先縮小——打標餵給 vision 模型的是 768px 縮圖，原圖多大都一樣。

## 證據強度說明

本設計中，以下是**查證過的事實**：兩個目標模型的架構與編碼器、CLIP／T5 的 token
限制、IdolSankaku 的訓練資料與評分、`tagdb-updater` 的欄位與規模、
`Bercraft` repo 是轉載、`soda-prompt-hub` 的現有實作。

以下是**社群共識但無實驗證據**：「不描述要學的東西」原則、danbooru tag 的建議數量。

以下是**使用者的實作經驗**，優先於前兩者：Krea 2 是風格模型而非寫實模型、
krea2 用 danbooru tag 不標質量詞會產生二次元風格、媒材標籤要寫。

---

# 實作契約

前後端與各模組分開實作，這一節是唯一的耦合點。形狀以此為準，不得各自發明。

## 打標設定 payload

`POST /api/dataset-workspaces/{id}/caption` 與 `/bulk-tags/apply` 共用：

```jsonc
{
  "profile_id": "anima" | "krea2",     // 已存在
  "mode": "general" | "portrait" | "outfit" | "style",
  "trigger": "miru",                    // mode=general 時忽略；其餘為必填
  "media_tags": true,                   // 寫入 photo/realistic，兩個 profile 預設 true
  "options": {                          // 12 個進階開關，全部預設 false
    "age": false,
    "lighting": false,
    "light_source": false,
    "camera_angle": false,
    "action": false,
    "content_rating": false,
    "exclude_artwork_info": false,
    "avoid_meta_phrases": false,        // tag 模式下忽略
    "depth_of_field": false,
    "shot_type": false,
    "avoid_vague": false,               // tag 模式下忽略
    "plain_words": false                // tag 模式下忽略
  },
  "max_tokens": 300,
  "overwrite_existing": false,          // 已存在
  "caption_status": "draft" | "reviewed" // 已存在
}
```

### 模式與省略的對應（後端唯一真相）

| `mode` | 顯示名 | 省略 |
|---|---|---|
| `general` | 通用 | 無 |
| `portrait` | 肖像 | 臉部五官 |
| `outfit` | 服裝 | 服裝 |
| `style` | 風格 | 畫風、色調、光線 |

`trigger` 輸入框的標籤由 `mode` 決定：`portrait`→人物稱呼、`outfit`→服裝名稱、
`style`→風格名稱、`general`→隱藏。**前端不得自行硬編這張表**，
由 `GET /api/dataset-workspaces/caption-modes` 提供。

### tag 模式下失效的開關

`profile_id == "anima"` 時，`avoid_meta_phrases`、`avoid_vague`、`plain_words`
三項無意義（tag 沒有句子）。後端忽略它們，**前端隱藏而非停用**——
擺一個按了沒反應的開關比沒有更糟。

## 新端點

```
GET /api/dataset-workspaces/caption-modes
    → {"modes": [{"id": "portrait", "label": "肖像",
                  "omits": "臉部五官", "trigger_label": "人物稱呼"}, ...],
       "options": [{"id": "age", "label": "包含年齡資訊",
                    "profiles": ["anima", "krea2"]}, ...]}
    前端據此建 UI，不硬編。
```

## Trigger word 的插入

```
krea2（自然語言）  替換主詞：「一名女性坐在椅子上」→「miru sitting on a chair」
anima（tag）       前置 trigger tag，保留 1girl/solo 等結構性標籤：
                   miru, 1girl, solo, sitting, white dress, indoors
```

## Tagger 設定

```python
config.wd14_model_root  →  models_root / "tagger" / "idolsankaku-swinv2-tagger-v1"
general_threshold       0.35  →  0.3094      # 該模型的 P=R 閾值
character_threshold     0.85  →  維持
```

與 WD v3 的推論程式碼相容（同 ONNX、同 `selected_tags.csv`、同閾值機制），
`wd14.py` 的推論流程不需改，只換模型路徑與閾值。

**兩個 threshold 都不可由 caller 設定。** API payload、CLI 參數、前端輸入框
一律不提供 threshold 欄位，值只從選定模型的 `TaggerModelConfig` 取。

理由：threshold 是模型的校準值，不是通用旋鈕。0.35 是 WD 社群對 WD 系列的慣例，
套到 IdolSankaku 上等於拿別人的刻度量自己的模型。留一個可覆蓋的欄位，
預設值就會在某個入口被寫死，然後悄悄繞過校準——這件事已經發生過三次
（`workspace_routes.py`、`dataset_curation.py`、`cli.py`／`dataset_routes.py`）。

前端不是移除輸入框就算了，原位置要顯示唯讀的「目前模型 + 校準值」——
控制項直接消失會讓使用者以為功能壞了。

**舊模型路徑要保留可切換**——換 tagger 是有風險的改動，
留一條路能對照兩者的輸出差異。

## 中文翻譯擴充

```python
tag_locale.localize_tag(tag)
    1. 查 TAG_TRANSLATIONS_ZH（手工 86 條，最高優先）
    2. 查 SQLite 快取
    3. 都沒有 → 呼叫本機模型翻譯 → 寫入快取 → 回傳
```

本機模型走現有的自定義 API 介面（不是 `DEFAULT_LM_STUDIO_URL` 那個過時預設值）。
翻譯失敗時回傳原文，**不得讓翻譯失敗中斷打標流程**。
