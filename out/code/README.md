# プロジェクト概要
このプロジェクトは、単語を管理するシンプルなCLIアプリケーションです。データベースに単語を追加し、リスト表示やランダム取得が可能です。

## 動作環境
- Python 3.10+

## セットアップ手順
```bash
git clone <repository-url>
cd <repository-directory>
python main.py
```

## コマンド使用例
```bash
python main.py
```

## ファイル構成
- `main.py`: アプリケーションのエントリーポイント
- `db.py`: データベース操作を行うモジュール
- `test_db.py`: データベース操作のテスト
- `README.md`: プロジェクトの説明書
- `requirements.txt`: 必要なライブラリ（標準ライブラリのみ使用）