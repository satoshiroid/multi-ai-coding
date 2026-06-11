# multi-ai-coding

自然言語の指示だけで、意匠・メカ・回路・ファームウェアを含むサイバーフィジカル製品を開発できる、
自律型マルチエージェント開発環境。

## アーキテクチャ

```
オーナー (Discord スマホ)
        │ 要件入力 / HITL承認
        ▼
┌─────────────────────────────────────────────────────────┐
│       PM / Orchestrator (L1 — Gemini 2.0 Flash)         │
│  ┌─────────────────────────────────────────────────┐    │
│  │   Senior Design Manager (L2 — Gemini 2.0 Flash)  │  │
│  └─────────────────────────────────────────────────┘    │
│                                                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐   │
│  │ Design   │ │  Mecha   │ │ Circuit  │ │Software  │   │
│  │ Worker   │ │ Worker   │ │ Worker   │ │ Worker   │   │
│  │(L3/Blender│ │(L3/FreeCAD│ │(L3/KiCAD) │ │ (L3/C++) │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘   │
└─────────────────────────────────────────────────────────┘
```

**ハブ&スポーク型**: PM が中央ハブ。ワーカー間の直接通信は禁止（制約違反の増幅防止）。

## パイプライン

PM が要件を分析して **ハードウェア製品** か **ソフトウェアアプリ** かを自動判別し、対応パイプラインを選択します。

### ハードウェア製品パイプライン（9ステージ）

| # | ステージ | 担当 | 内容 |
|---|---------|------|------|
| 1 | 要件定義入力 | オーナー | 自然言語で製品アイデアを入力 |
| 2 | システムアーキテクチャ策定 | PM (L1) | project_type 判定 + ドメイン分解 |
| 3 | コンセプトデザイン | DesignWorker (L3/Blender) | 3Dモデル・レンダリング生成 |
| 4 | **HITL承認ゲート1** | オーナー | デザイン承認 / 修正ループ |
| 5 | 並列設計 | MechaWorker + CircuitWorker | FreeCAD筐体 + KiCAD基板 |
| 6 | 整合性チェック | PM | 内寸 vs 基板外寸の数値検証 |
| 7 | **HITL承認ゲート2** | オーナー | 仕様・コスト承認 |
| 8 | 製造データ生成 | CircuitWorker + SoftwareWorker | Gerber出力 + ファームウェア |
| 9 | **最終サインオフ** | オーナー | 製造データアーカイブ |

### アプリ開発パイプライン（8ステージ）

PM が `project_type: "app"` と判断した場合（Web / モバイル / デスクトップ / SaaS / CLI）に選択されます。

| # | ステージ | 担当 | 内容 |
|---|---------|------|------|
| 1 | 要件定義入力 | オーナー | 自然言語でアプリアイデアを入力 |
| 2 | アーキテクチャ策定 | PM (L1) | project_type = "app" を返す |
| 3 | UI/UXデザイン | DesignWorker (L3) | 画面設計・遷移フロー |
| 4 | **HITL承認ゲート1** | オーナー | UIデザイン承認 / 修正ループ |
| 5 | アーキテクチャ設計 | SoftwareWorker (L3) | 技術スタック・モジュール・API設計 |
| 6 | **HITL承認ゲート2** | オーナー | アーキテクチャ承認 |
| 7 | MVP実装 | SoftwareWorker (L3) | コアコード生成（200行以内の骨格） |
| 8 | **最終サインオフ** | オーナー | コード・セットアップ手順確認 |

L2 シニアは各 L3 の `confidence_score < 70` で自動介入。L2 解決不能時は Discord でオーナーへエスカレーション。

## LLMコスト戦略

| 層 | 役割 | プロバイダ / モデル | フォールバック |
|----|------|-----------------|------|
| L1 | PM / オーケストレーター | gemini / gemini-2.0-flash（無料枠） | gemini-2.0-flash |
| L2 | シニア設計マネージャー | gemini / gemini-2.0-flash（無料枠） | gemini-2.0-flash |
| L3 | ワーカー（実装/デバッグ/ツール操作） | **anthropic / claude-opus-4-8** | gemini-2.0-flash |

計画・調停系（L1/L2）は Gemini 無料枠、**ツール操作スクリプト生成（L3）は Claude** のハイブリッド構成。
コード精度が成果物到達率を決めるため、L3 だけ有料モデルにする方がエラー再試行込みの実質コストは低い
（bpyスクリプト1生成 ≈ $0.06、モデリング1セッション ≈ $0.3〜0.5、フルパイプライン ≈ $1〜3）。

