# Turso と Streamlit Cloud 次の作業

この手順は「何をどこに入力するか」だけに絞った版です。

## 1. Turso で必要な 2 つを用意する

開くページ:

```text
https://app.turso.tech/
```

やること:

1. ログインします。
2. `Create Database` を押します。
3. Database name に `uriage` と入力します。
4. 作成した DB の画面で、次の 2 つをコピーします。

```text
TURSO_DATABASE_URL
TURSO_AUTH_TOKEN
```

メモ:

- `TURSO_DATABASE_URL` は `libsql://` で始まります。
- `TURSO_AUTH_TOKEN` は長い秘密の文字列です。
- Token はスタッフへ送らないでください。

## 2. 今の DB を Turso にアップロードする

PowerShell を開いて、次を 1 行ずつ入れます。

```powershell
cd "C:\Users\user01\Documents\Codex\2026-05-18\c-claude-code-codex1de"
.\upload_db_to_turso.ps1
```

聞かれたら入力します。

```text
Paste TURSO_DATABASE_URL
```

ここには `libsql://...` を貼ります。

```text
Paste TURSO_AUTH_TOKEN (hidden)
```

ここには Token を貼ります。画面に文字は出ませんが、そのまま Enter で大丈夫です。

最後に `Done.` と出れば完了です。

## 3. GitHub に載せる ZIP を作る場合

最新版のアップロード用 ZIP が必要な場合は、PowerShell で次を実行します。

```powershell
cd "C:\Users\user01\Documents\Codex\2026-05-18\c-claude-code-codex1de"
.\make_streamlit_cloud_package.ps1
```

作成されるファイル:

```text
streamlit_cloud_package_ready.zip
```

## 4. Streamlit Cloud に Secrets を入れる

開くページ:

```text
https://share.streamlit.io/
```

Streamlit Cloud のアプリ作成画面で `Advanced settings` を開き、`Secrets` 欄に次を貼ります。

```toml
TURSO_DATABASE_URL = "libsql://ここにTursoのDatabase URL"
TURSO_AUTH_TOKEN = "ここにTursoのToken"

OPENAI_API_KEY = "sk-..."
```

`OPENAI_API_KEY` は、AI要約を本番でも使う場合だけ入れます。まだ無ければ、この行は消して大丈夫です。

## 5. Streamlit Cloud の入力欄

App file path / Main file path:

```text
streamlit_app.py
```

Branch:

```text
main
```

App URL:

好きな英数字の名前にできます。例:

```text
emifull-dashboard
```

公開後は、次のような URL になります。

```text
https://emifull-dashboard.streamlit.app/
```

## 6. 公開できたら確認すること

1. 公開 URL をシークレットウィンドウで開く
2. ログイン画面だけが出る
3. 一般スタッフのアカウントでログインする
4. 管理者専用ページが見えない
5. 管理者アカウントでログインする
6. 管理者ページが見える
7. 報告書提出と業績会議を確認する

ここまでできれば、スタッフへ URL を共有できます。
