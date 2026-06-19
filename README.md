# Claude Browser Agent

Claude がブラウザを操作する DOM ベースの自律エージェント。**ログイン・検索・スクリーンショット保存**を
自然言語の指示で実行する。**Microsoft Edge（既定）/ Chrome 切替**、**WSL 不要のネイティブ Windows 11** 対応。

2 つの使い方を同梱:

| 形態 | ファイル | 説明 |
|---|---|---|
| スタンドアロン | `agent.py` | Anthropic API を使い、CLI で単発タスクを自動実行 |
| テンプレート実行 | `run_template.py` | サイトごとのテンプレート＋数値データで繰り返し実行（推奨） |
| MCP サーバー | `mcp_server.py` | Claude Desktop / OpenClaw から会話しながらブラウザを操作 |

`agent.py` と `run_template.py` は同じ `browser.py`（Selenium ラッパー）を共有する。

## 仕組み

ページ上の操作可能な要素に連番 `[0] [1] [2] …` を振り、その一覧を Claude に渡す。Claude は番号を
指定して `click_element` / `input_text` を呼ぶ（座標・セレクタを推測させない＝安定）。各操作後に
最新のページ状態を返し、結果を見て次を判断させる。

## 前提（ネイティブ Windows 11 / WSL 不要）

1. **Python 3.10+** … python.org のインストーラ（"Add python.exe to PATH" にチェック）
2. **Microsoft Edge** … Windows 11 に標準搭載（既定ブラウザ）。Chrome を使う場合のみ別途インストール
3. WebDriver（msedgedriver / chromedriver）は **Selenium Manager が自動取得** するため手動導入不要

```powershell
cd C:\path\to\claude_browser_agent
pip install -r requirements.txt
copy .env.example .env   # ANTHROPIC_API_KEY 等を記入（スタンドアロン用）
```

---

## 2. サイトごとのテンプレート運用（推奨）

「ログイン → メニュー操作 → 多数の数値項目を入力 → 登録 → 完了画面をスクショ」を、サイトごとに
**1 度書いたテンプレートで繰り返す**ための仕組み。手順（テンプレート）と数値（データ）を分離する。

- **テンプレート** (`templates/<site>.yaml`) … ログイン手順・メニュー操作・どの項目に入れるか。サイトごとに 1 つ。
- **データ** (`data/<run>.json`) … 実際に入れる数値。実行ごとに差し替え。値は `{{key}}` で参照。
- パスワード等は `{{SECRET:NAME}}` のまま残り、実行時に環境変数から補完（プロンプトに実値は載らない）。

```powershell
# まず生成プロンプトを確認（ブラウザは起動しない）
python run_template.py --template templates/example_site.yaml --values data/example_values.json --dry-run

# 実行（Edge を表示して）
python run_template.py `
  --template templates/example_site.yaml `
  --values data/example_values.json `
  --browser edge --no-headless
```

テンプレートの書き方は `templates/example_site.yaml` を参照（`login` / `navigation` / `fields` /
`submit` / `verify` / `screenshot` の各節）。項目が多いサイトは `fields` に足していくだけ。
複雑なメニューは `navigation` に自然言語で具体的に書けば Claude が辿る。

**信頼性の工夫**: 入力欄の現在値を読み取れるようにしてあり（`get_page_state` に「現在値」が出る）、
生成プロンプトは「1 項目ごとに入力 → 現在値を確認 → 最後に全項目を再検証 → 完了を確認してスクショ」
という順序を強制する。`--values` に値の指定漏れがあれば実行前にエラーで止まる。項目が多い場合は
`--max-steps` を増やす。難しいサイトは `--model claude-opus-4-8` を検討。

| オプション | 既定 | 説明 |
|---|---|---|
| `--template` | （必須） | サイトテンプレート (YAML) |
| `--values` | なし | 入力値 (JSON) |
| `--browser` | `edge` | `edge` / `chrome` |
| `--model` | `claude-sonnet-4-6` | 難サイトは `claude-opus-4-8` |
| `--max-steps` | `40` | 項目数に応じて増やす |
| `--dry-run` | — | 生成プロンプトの確認のみ |
| `--no-headless` | （headless） | ブラウザを表示 |

---

## 3. スタンドアロン（単発 CLI）

```powershell
# Edge で: ログイン → 検索 → スクショ保存
python agent.py `
  --task "ユーザー名 {{SECRET:MY_USERNAME}} とパスワード {{SECRET:MY_PASSWORD}} でログインし、'MCP server' を検索して result.png に保存して" `
  --start-url example.com `
  --browser edge --no-headless

# Chrome に切り替える場合
python agent.py --task "..." --browser chrome
```

