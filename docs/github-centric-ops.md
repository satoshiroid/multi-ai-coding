# GitHub中心の運用ガイド（Discord intake × Actions実行）

アイデアの投入から成果物のPR化までを、ソース・APIキーともにGitHub側で管理する運用方式。

## 全体フロー

```
#hardware / #app (Discord forum, Mac常駐の intake bot)
   │ チャンネル → project_type を決定（コードのルックアップ。LLM分類なし）
   ▼ repository_dispatch (event_type: new-project) { requirement, project_type, thread_id }
GitHub Actions (.github/workflows/build.yml)
   ├─ setup     : project_type → runner を決定
   ├─ app       → ubuntu-latest        (hosted; CAD不要)
   └─ hardware  → [self-hosted, macOS] (Blender/FreeCAD/KiCAD + MCP)
   ▼ run_pipeline.py --auto-approve --project-type <type> --export ./out
   ▼ out/ を peter-evans/create-pull-request でPR化
   ▼ PRリンクを Discordスレッドへ通知（任意・Webhook）
```

- **HITLの最終承認 = PRレビュー/マージ**。内部ゲートは `--auto-approve` で通過。
- **種別は入口で確定**するため、分類用のLLM呼び出し/ジョブは存在しない。

## ワークフロー

| ファイル | トリガー | 役割 |
|---|---|---|
| `.github/workflows/ci.yml` | push / PR | mockでロジック全体テスト（pytest + app/hardware mock E2E）。**Secrets不要** |
| `.github/workflows/build.yml` | `repository_dispatch` / 手動 | 実パイプライン実行 → PR化。**Secrets使用** |

手動実行（Discord無しでの動作確認）:
GitHub → Actions → `build` → *Run workflow* で `requirement` と `project_type` を指定。

## セットアップ・チェックリスト（手動작업）

### 1. GitHub Secrets（リポジトリ Settings → Secrets and variables → Actions → **Secrets**）
- `ANTHROPIC_API_KEY` （Claude）
- `GEMINI_API_KEY`
- `OPENAI_API_KEY` （OpenAI を使う場合）
- `OLLAMA_BASE_URL`（任意）
- `DISCORD_WEBHOOK_URL`（任意・PRリンク通知用。intake用フォーラムchに作成したWebhook）

> LLMキーはここ（GitHub側）に置く。リポジトリのファイルには絶対に入れない。新しい
> キーは *New repository secret* から入力する（＝OpenAIキーの入力欄）。

#### LLMの割り当て（エージェント単位＝バーチャル社員）
各エージェントは**特性に合わせて別LLM**を割り当て済み（`config/settings.yaml` の `agents:`）。
1つが瞬間的に429/障害でも止まらないよう、全primaryにクロスプロバイダのfallback付き。

| エージェント | 既定 (primary) | fallback | 狙い |
|---|---|---|---|
| pm (L1) | **Gemini** 2.0 Flash | Claude Sonnet 4.6 | 高速・安価・大コンテキストの計画/分解 |
| senior (L2) | **Claude Opus** 4.8 | Claude Sonnet 4.6 | 最重要の判断/調停 |
| design (L3) | **GPT-4o** | Gemini 2.0 Flash | 創造的・多モーダルな意匠/UX |
| mecha (L3) | **Claude Opus** 4.8 | Claude Sonnet 4.6 | パラメトリックCADコードの精度 |
| circuit (L3) | **Claude Sonnet** 4.6 | GPT-4o-mini | 構造化EDA/BOM |
| software (L3) | **GPT-4o** | Claude Sonnet 4.6 | 汎用コード生成（ファーム/アプリ） |

（意匠のレンダリング画像レビューは別途 Gemini Vision を使用。）

#### 変えたいとき（優先順位 高→低）
1. **手動ドロップダウン** — Actions → `build` → *Run workflow* の `llm`（claude/gemini/openai）。
   選ぶとその実行は**全エージェントを1モデルに強制**（A/Bテスト用。`default`=下に従う）。
2. **エージェント単位の上書き** — Variables `LLM_AGENT_<NAME>_PROVIDER` / `_MODEL`
   （NAME ∈ PM/SENIOR/DESIGN/MECHA/CIRCUIT/SOFTWARE）。`settings.yaml` を編集せずGitで切替。
3. **`config/settings.yaml` の `agents:`** — 既定の配置（上表）。Git管理。
4. **tier単位** — Variables `LLM_L{1,2,3}_*` / `settings.yaml` の `tiers:`（fallback baseline）。

provider と model はセットで指定。providers: `anthropic`(Claude) / `gemini` / `openai` / `ollama`。

### 2. self-hosted ランナー（hardware用 / Macで実行）
- リポジトリ Settings → Actions → Runners → *New self-hosted runner*（macOS）。
- ラベルに **`self-hosted` と `macOS`** が付くこと（`build.yml` の routing がこの2ラベルで解決）。
- **LaunchDaemonではなくログインGUIセッションで起動**すること。Blender等GUIアプリ/MCPソケットに到達するため。
- hardware実行時は Blender（addonのTCPサーバ `localhost:9876`）と、必要に応じFreeCAD/KiCAD MCPが起動していること（`config/settings.yaml` の `mcp:` を参照）。

### 3. Discord intake bot（Mac常駐 / 薄いトリガー）
フォーラムチャンネルを2つ用意（例 `#hardware-projects` / `#app-projects`）。`.env` に:
```
DISCORD_BOT_TOKEN=...
HARDWARE_CHANNEL_ID=...      # → project_type "hardware"
APP_CHANNEL_ID=...           # → project_type "app"
GITHUB_REPO=satoshiroid/multi-ai-coding
GITHUB_DISPATCH_TOKEN=...    # repo(write) scope の PAT
DISCORD_WEBHOOK_URL=...      # 任意（GitHub Secretと同値）
```
起動:
```
python examples/run_dispatch_bot.py
```
常駐させるなら launchd の **LaunchAgent**（GUIセッション）に登録。

### 4. セキュリティ
- 旧 `git remote` URL に埋まっていた PAT（`ghp_...`）は**無効化＆再発行**する。
- `GITHUB_DISPATCH_TOKEN` は repo write 最小スコープのfine-grained PAT推奨。

## ローカルでの確認（Actionsに出す前）
```
# ロジック全体テスト（mock・キー不要）
pytest tests/ -q
python examples/run_pipeline.py --mock --auto-approve --project-type app --export ./out "メモ管理アプリ"

# 実LLM・実CADでの本番相当（Mac上・CAD起動が必要）
python examples/run_pipeline.py --auto-approve --project-type hardware --export ./out "環境モニター"
```

> ローカルのPythonは 3.10+（推奨3.12）。CIも3.12固定。
