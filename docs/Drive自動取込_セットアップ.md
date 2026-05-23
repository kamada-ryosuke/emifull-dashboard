# Google Drive 自動取込 セットアップ

障がい事業部ダッシュボードの「売上一覧 / 入金管理」で、CSVファイルを
Google Drive の指定フォルダに置くだけで自動取込される仕組みのセットアップ手順。

---

## 仕組みの全体像

```
[Drive ルートフォルダ]
  ├ 001_SORATO（UMIE）いなみ/        ← 施設フォルダ (損益ダッシュボードの親基準)
  ├ 002_SORATO（UMIE）いなみ第二教室/
  ├ 003_SORATO（UMIE）てんり/
  ├ 005_Hinodeシェアホーム天理/
  ├ 006_ジョブカレッジかこがわ/
  ├ 007_カラダキッズかこがわ/
  ├ 008_カラダキッズてんり/
  ├ 009_Hinodeシェアホーム加古川/
  ├ 010_相談支援NOAH加古川/
  ├ 011_のじぎく高砂/
  ├ 012_のじぎく稲美/
  └ _処理済/
       ├ 001_SORATO（UMIE）いなみ/
       │    └ SK_xxx.csv         ← 取込完了したファイル
       └ ...
```

- 施設フォルダ名は **損益ダッシュボードの親(`pl_groups`)を基準** に自動生成
- CSV→施設の対応付けは **CSV内の事業所コード(10桁)** で照合（フォルダ名ミスにも強い）
- 取込済みファイルは **Drive 上で `_処理済/<元フォルダ名>/`** に自動移動

---

## 手順1: GCPでサービスアカウントを作成

サービスアカウント方式が運用上いちばん楽です（OAuthトークンの期限切れに悩まされない）。

1. [Google Cloud Console](https://console.cloud.google.com/) を開く
2. 任意のプロジェクトを作成 (例: `emifull-uriage-drive`)
3. **APIとサービス → ライブラリ** で「Google Drive API」を検索 → **有効化**
4. **APIとサービス → 認証情報 → 認証情報を作成 → サービスアカウント**
   - 名前: `uriage-drive-bot` 等
   - 役割: なし（Driveのフォルダ単位で個別共有するので不要）
5. 作成したSAを開く → **キー → 鍵を追加 → JSON**
   - JSONファイルがダウンロードされるので
   - **`C:\売上入金管理ツール\config\drive-service-account.json`** に保存

---

## 手順2: Drive のルートフォルダを SA に共有

1. Driveでルートフォルダを開く
   `https://drive.google.com/drive/folders/11qlAL0fMW7j79Ntz5K2W2rI4cNcFp5l9`
2. **共有** をクリック
3. 手順1で作ったサービスアカウントのメール (`xxx@yyy.iam.gserviceaccount.com`) を入力
4. 権限を **編集者** に設定 → 送信
   - 編集者である必要があります（ファイル移動のため）

---

## 手順3: 設定ファイル

```bash
cd C:\売上入金管理ツール
copy config\drive_config.json.sample config\drive_config.json
```

`config/drive_config.json` を開いて中身を確認（基本デフォルトのままでOK）：

```json
{
  "root_folder_id": "11qlAL0fMW7j79Ntz5K2W2rI4cNcFp5l9",
  "processed_folder_name": "_処理済",
  "auth_mode": "service_account",
  "service_account_path": "config/drive-service-account.json",
  "csv_extensions": [".csv", ".CSV"],
  "max_file_size_mb": 10,
  "watcher_poll_interval_sec": 300
}
```

別のDriveルートを使うなら `root_folder_id` を差し替えてください。

---

## 手順4: 依存ライブラリをインストール

```bash
cd C:\売上入金管理ツール
pip install -r requirements.txt
```

新しく追加されたのは：
- `google-api-python-client`
- `google-auth`
- `google-auth-httplib2`
- `google-auth-oauthlib`

---

## 手順5: 施設フォルダを Drive に作成

```bash
python scripts\setup_drive_folders.py
```

成功すると、Drive のルートフォルダ直下に **損益ダッシュボードの親グループ名で施設フォルダ + `_処理済` フォルダ** が作成されます。

すでに存在するフォルダはスキップされるので、何度実行しても安全です。
損益ダッシュボードで親グループを増減した後にもう一度実行すると、Drive側にも反映されます。

---

## 手順6: CSVを置いて取り込みテスト

1. Drive でいずれかの施設フォルダを開く（例: `003_SORATO（UMIE）てんり`）
2. 国保連からダウンロードした CSV (例: `SK202604_xxxx.csv`) をドラッグ＆ドロップ
3. 取込方法は2通り：

### A. Streamlit UIから手動

ブラウザで Streamlit を開き **「売上一覧 / 入金管理」→「📥 CSV取込」タブ** へ。

「☁ Driveから今すぐ取込」ボタンを押すと、その時点で Drive を巡回して新規CSVを取り込みます。
取込が成功したファイルは `_処理済/<元フォルダ名>/` に移動します。

### B. 常駐ウォッチャー（完全自動）

```bash
python scripts\drive_watcher.py
```

5分ごと（`watcher_poll_interval_sec` で変更可）に Drive を巡回します。
**Driveに置くだけで自動取込される運用**にしたい場合はこちらを常駐させてください。

#### Windowsで常駐起動するには

`autostart_launcher.py` 系の仕組み（既存）と同じ要領で、タスクスケジューラに登録するか、
スタートアップに `.bat` ファイルを置いてください。

例: `start_drive_watcher.bat`
```bat
@echo off
cd /d C:\売上入金管理ツール
python scripts\drive_watcher.py
```

---

## トラブルシューティング

### `drive_config.json が見つかりません`
→ 手順3を実行してください。

### `サービスアカウント鍵が見つかりません: ...drive-service-account.json`
→ 手順1で保存したJSONのパスが違うか、ファイルがありません。

### `事業所コード XXXXXXXXXX が施設マスタに未紐付け`
→ そのコードのCSVは Streamlit UI で一度手動アップロードして
   施設に紐付けしてください（初回のみ）。次回以降は自動取込されます。

### `403 The user does not have sufficient permissions`
→ 手順2でDriveのフォルダを **編集者権限で** SAに共有していない。
   閲覧者権限ではファイル移動ができません。

### 同じファイルが2回取り込まれる
→ そのまま運用してOKです。`upsert_monthly_record` が `service_year_month / facility_id /
   cert_number` の3点で UPSERT するので、二重取込しても請求額が正しく上書きされ、
   入金情報は保持されます。
