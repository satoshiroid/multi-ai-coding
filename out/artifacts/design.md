# design artifacts

## blender_script



## design_spec

```json
{
  "command_design": {
    "add": {
      "input": "add <単語> <意味>",
      "output": "単語 '<単語>' が追加されました。"
    },
    "list": {
      "input": "list",
      "output": "登録単語一覧:\n1. 単語1: 意味1\n2. 単語2: 意味2\n..."
    },
    "quiz": {
      "input": "quiz",
      "output": "単語: <単語>\n意味を入力してください: "
    }
  },
  "output_format_examples": {
    "add": "単語 'example' が追加されました。",
    "list": "登録単語一覧:\n1. apple: りんご\n2. banana: バナナ",
    "quiz": "単語: apple\n意味を入力してください: "
  },
  "error_messages": {
    "duplicate_word": "エラー: 単語 '<単語>' は既に登録されています。",
    "db_not_initialized": "エラー: データベースが初期化されていません。",
    "quiz_no_words": "エラー: 登録された単語がありません。"
  },
  "ux_flow": "Start -> [add] -> [list] -> [quiz] -> End\n[add] -> [Error: duplicate_word] -> [add]\n[quiz] -> [Error: quiz_no_words] -> [add]\n[any] -> [Error: db_not_initialized] -> End"
}
```
