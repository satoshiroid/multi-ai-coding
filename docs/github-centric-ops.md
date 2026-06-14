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

### 1. GitHub Secrets（リポジトリ Settings → Secrets and variables → Actions）
- `ANTHROPIC_API_KEY`
- `GEMINI_API_KEY`
- `OLLAMA_BASE_URL`（任意）
- `DISCORD_WEBHOOK_URL`（任意・PRリンク通知用。intake用フォーラムchに作成したWebhook）

> LLMキーはここ（GitHub側）に置く。リポジトリのファイルには絶対に入れない。

#### LLM選択（Settings → Secrets and variables → Actions → **Variables**）
provider/model は非機密なので **Variables** で選ぶ（`settings.yaml` を編集せずGitで切替）。
優先順位: `LLM_L{n}_*`（tier別） > `LLM_*`（全体） > `settings.yaml`。provider と model はセットで設定する。

| 変数 | 例 | 対象 |
|---|---|---|
| `LLM_L1_PROVIDER` / `LLM_L1_MODEL` | `anthropic` / `claude-haiku-4-5-20251001` | PM |
| `LLM_L2_PROVIDER` / `LLM_L2_MODEL` | `anthropic` / `claude-haiku-4-5-20251001` | Senior |
| `LLM_L3_PROVIDER` / `LLM_L3_MODEL` | `anthropic` / `claude-opus-4-8` | Workers |
| `LLM_PROVIDER` / `LLM_MODEL` | — | 全tier一括の既定 |

> **注意（実測）**: Gemini無料枠は `gemini-2.0-flash` で `limit: 0`（枯渇/無効）になり得る。
> その場合は上記Variablesで Anthropic を選ぶか、Gemini課金を有効化する。`settings.yaml`
> 既定の「L1/L2=Gemini無料」のままだと L1 で 429 停止する。

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
