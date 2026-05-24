"""Google Drive 自動取込モジュール

役割:
  - 指定したルートフォルダ配下の施設サブフォルダを巡回
  - 新規CSVファイルを検出 → Shift-JIS としてダウンロード
  - 既存の csv_parser → db.upsert_monthly_record で取込
  - 取込済みCSVを Drive 上で `_処理済/<元フォルダ名>/` に移動

設計メモ:
  - フォルダ名はあくまで人間用の整理。CSVに含まれる事業所コード(10桁)で
    施設マスタを照合するため、誤フォルダに置かれても検出される。
  - 既存処理(file_hash)で二重取込は防止される（imports.file_hash 一致は upsert で安全）。
  - 認証はサービスアカウント(推奨) / OAuth の二系統。

主要API:
  load_config()                         # config/drive_config.json を読み込む
  build_service(config)                 # googleapiclient の Drive サービスを返す
  ensure_facility_folders(svc, root_id, names)
  ensure_subfolder(svc, parent_id, name)
  list_csv_files(svc, root_id, processed_folder_name)
  download_file_bytes(svc, file_id)
  move_to_processed(svc, file_id, src_folder_name, processed_root_id)
  sync(config=None, on_log=None) -> dict   # ワンショット全体実行
"""
from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from lib import csv_parser, db


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "drive_config.json"
SAMPLE_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "drive_config.json.sample"

DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


# =====================================================================
# 設定
# =====================================================================

class DriveConfigError(RuntimeError):
    pass


def load_config() -> dict:
    """config/drive_config.json を読む。なければサンプルを案内するエラー。"""
    if not CONFIG_PATH.exists():
        raise DriveConfigError(
            f"{CONFIG_PATH.name} が見つかりません。"
            f"{SAMPLE_CONFIG_PATH.name} を {CONFIG_PATH.name} にコピーして、"
            f"root_folder_id とサービスアカウント鍵パスを設定してください。"
        )
    with CONFIG_PATH.open(encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("root_folder_id"):
        raise DriveConfigError("drive_config.json の root_folder_id が空です。")
    return cfg


# =====================================================================
# 認証 / サービス
# =====================================================================

def build_service(config: dict):
    """Google Drive APIサービスを構築。"""
    try:
        from googleapiclient.discovery import build
    except ImportError as e:
        raise DriveConfigError(
            "google-api-python-client が未インストールです。"
            "`pip install -r requirements.txt` を実行してください。"
        ) from e

    auth_mode = config.get("auth_mode", "service_account")
    base_dir = CONFIG_PATH.parent.parent  # プロジェクトルート

    if auth_mode == "service_account":
        sa_path = base_dir / config["service_account_path"]
        from google.oauth2 import service_account
        if sa_path.exists():
            creds = service_account.Credentials.from_service_account_file(
                str(sa_path), scopes=[DRIVE_SCOPE],
            )
        else:
            sa_info = _load_service_account_from_streamlit_secrets()
            if not sa_info:
                raise DriveConfigError(
                    f"サービスアカウント鍵が見つかりません: {sa_path}\n"
                    "GCPでサービスアカウントを作成→鍵JSONをDL→このパスに保存、"
                    "またはStreamlit Secretsに GOOGLE_SERVICE_ACCOUNT_JSON を設定し、"
                    f"Driveのルートフォルダ(ID={config['root_folder_id']})を"
                    "そのSAメールに『閲覧者』以上で共有してください。"
                )
            creds = service_account.Credentials.from_service_account_info(
                sa_info, scopes=[DRIVE_SCOPE],
            )
    elif auth_mode == "oauth":
        creds = _get_oauth_credentials(config, base_dir)
    else:
        raise DriveConfigError(f"未対応の auth_mode: {auth_mode}")

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _load_service_account_from_streamlit_secrets() -> dict | None:
    """Streamlit Cloud用にSecretsからサービスアカウントJSONを読む。

    値はチャットやログに出さず、Drive認証オブジェクト作成にだけ使う。
    """
    try:
        import streamlit as st
    except Exception:
        return None

    try:
        secrets = st.secrets
    except Exception:
        return None

    # 1) JSON文字列として登録する方式
    for key in (
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "GCP_SERVICE_ACCOUNT_JSON",
        "DRIVE_SERVICE_ACCOUNT_JSON",
    ):
        try:
            raw = secrets.get(key)
        except Exception:
            raw = None
        if not raw:
            continue
        if isinstance(raw, dict):
            return dict(raw)
        try:
            return json.loads(str(raw))
        except Exception as e:
            raise DriveConfigError(
                f"Streamlit Secrets の {key} をJSONとして読めません。"
            ) from e

    # 2) [gcp_service_account] セクションとして登録する方式
    try:
        section = secrets.get("gcp_service_account")
    except Exception:
        section = None
    if section:
        return dict(section)

    return None


def _get_oauth_credentials(config: dict, base_dir: Path):
    """OAuthデスクトップアプリ方式。初回はブラウザ認証→トークン保存。"""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    cred_path = base_dir / config["oauth_credentials_path"]
    token_path = base_dir / config["oauth_token_path"]

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), [DRIVE_SCOPE])

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not cred_path.exists():
                raise DriveConfigError(
                    f"OAuthクライアント鍵が見つかりません: {cred_path}\n"
                    "GCPで『デスクトップアプリ』としてOAuthクライアントを作成→鍵JSONをDL→"
                    "このパスに保存してください。"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(cred_path), [DRIVE_SCOPE])
            creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        with token_path.open("w", encoding="utf-8") as f:
            f.write(creds.to_json())

    return creds


