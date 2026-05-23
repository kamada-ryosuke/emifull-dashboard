# Streamlit Cloud 公開手順

このアプリは Streamlit 製の「障がいダッシュボード」です。スタッフへ URL を共有し、スマホ・PC からログインして使ってもらう前提では、次の構成にします。

- 画面公開: Streamlit Community Cloud
- データベース: Turso
- 入口ファイル: `streamlit_app.py`
- ログイン画面: `ログイン.py`
- 認証・権限: `lib/auth.py`
- DB 接続: `lib/db.py`

URL 自体は共有して問題ありません。ただし、未ログイン者はログイン画面しか見えない構成です。

## 0. Codex 側で完了済みのこと

- 公開用ファイル構成の確認
- ログイン必須ページ、管理者専用ページの確認
- Streamlit Cloud 用の設定確認
- Turso 接続用の環境変数名の整理
- SQLite DB を Turso へ移すための `upload_db_to_turso.ps1` 用意
- スタッフ共有用案内文の用意
- 公開前チェックスクリプトの用意

公開前チェックは次で実行できます。

```powershell
cd "C:\Users\user01\Documents\Codex\2026-05-18\c-claude-code-codex1de"
& "C:\Users\user01\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" scripts\check_streamlit_cloud_ready.py
```

`OK: Streamlit Cloud files are ready.` と出れば、公開前の基本チェックは通っています。

## 1. 公式ページ

使うページはこの 3 つです。

- Turso: https://app.turso.tech/
- Streamlit Community Cloud: https://share.streamlit.io/
- GitHub 新規リポジトリ作成: https://github.com/new

## 2. Turso でデータベースを作る

1. https://app.turso.tech/ を開きます。
2. GitHub などでログインします。
3. `Create Database` または `New Database` を押します。
4. Database name に次を入力します。

```text
uriage
```

5. Region は選べる場合、東京または日本に近い地域を選びます。迷ったら初期値でも大丈夫です。
6. 作成後、データベース画面で次の 2 つを取得します。

```text
TURSO_DATABASE_URL
TURSO_AUTH_TOKEN
```

`TURSO_DATABASE_URL` は `libsql://...` で始まる URL です。

`TURSO_AUTH_TOKEN` は長い文字列です。これはパスワードと同じ扱いなので、スタッフへ共有しないでください。

## 3. 今のローカル DB を Turso へ移す

Turso の 2 つの値が用意できたら、PowerShell で次を実行します。

```powershell
cd "C:\Users\user01\Documents\Codex\2026-05-18\c-claude-code-codex1de"
.\upload_db_to_turso.ps1
```

途中で入力を求められます。

`Paste TURSO_DATABASE_URL` と出たら、Turso 画面でコピーした `libsql://...` を貼り付けて Enter。

`Paste TURSO_AUTH_TOKEN (hidden)` と出たら、Turso の Token を貼り付けて Enter。画面には表示されませんが、入力されています。

最後に `Done.` と出れば、現在の DB が Turso に移っています。

## 4. GitHub に公開用ファイルを置く

最新版のアップロード用 ZIP が必要な場合は、PowerShell で次を実行します。

```powershell
cd "C:\Users\user01\Documents\Codex\2026-05-18\c-claude-code-codex1de"
.\make_streamlit_cloud_package.ps1
```

作成されるファイル:

```text
streamlit_cloud_package_ready.zip
```

1. https://github.com/new を開きます。
2. Repository name に分かりやすい名前を入れます。

例:

```text
emifull-dashboard
```

3. Public / Private はどちらでも構いません。迷ったら Private にしてください。
4. 作成した GitHub リポジトリへ、このアプリの公開用ファイルをアップロードします。

アップロードしてはいけないもの:

- `data/uriage.db`
- `.streamlit/secrets.toml`
- API キーや Token を書いたファイル
- 個人情報を含む出力ファイル

## 5. Streamlit Cloud で公開する

1. https://share.streamlit.io/ を開きます。
2. `Create app` を押します。
3. `Yup, I have an app` のような選択肢が出たら選びます。
4. GitHub のリポジトリを選びます。
5. Branch は通常 `main` を選びます。
6. Main file path / App file path には次を入力します。

```text
streamlit_app.py
```

7. `Advanced settings` を開きます。
8. `Secrets` 欄に次を貼り付けます。

```toml
TURSO_DATABASE_URL = "libsql://ここにTursoのDatabase URL"
TURSO_AUTH_TOKEN = "ここにTursoのToken"

# AI要約を本番でも使う場合だけ入れます
OPENAI_API_KEY = "sk-..."
```

9. `Save` を押します。
10. `Deploy` を押します。

公開後、Streamlit から `https://xxxx.streamlit.app/` のような URL が発行されます。これがスタッフへ共有する URL です。

## 6. 公開後の確認

スタッフへ URL を送る前に、次を確認してください。

1. シークレットウィンドウで公開 URL を開く
2. ログイン画面だけが表示される
3. 一般ユーザーでログインする
4. 一般ユーザーに管理者専用ページが見えない
5. 管理者でログインする
6. 管理者ページが見える
7. 損益ダッシュボード、報告書提出、業績会議 Excel 出力を確認する
8. スマホ幅でメニューと入力欄が崩れないか確認する

## 7. 大事な注意

- 公開環境では `CODEX_AUTO_LOGIN` を設定しないでください。
- GitHub に `.streamlit/secrets.toml` をアップロードしないでください。
- GitHub に `data/uriage.db` をアップロードしないでください。
- Turso の Token はスタッフへ共有しないでください。
- 管理者がアプリを修正して GitHub に反映すると、Streamlit Cloud 側にも反映されます。

## 8. スタッフ共有用案内文

```text
障がいダッシュボードを公開しました。

下記URLから、スマホ・パソコンでアクセスできます。
URL：（ここに公開URLを入れてください）

ログインには、管理者から案内されたメールアドレスとパスワードを使用してください。
URLを開くとログイン画面が表示されます。

ログイン後は、ご自身の権限に応じた画面のみ表示されます。
ログインできない場合、またはパスワードを忘れた場合は、管理者まで連絡してください。
```
