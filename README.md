# multi-ai-coding

自然言語の指示だけで、意匠・メカ・回路・ファームウェアを含むサイバーフィジカル製品を開発できる、
自律型マルチエージェント開発環境。

## アーキテクチャ

```
オーナー (Discord スマホ)
        │ 要件入力 / HITL承認
        ▼
┌─────────────────────────────────────────────────────────┐
│          PM / Orchestrator (L1 — Claude Opus)           │
│  ┌─────────────────────────────────────────────────┐    │
│  │        Senior Design Manager (L2 — Claude Sonnet) │  │
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

## 9ステージパイプライン

| # | ステージ | 担当 | 内容 |
|---|---------|------|------|
| 1 | 要件定義入力 | オーナー | 自然言語で製品アイデアを入力 |
| 2 | システムアーキテクチャ策定 | PM (L1) | ドメイン別サブタスクに分解 |
| 3 | コンセプトデザイン | DesignWorker (L3/Blender) | 3Dモデル・レンダリング生成 |
| 4 | **HITL承認ゲート1** | オーナー | デザイン承認 / 修正ループ |
| 5 | 並列設計 | MechaWorker + CircuitWorker | FreeCAD筐体 + KiCAD基板 |
| 6 | 整合性チェック | PM | 内寸 vs 基板外寸の数値検証 |
| 7 | **HITL承認ゲート2** | オーナー | 仕様・コスト承認 |
| 8 | 製造データ生成 | CircuitWorker + SoftwareWorker | Gerber出力 + ファームウェア |
| 9 | **最終サインオフ** | オーナー | 製造データアーカイブ |

L2 シニアは各 L3 の `confidence_score < 70` で自動介入。L2 解決不能時は Discord でオーナーへエスカレーション。

## LLMコスト戦略

| 層 | 役割 | プロバイダ / モデル | 代替 |
|----|------|-----------------|------|
| L1 | PM / オーケストレーター | anthropic / claude-opus-4-8 | gemini-2.x-pro |
| L2 | シニア設計マネージャー | anthropic / claude-sonnet-4-6 | gemini-2.x-pro |
| L3 | ワーカー（実装/デバッグ/ツール操作） | gemini / gemini-flash（無料枠） | ollama / qwen2.5-coder |

完全無料運用にしたい場合は `config/settings.yaml` で全層を `ollama` に変更するだけ。

> **注意**: Claude Pro / Max 月額プランは API アクセスを含みません。
> API 利用には別途 [Anthropic API](https://console.anthropic.com) の従量課金登録が必要です。

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
| Blender | [blendermcp](https://github.com/ahujasid/blender-mcp) — Mac 動作確認済み |
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
# 全テスト実行（36件）
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
│   └── run_server.py        # 本番サーバー起動
├── tests/                   # 36 テスト（全モック）
├── requirements.txt
├── pytest.ini
└── .env.example
```

## 設計原則

- **ハブ&スポーク**: ワーカー間直接通信禁止。制約違反の連鎖増幅を防止
- **confidence_score 契約**: 全 L3 出力は `{summary, confidence_score, artifacts, metadata}` の JSON
- **共有コンテキスト**: 寸法は上書き (overwrite)、BOM は追記 (append) の 2 モード
- **整合性チェック**: 筐体内寸 ≥ 基板外寸 + 2×クリアランス (1.0mm) を数値保証
- **HITL ゲート**: `asyncio.Future` で非ブロッキング待機。72h タイムアウト後にセーフ停止
- **MCP 接続**: 既存の成熟した MCP サーバーへの接続アダプタのみ実装（CAD 制御は委譲）
- **テスト可能性**: 全ロジックをモック MCP/モック LLM で CI 実行可能