# =====================================================================
# フォルダ操作
# =====================================================================

def find_folder(service, parent_id: str, name: str) -> Optional[str]:
    """親IDの直下にあるサブフォルダ名で検索しIDを返す。なければNone。"""
    safe_name = name.replace("'", "\\'")
    q = (
        f"'{parent_id}' in parents and "
        f"mimeType='{DRIVE_FOLDER_MIME}' and "
        f"name='{safe_name}' and trashed=false"
    )
    res = service.files().list(
        q=q, fields="files(id,name)", pageSize=10,
        supportsAllDrives=True, includeItemsFromAllDrives=True,
    ).execute()
    items = res.get("files", [])
    return items[0]["id"] if items else None


def ensure_subfolder(service, parent_id: str, name: str) -> str:
    """サブフォルダがなければ作成、IDを返す。"""
    fid = find_folder(service, parent_id, name)
    if fid:
        return fid
    file_metadata = {
        "name": name,
        "mimeType": DRIVE_FOLDER_MIME,
        "parents": [parent_id],
    }
    created = service.files().create(
        body=file_metadata, fields="id", supportsAllDrives=True,
    ).execute()
    return created["id"]


def list_subfolders(service, parent_id: str) -> list[dict]:
    """親直下のサブフォルダ全件。"""
    q = (
        f"'{parent_id}' in parents and "
        f"mimeType='{DRIVE_FOLDER_MIME}' and trashed=false"
    )
    items, page_token = [], None
    while True:
        res = service.files().list(
            q=q, fields="nextPageToken, files(id,name)", pageSize=100,
            pageToken=page_token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        items.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return items


def list_csvs_in_folder(service, folder_id: str, extensions: list[str]) -> list[dict]:
    """フォルダ直下のCSV一覧。"""
    q = f"'{folder_id}' in parents and trashed=false and mimeType!='{DRIVE_FOLDER_MIME}'"
    items, page_token = [], None
    while True:
        res = service.files().list(
            q=q,
            fields="nextPageToken, files(id,name,size,modifiedTime,parents)",
            pageSize=100, pageToken=page_token,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        items.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    ext_lower = {e.lower() for e in extensions}
    return [it for it in items if Path(it["name"]).suffix.lower() in ext_lower]


def ensure_facility_folders(service, root_id: str, names: list[str]) -> dict[str, str]:
    """損益ダッシュボードの親グループ名でサブフォルダを保証作成。"""
    return {n: ensure_subfolder(service, root_id, n) for n in names}


def get_folder_name(service, folder_id: str) -> str:
    res = service.files().get(
        fileId=folder_id, fields="name", supportsAllDrives=True,
    ).execute()
    return res["name"]


# =====================================================================
# ダウンロード / 移動
# =====================================================================

def download_file_bytes(service, file_id: str) -> bytes:
    """Driveからファイル本体をバイト列で取得。"""
    from googleapiclient.http import MediaIoBaseDownload
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _status, done = downloader.next_chunk()
    return buf.getvalue()


def move_file(service, file_id: str, new_parent_id: str) -> None:
    """親IDを差し替えてファイルを移動。"""
    file = service.files().get(
        fileId=file_id, fields="parents", supportsAllDrives=True,
    ).execute()
    prev_parents = ",".join(file.get("parents", []))
    service.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=prev_parents,
        fields="id, parents",
        supportsAllDrives=True,
    ).execute()


# =====================================================================
# 施設名 (損益ダッシュボードの親グループ)
# =====================================================================

def facility_folder_names() -> list[str]:
    """Drive側に作るべきフォルダ名のリスト。
    `<code>_<group_name>` フォーマット (例: '001_SORATO（UMIE）いなみ')。"""
    groups = db.list_pl_groups(active_only=True)
    return [_folder_name_for_group(g) for g in groups]


def _folder_name_for_group(group: dict) -> str:
    code = group.get("code") or ""
    name = group.get("name") or ""
    return f"{code}_{name}" if code else name


def _resolve_destination_folder_name(facility: dict) -> str:
    """売上入金facility → 損益ダッシュボードのPLグループ折り名 (例 '003_SORATO（UMIE）てんり')。
    名前が一致するPLグループがあればそのフォルダ名、なければ売上入金側のshort_code+nameでフォールバック。"""
    facility_name = facility.get("facility_name") or ""
    for g in db.list_pl_groups(active_only=True):
        if (g.get("name") or "") == facility_name:
            return _folder_name_for_group(g)
    short = facility.get("short_code") or ""
    return f"{short}_{facility_name}" if short else facility_name


# =====================================================================
# 同期オーケストレータ
# =====================================================================

@dataclass
class SyncResult:
    folders_created: list[str] = field(default_factory=list)
    files_processed: list[dict] = field(default_factory=list)
    files_skipped: list[dict] = field(default_factory=list)
    files_failed: list[dict] = field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    def to_dict(self):
        return {
            "folders_created": self.folders_created,
            "files_processed": self.files_processed,
            "files_skipped": self.files_skipped,
            "files_failed": self.files_failed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": {
                "processed": len(self.files_processed),
                "skipped": len(self.files_skipped),
                "failed": len(self.files_failed),
            },
        }