> **ローカルLLMについて**: qwen2.5-coder 等によるツール操作は 8GB クラスの Mac では非現実的と
> 実測で確認済み（3b は bpy API を知らず、7b はメモリに載らない。発熱も深刻）。
> 詳細な分析は [docs/architecture-review.md](docs/architecture-review.md) を参照。
> 16GB 以上のマシンでは `--builder ollama` で再試行可能です。

> **注意**: Claude Pro / Max 月額プランは API アクセスを含みません。
> Anthropic API を使う場合は別途 [Anthropic API](https://console.anthropic.com) の従量課金登録（クレジット購入）が必要です。

> **セキュリティ**: API キーをターミナルに `echo`/`cat` で表示したりスクリーンショットに含めると
> 漏洩します。漏洩した場合は即時再発行してください
> （Anthropic: console.anthropic.com / Gemini: aistudio.google.com/apikey / Discord: Developer Portal → Reset Token）。

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. 環境変数の設定

`.env.example` をコピーして `.env` を作成:

```bash
cp .env.example .env
```

`.env` を編集:

```env
# LLM API キー
ANTHROPIC_API_KEY=sk-ant-...        # https://console.anthropic.com
GEMINI_API_KEY=AIza...              # https://aistudio.google.com/apikey
OLLAMA_BASE_URL=http://localhost:11434  # Ollama ローカル実行時

# Discord Bot
DISCORD_BOT_TOKEN=...               # Discord Developer Portal で取得
DISCORD_FORUM_CHANNEL_ID=123...     # フォーラムチャンネルの ID
OWNER_USER_ID=456...                # オーナーの Discord ユーザー ID
```

### 3. Discord Bot セットアップ

1. [Discord Developer Portal](https://discord.com/developers/applications) でアプリ作成
2. **Bot** セクションでボットを追加 → `DISCORD_BOT_TOKEN` を取得
3. **Privileged Gateway Intents** で `MESSAGE CONTENT` を有効化
4. OAuth2 URL Generator で `bot` + `applications.commands` スコープ、必要権限を付与してサーバーへ招待
5. サーバーにフォーラムチャンネルを作成 → チャンネルID を `DISCORD_FORUM_CHANNEL_ID` に設定
6. 自分のユーザーIDを `OWNER_USER_ID` に設定（右クリック → 「ユーザーIDをコピー」、開発者モード要）

### 4. MCPサーバーのセットアップ（実機操作時）

`config/settings.yaml` の `mcp_servers` セクションで各サーバーの起動コマンドを指定。

| ドメイン | 推奨MCPサーバー |
|---------|--------------|
| Blender | [blender-mcp](https://github.com/ahujasid/blender-mcp) — アドオンをインストール後、Blender 内の「Blender MCP」パネルでポート **9876** を指定してサーバー起動。このポートは MCP/SSE ではなく**生の JSON-over-TCP ソケット**のため、`config/settings.yaml` は `transport: tcp` / `host: localhost` / `port: 9876` を設定済み（中継プロセス不要で直接接続） |
| FreeCAD | [neka-nat/freecad-mcp](https://github.com/neka-nat/freecad-mcp) |
| KiCAD | [lamaalrajih/kicad-mcp](https://github.com/lamaalrajih/kicad-mcp) |

## 実行方法

### モックモード（API・CAD・Discord 不要）

```bash
# デフォルト要件でパイプライン実行
python examples/run_pipeline.py --mock

# 要件を指定
python examples/run_pipeline.py --mock "Wi-Fi環境モニターを作りたい"

# CLIでHITL承認を手動入力
python examples/run_pipeline.py "製品アイデア"
```

### Blender ライブモデリングテスト（Claude × デザインレビュー）

起動中の Blender を **Claude**（claude-opus-4-8）が操作してモデリングし、
レンダリング結果を**デザインLLM**（Gemini vision）が採点・修正指示する自律ループ:

```
要件 → [Claude] bpyスクリプト生成 → [Blender:9876] 実行(エラー時は自己デバッグ)
     → レンダリング(PNG) → [Gemini vision] スコア+修正指示 → 合格 or 再修正ループ
```

事前準備:

```bash
# 1. Blender で BlenderMCP アドオンのサーバーを起動（ポート9876）

# 2. .env に ANTHROPIC_API_KEY を設定（従量課金・クレジット購入要）

# 3. .env に GEMINI_API_KEY を設定（レビュー不要なら --no-review で省略可）
```

実行:

```bash
# デフォルト: Claudeビルダー + Geminiレビュー
python examples/run_blender_live.py "丸みを帯びたワイヤレスキーボードのコンセプトモデル"

# 低コスト構成（Haikuビルダー・レビューなし）
python examples/run_blender_live.py --builder-model claude-haiku-4-5 --no-review "..."

# 無料枠のみ（Geminiビルダー）
python examples/run_blender_live.py --builder gemini "..."

# ローカルLLM実験（16GB+マシン向け。8GBでは非推奨 — docs/architecture-review.md参照）
python examples/run_blender_live.py --builder ollama --builder-model qwen2.5-coder:7b "..."
```

レンダリングは `outputs/blender_live/iter_NN.png` に保存されます。

### Discord Bot 常駐サーバー

```bash
python examples/run_server.py
```

フォーラムチャンネルに新しいスレッドを投稿すると自動でパイプラインが起動します。

### FastAPI REST サーバー（Bot と並走）

```bash
uvicorn src.interfaces.api:app --host 0.0.0.0 --port 8000
```

| エンドポイント | 説明 |
|-------------|------|
| `GET /health` | ヘルスチェック |
| `GET /projects` | プロジェクト一覧 |
| `GET /projects/{id}` | プロジェクト状態取得 |
| `POST /projects` | 新規プロジェクト起動 |

## テスト

```bash
# 全テスト実行（83件）
pytest tests/ -v

# モック E2E のみ
pytest tests/test_orchestrator.py -v

# LLM ファクトリ
pytest tests/test_llm_factory.py -v
```

全テストはモック MCP・モック Discord で API キーなし・CAD ツールなしで実行できます。

## ディレクトリ構造

```
multi-ai-coding/
├── config/
│   ├── settings.yaml        # 階層別モデル割当・MCPサーバー接続
│   └── agents.yaml          # エージェント役割・システムプロンプト
├── docs/
│   └── architecture-review.md  # 企画再評価（ローカルLLM実測・検証ゲート設計）
├── src/
│   ├── models.py            # Pydantic v2 コアモデル
│   ├── llm/
│   │   ├── provider.py      # LLMProvider 抽象基底
│   │   ├── anthropic_provider.py
│   │   ├── gemini_provider.py
│   │   ├── ollama_provider.py
│   │   ├── factory.py       # TieredLLM（プライマリ+フォールバック）
│   │   └── mock_provider.py
│   ├── mcp/
│   │   ├── client.py        # McpClient (stdio/官式SDK)
│   │   ├── blender_client.py
│   │   ├── blender_tcp.py   # BlenderMCPアドオン直結TCPクライアント
│   │   ├── freecad_client.py
│   │   ├── kicad_client.py
│   │   └── mock_transport.py
│   ├── agents/
│   │   ├── base_agent.py
│   │   ├── pm_agent.py      # L1 PM
│   │   ├── senior_agent.py  # L2 シニア
│   │   └── worker_agents.py # L3 ワーカー x4
│   ├── hitl/
│   │   ├── hitl_manager.py  # asyncio.Future ベース承認ゲート
│   │   └── channels/
│   │       ├── base_channel.py
│   │       ├── discord_channel.py  # Discord Embed + ボタン承認
│   │       └── cli_channel.py
│   ├── orchestrator/
│   │   ├── pm_orchestrator.py   # メイン実行エンジン
│   │   ├── context_store.py     # 共有コンテキスト（BOM/制約）
│   │   ├── consistency.py       # 内寸 vs 基板外寸チェック
│   │   ├── state_store.py       # SQLite 状態永続化
│   │   ├── task_router.py
│   │   └── builder.py
│   └── interfaces/
│       ├── discord_bot.py   # Bot: スレッド作成→パイプライン起動
│       └── api.py           # FastAPI REST
├── workflows/
│   └── manufacturing_pipeline.py  # 9ステージ定義
├── examples/
│   ├── run_pipeline.py      # CLI エントリポイント
│   ├── run_blender_live.py  # Blenderライブモデリング（Ollama×Geminiレビュー）
│   └── run_server.py        # 本番サーバー起動
├── tests/                   # 83 テスト（全モック）
├── requirements.txt
├── pytest.ini
└── .env.example
```

## 設計原則

- **ハブ&スポーク**: ワーカー間直接通信禁止。制約違反の連鎖増幅を防止
- **confidence_score 契約**: 全 L3 出力は `{summary, confidence_score, artifacts, metadata}` の JSON
- **共有コンテキスト**: 寸法は上書き (overwrite)、BOM は (domain, part_number) キーのマージ（同一部品の再掲は置換し二重計上を防止）
- **整合性チェック**: 筐体内寸 ≥ 基板外寸 + 2×クリアランス (1.0mm) を数値保証
- **HITL ゲート**: `asyncio.Future` で非ブロッキング待機。72h タイムアウト後にセーフ停止
- **MCP 接続**: 既存の成熟した MCP サーバーへの接続アダプタのみ実装（CAD 制御は委譲）
- **テスト可能性**: 全ロジックをモック MCP/モック LLM で CI 実行可能