| オプション | 既定 | 説明 |
|---|---|---|
| `--task` | （必須） | 自然言語のタスク |
| `--start-url` | なし | 開始 URL |
| `--browser` | `edge` | `edge` / `chrome` |
| `--model` | `claude-sonnet-4-6` | 難しいタスクは `claude-opus-4-8` |
| `--max-steps` | `25` | 最大ステップ数 |
| `--no-headless` | （headless） | ブラウザを画面に表示 |
| `--out-dir` | `output` | スクショ保存先 |

---

## 4. MCP サーバー版（Claude Desktop / OpenClaw）

会話の中で「○○にログインして△△を検索しスクショを取って」と頼むと、Claude が下記ツールを呼ぶ:
`open_browser` / `navigate` / `get_page_state` / `click_element` / `input_text` /
`send_keys` / `scroll` / `take_screenshot` / `close_browser`。
`take_screenshot` は保存に加え、撮った画像を Claude にも返すのでそのまま画面を見て判断できる。API キーは不要。

### Claude Desktop 設定（Windows）

`%APPDATA%\Claude\claude_desktop_config.json` を開き、`claude_desktop_config.example.json` を参考に追記:

```json
{
  "mcpServers": {
    "browser-agent": {
      "command": "python",
      "args": ["C:\\path\\to\\claude_browser_agent\\mcp_server.py"],
      "env": {
        "BROWSER_AGENT_BROWSER": "edge",
        "BROWSER_AGENT_HEADLESS": "0",
        "BROWSER_AGENT_OUTPUT": "C:\\Users\\<you>\\Pictures\\browser-agent",
        "MY_USERNAME": "your-login-id",
        "MY_PASSWORD": "your-password"
      }
    }
  }
}
```

保存して Claude Desktop を再起動すると `browser-agent` のツールが現れる。`BROWSER_AGENT_BROWSER` を
`chrome` にすれば Chrome で動く。`uv` 派なら `"command": "uv"`, `"args": ["run", "mcp_server.py"]` でも可。

### OpenClaw

`mcp.servers` に同様のエントリ（`command` / `args` / `env`）を追加するか
`openclaw mcp set browser-agent -- python C:\path\to\mcp_server.py` で登録する。

---

## 秘密情報の扱い

パスワード等は **モデルに渡さない**。指示やツール引数では `{{SECRET:NAME}}` と書き、実際の値は
ローカル（`.env` または MCP の `env`）の環境変数から `browser.py` 内で補完される。ログ表示も
`[SECRET:NAME]` にマスクされる。`.env` と設定 JSON は Git にコミットしないこと（`.gitignore` 済み）。

## トラブルシュート

- **ドライバ取得に失敗** … ネットワーク制限のある社内環境では Selenium Manager が
  ドライバを取得できないことがある。その場合は社内ミラー or 手動で msedgedriver/chromedriver を
  PATH に置く（バージョンはブラウザに合わせる）。
- **会社プロキシ** … 必要なら `HTTPS_PROXY` 環境変数を設定する。
- **要素が見つからない** … 動的ページでは `get_page_state` を取り直してから操作する（Claude は自動で行う）。

## ライセンス

[Apache License 2.0](LICENSE) のもとで公開しています。Copyright 2026 Iwamoto-Ai.