def sync(
    config: Optional[dict] = None,
    on_log: Optional[Callable[[str], None]] = None,
    create_missing_folders: bool = True,
) -> SyncResult:
    """ワンショット同期。

    手順:
      1) 施設サブフォルダを保証作成 (損益ダッシュボードの親基準)
      2) 各施設サブフォルダ直下のCSVを列挙
      3) ダウンロード → csv_parser で解析 → upsert
      4) 成功したものは _処理済/<元フォルダ名>/ に移動
    """
    if config is None:
        config = load_config()
    log = on_log or (lambda m: None)

    result = SyncResult()
    result.started_at = _now_iso()

    service = build_service(config)
    root_id = config["root_folder_id"]
    processed_name = config.get("processed_folder_name", "_処理済")
    extensions = config.get("csv_extensions", [".csv", ".CSV"])
    max_size = int(config.get("max_file_size_mb", 10)) * 1024 * 1024

    # 1) 施設フォルダ作成
    if create_missing_folders:
        existing = {f["name"] for f in list_subfolders(service, root_id)}
        for name in facility_folder_names():
            if name not in existing:
                ensure_subfolder(service, root_id, name)
                result.folders_created.append(name)
                log(f"作成: {name}")

    # 処理済ルートを保証
    processed_root_id = ensure_subfolder(service, root_id, processed_name)

    # 2) ルート直下 + 各サブフォルダのCSVを処理
    #    施設サブフォルダと、ルート直下に直接置かれたCSV、両方が対象。
    #    取込後の移動先は CSVから判定した施設名 → _処理済/<施設フォルダ名>/
    scan_targets: list[tuple[str, str]] = [("（ルート）", root_id)]
    for sub in list_subfolders(service, root_id):
        if sub["name"] == processed_name:
            continue
        scan_targets.append((sub["name"], sub["id"]))

    for src_label, folder_id in scan_targets:
        csvs = list_csvs_in_folder(service, folder_id, extensions)
        if not csvs:
            continue
        log(f"フォルダ '{src_label}': {len(csvs)} ファイル検出")
        for f in csvs:
            try:
                size = int(f.get("size", 0))
            except (TypeError, ValueError):
                size = 0
            if size > max_size:
                log(f"  スキップ (サイズ上限超過): {f['name']}")
                result.files_skipped.append(_file_info(f, src_label, "サイズ上限超過"))
                continue
            try:
                imported = _process_one_csv(service, f, src_label, processed_root_id, log)
                result.files_processed.append(imported)
                log(f"  取込OK: {f['name']} → {imported['service_year_month']} "
                    f"{imported['facility_name']} → 処理済/{imported['dest_folder']} "
                    f"({imported['inserted']}新規/{imported['updated']}更新)")
            except _SkipReason as e:
                log(f"  スキップ: {f['name']} ({e})")
                result.files_skipped.append(_file_info(f, src_label, str(e)))
            except Exception as e:
                log(f"  エラー: {f['name']}: {e}")
                result.files_failed.append(_file_info(f, src_label, str(e)))

    result.finished_at = _now_iso()
    return result


# =====================================================================
# 1件処理 (private)
# =====================================================================

class _SkipReason(Exception):
    pass


def _process_one_csv(service, file: dict, src_folder_name: str,
                      processed_root_id: str, log: Callable[[str], None]) -> dict:
    """1ファイルをDLして取込。成功したら処理済へ移動。"""
    data = download_file_bytes(service, file["id"])
    parsed = csv_parser.parse_csv_bytes(data)

    if "error" in parsed:
        raise _SkipReason(f"解析エラー: {parsed['error']}")
    if not parsed["records"]:
        raise _SkipReason("K122-01 / J121-01 レコードなし")
    if not parsed["csv_facility_codes"]:
        raise _SkipReason("事業所番号なし")

    csv_code = parsed["csv_facility_codes"][0]
    facility = db.get_facility_by_csv_code(csv_code)
    if not facility:
        raise _SkipReason(
            f"事業所コード {csv_code} が施設マスタに未紐付け。"
            "Streamlit UI でCSVを一度アップして紐付けしてください。"
        )

    import_id = db.create_import(
        facility_id=facility["id"],
        service_ym=parsed["service_year_month"],
        billing_ym=parsed["billing_year_month"],
        filename=f"[Drive] {file['name']}",
        file_hash=parsed["file_hash"],
        row_count=parsed["row_count"],
    )

    inserted = updated = 0
    for rec in parsed["records"]:
        rec = dict(rec)
        rec["facility_id"] = facility["id"]
        if db.upsert_monthly_record(rec, import_id) == "inserted":
            inserted += 1
        else:
            updated += 1

    # 処理済へ移動 (CSVから判定した施設名で振り分け、ソースフォルダの場所は無関係)
    dest_folder_name = _resolve_destination_folder_name(facility)
    dest_folder_id = ensure_subfolder(service, processed_root_id, dest_folder_name)
    move_file(service, file["id"], dest_folder_id)

    return {
        "file_id": file["id"],
        "filename": file["name"],
        "src_folder": src_folder_name,
        "dest_folder": dest_folder_name,
        "service_year_month": parsed["service_year_month"],
        "facility_name": facility["facility_name"],
        "inserted": inserted,
        "updated": updated,
        "row_count": parsed["row_count"],
    }


def _file_info(file: dict, folder_name: str, reason: str) -> dict:
    return {
        "file_id": file.get("id"),
        "filename": file.get("name"),
        "src_folder": folder_name,
        "reason": reason,
    }


def _now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
