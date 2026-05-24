"""SQLite/Tursoデータベース操作モジュール"""
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "uriage.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LOCAL_REPLICA_PATH = Path(__file__).resolve().parent.parent / "data" / "cloud_replica.db"
_CLOUD_CONNECTION = None
_CLOUD_CONNECTION_KEY = None

USER_POSITION_PRESETS = {
    "kamada.rusk@emifull-group.or.jp": "部長",
    "oketani.msm@emifull-group.or.jp": "係長",
    "nishitsuji.msys@emifull-group.or.jp": "係長",
    "kanbe.tkhr@emifull-group.or.jp": "係長",
    "fukaya.kkr@emifull-group.or.jp": "課長",
    "morita.yshr@emifull-group.or.jp": "次長",
    "kuroda.yusk@emifull-group.or.jp": "副主任",
}


def _secret_value(key):
    value = os.getenv(key)
    if value:
        return value
    try:
        import streamlit as st

        return st.secrets.get(key)
    except Exception:
        return None


def _use_cloud_db():
    return bool(_secret_value("TURSO_DATABASE_URL") and _secret_value("TURSO_AUTH_TOKEN"))


def init_login_schema():
    """Create only the tables needed before the login screen is shown."""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
            name TEXT,
            position TEXT,
            password_hash TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS login_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            email TEXT NOT NULL,
            name TEXT,
            role TEXT,
            login_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            logout_at TEXT,
            logout_reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)
        user_cols = {
            r['name'] for r in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if 'password_hash' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if 'position' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN position TEXT")
        _ensure_user_position_presets(conn)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_login_events_login_at "
            "ON login_events(login_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_login_events_email "
            "ON login_events(email)"
        )


class _CloudRow:
    def __init__(self, columns, values):
        self._columns = list(columns)
        self._values = tuple(values)
        self._index = {name: idx for idx, name in enumerate(self._columns)}

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._columns)

    def __repr__(self):
        return repr(dict(zip(self._columns, self._values)))


class _CloudCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    @property
    def _columns(self):
        description = getattr(self._cursor, "description", None) or ()
        return [col[0] for col in description]

    def _row(self, row):
        if row is None or not self._columns:
            return row
        return _CloudRow(self._columns, row)

    def fetchone(self):
        return self._row(self._cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self._cursor.fetchall()]

    def fetchmany(self, size=None):
        if size is None:
            rows = self._cursor.fetchmany()
        else:
            rows = self._cursor.fetchmany(size)
        return [self._row(row) for row in rows]

    def __iter__(self):
        for row in self._cursor:
            yield self._row(row)


class _CloudConnection:
    def __init__(self, conn):
        object.__setattr__(self, "_conn", conn)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name == "_conn":
            object.__setattr__(self, name, value)
            return
        try:
            setattr(self._conn, name, value)
        except Exception:
            object.__setattr__(self, name, value)

    def execute(self, sql, params=()):
        if params is None:
            params = ()
        return _CloudCursor(self._conn.execute(sql, params))

    def executemany(self, sql, seq_of_params):
        return _CloudCursor(self._conn.executemany(sql, seq_of_params))

    def executescript(self, sql_script):
        statement = ""
        for line in sql_script.splitlines():
            statement += line + "\n"
            if sqlite3.complete_statement(statement):
                sql = statement.strip()
                if sql:
                    self._conn.execute(sql)
                statement = ""
        if statement.strip():
            self._conn.execute(statement)


def _connect_cloud():
    global _CLOUD_CONNECTION, _CLOUD_CONNECTION_KEY
    try:
        import libsql
    except ImportError as exc:
        raise RuntimeError(
            "クラウドDBへ接続するには requirements.txt の libsql が必要です。"
        ) from exc

    database = _secret_value("TURSO_DATABASE_URL")
    auth_token = _secret_value("TURSO_AUTH_TOKEN")
    key = (database, auth_token)
    if _CLOUD_CONNECTION is not None and _CLOUD_CONNECTION_KEY == key:
        return _CloudConnection(_CLOUD_CONNECTION)

    conn = libsql.connect(
        database=database,
        auth_token=auth_token,
    )
    _CLOUD_CONNECTION = conn
    _CLOUD_CONNECTION_KEY = key
    return _CloudConnection(conn)


# 事前登録する施設マスタ (short_code, 施設名, CSV事業所番号)
# CSVコードが None のものは未取得（申請中など）
FACILITY_PRESETS = [
    ("001", "SORATO（UMIE）いなみ",            "2852801279"),
    ("002", "SORATO（UMIE）いなみ第二教室",     "2852801287"),
    ("003", "SORATO（UMIE）てんり",            "2950900056"),
    ("004", "ジョブカレッジかこがわ",           "2852201801"),
    ("005", "カラダキッズかこがわ",             "2852201819"),
    ("006", "Hinodeシェアホーム天理",           "2920900160"),
    ("007", "Hinodeシェアホーム加古川",         "2822200305"),
    ("008", "相談支援NOAH加古川",               "2832210336"),
    ("009", "のじぎく高砂",                     "2812100333"),
    ("010", "のじぎく稲美",                     "2812800312"),
    ("011", "カラダキッズてんり",               None),  # 申請中
]

# 回収手段の選択肢（区分ごとに使い分け）
PAYMENT_METHODS_SELF = ["SMBC", "振込", "現金", "その他"]    # 自己負担用
PAYMENT_METHODS_KOKUHO = ["国保", "自己"]                    # 国保請求用
PAYMENT_METHODS = PAYMENT_METHODS_SELF + PAYMENT_METHODS_KOKUHO  # 全選択肢


def get_payment_methods(kbn):
    """区分(kbn)に応じた回収手段の選択肢を返す。
    kbn = 'self' → 自己負担用 / 'kokuho' → 国保請求用"""
    return PAYMENT_METHODS_SELF if kbn == 'self' else PAYMENT_METHODS_KOKUHO


def _ensure_user_position_presets(conn):
    """既存ユーザーに既定の役職を補完する。手入力済みの役職は上書きしない。"""
    for email, position in USER_POSITION_PRESETS.items():
        conn.execute(
            """
            UPDATE users
               SET position = ?
             WHERE lower(email) = ?
               AND (position IS NULL OR trim(position) = '')
            """,
            (position, email),
        )


@contextmanager
def get_conn():
    if _use_cloud_db():
        conn = _connect_cloud()
    else:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if not _use_cloud_db():
            conn.close()


def init_db():
    """初回起動時にテーブル作成。旧スキーマを検知したら自動リセット。"""
    with get_conn() as conn:
        # 旧スキーマ検出（facility_codeカラムがあるが short_code が無い）
        try:
            cols = {r['name'] for r in conn.execute("PRAGMA table_info(facilities)").fetchall()}
        except Exception:
            cols = set()
        if cols and 'short_code' not in cols:
            # スキーマ変更：すべてリセット（PoC段階のため許容）
            conn.executescript("""
                DROP TABLE IF EXISTS monthly_records;
                DROP TABLE IF EXISTS imports;
                DROP TABLE IF EXISTS facilities;
            """)

        conn.executescript("""
        CREATE TABLE IF NOT EXISTS facilities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            short_code TEXT UNIQUE NOT NULL,        -- '001'〜'011'
            facility_name TEXT NOT NULL,
            csv_facility_code TEXT UNIQUE,          -- CSVの10桁コード（任意、未紐付けはNULL）
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            facility_id INTEGER REFERENCES facilities(id),
            service_year_month TEXT NOT NULL,
            billing_year_month TEXT,
            source_filename TEXT,
            file_hash TEXT,
            row_count INTEGER,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS monthly_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_year_month TEXT NOT NULL,
            billing_year_month TEXT,
            facility_id INTEGER NOT NULL REFERENCES facilities(id),
            cert_number TEXT NOT NULL,
            guardian_name TEXT,
            child_name TEXT,
            fee_limit INTEGER,
            total_cost INTEGER,
            -- 請求額（CSVから自動入力）
            self_charge INTEGER,            -- 決定利用者負担額（自己負担）
            kokuho_charge INTEGER,          -- 請求額給付費（国保請求）
            -- 自己負担の入金トラッキング
            self_paid_amount INTEGER DEFAULT 0,
            self_paid_date DATE,
            self_payment_method TEXT,
            self_payment_status TEXT DEFAULT '未入金',
            -- 国保請求の入金トラッキング
            kokuho_paid_amount INTEGER DEFAULT 0,
            kokuho_paid_date DATE,
            kokuho_payment_method TEXT,
            kokuho_payment_status TEXT DEFAULT '未入金',
            memo TEXT,
            source_import_id INTEGER REFERENCES imports(id),
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (service_year_month, facility_id, cert_number)
        );

        CREATE INDEX IF NOT EXISTS idx_records_ym ON monthly_records(service_year_month);
        CREATE INDEX IF NOT EXISTS idx_records_facility ON monthly_records(facility_id);

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
            name TEXT,
            position TEXT,
            password_hash TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS login_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            email TEXT NOT NULL,
            name TEXT,
            role TEXT,
            login_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            logout_at TEXT,
            logout_reason TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # === マイグレーション: 既存DBに password_hash 列を追加 ===
        user_cols = {
            r['name'] for r in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if 'password_hash' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if 'position' not in user_cols:
            conn.execute("ALTER TABLE users ADD COLUMN position TEXT")
        _ensure_user_position_presets(conn)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_login_events_login_at "
            "ON login_events(login_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_login_events_email "
            "ON login_events(email)"
        )

        # 初期管理者(kamada)を投入。emifull123 を pbkdf2_sha256 でハッシュ化したもの。
        # 平文を残さないよう、初期投入時のみ事前計算済みハッシュを使う。
        # 実装: lib.auth.hash_password('emifull123') を一度実行して得たハッシュを格納。
        # ※ ハッシュは毎回ランダムソルト生成するため、別環境で計算しても同じ値にはならない。
        #    ここでは初回起動時にハッシュ生成し、未登録のときだけINSERTする。
        existing = conn.execute(
            "SELECT id, password_hash FROM users WHERE email = ?",
            ('kamada.rusk@emifull-group.or.jp',)
        ).fetchone()
        if existing is None:
            from lib.auth import hash_password  # 循環import回避のため遅延import
            conn.execute("""
                INSERT INTO users (email, role, name, password_hash)
                VALUES (?, 'admin', ?, ?)
            """, (
                'kamada.rusk@emifull-group.or.jp',
                '管理者(初期)',
                hash_password('emifull123'),
            ))
        elif existing['password_hash'] is None:
            # メールだけ既登録でパスワード未設定なら、初期パスワードを当てる
            from lib.auth import hash_password
            conn.execute(
                "UPDATE users SET password_hash = ?, role = 'admin' WHERE id = ?",
                (hash_password('emifull123'), existing['id']),
            )

        # === マイグレーション: 区分別の備考列を追加 ===
        record_cols = {
            r['name'] for r in conn.execute("PRAGMA table_info(monthly_records)").fetchall()
        }
        if 'self_memo' not in record_cols:
            conn.execute("ALTER TABLE monthly_records ADD COLUMN self_memo TEXT")
        if 'kokuho_memo' not in record_cols:
            conn.execute("ALTER TABLE monthly_records ADD COLUMN kokuho_memo TEXT")

        # === マイグレーション: 請求額0円の'未入金'を'対象外'へ修正 ===
        conn.execute("""
            UPDATE monthly_records
            SET self_payment_status = '対象外'
            WHERE (self_charge IS NULL OR self_charge <= 0)
              AND (self_payment_status = '未入金' OR self_payment_status IS NULL)
        """)
        conn.execute("""
            UPDATE monthly_records
            SET kokuho_payment_status = '対象外'
            WHERE (kokuho_charge IS NULL OR kokuho_charge <= 0)
              AND (kokuho_payment_status = '未入金' OR kokuho_payment_status IS NULL)
        """)

        # 損益（部門別 P&L）スキーマを初期化
        # ※ 関数末尾で呼ぶ（このトランザクションとは別接続）
        # 事前登録施設をUPSERT（CSV事業所コード含む）
        for short_code, name, csv_code in FACILITY_PRESETS:
            # 同じCSVコードが別のshort_codeに紐付いていたらクリア（衝突回避）
            if csv_code:
                conn.execute("""
                    UPDATE facilities SET csv_facility_code = NULL
                    WHERE csv_facility_code = ? AND short_code != ?
                """, (csv_code, short_code))
            conn.execute("""
                INSERT INTO facilities (short_code, facility_name, csv_facility_code)
                VALUES (?, ?, ?)
                ON CONFLICT(short_code) DO UPDATE SET
                    facility_name = excluded.facility_name,
                    csv_facility_code = excluded.csv_facility_code
            """, (short_code, name, csv_code))

    # 損益スキーマ初期化（with get_conn() を抜けてから別トランザクションで実行）
    init_pl_schema()
    init_journal_schema()
    init_payroll_schema()
    init_debit_schema()
    init_vehicle_schema()
    init_cash_advance_schema()
    init_profit_reports_schema()


# === 施設マスタ ===

def list_facilities():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM facilities ORDER BY short_code"
        ).fetchall()]


def get_facility_by_csv_code(csv_code):
    """CSVの10桁コードから施設を引く"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM facilities WHERE csv_facility_code = ?", (csv_code,)
        ).fetchone()
        return dict(row) if row else None


def get_facility_by_short_code(short_code):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM facilities WHERE short_code = ?", (short_code,)
        ).fetchone()
        return dict(row) if row else None


def update_facility_csv_code(facility_id, csv_code):
    """既存施設にCSVコードを紐付け"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE facilities SET csv_facility_code = ? WHERE id = ?",
            (csv_code, facility_id)
        )


def update_facility(facility_id, short_code, facility_name, csv_facility_code=None):
    """施設情報の編集"""
    with get_conn() as conn:
        conn.execute("""
            UPDATE facilities
            SET short_code = ?, facility_name = ?, csv_facility_code = ?
            WHERE id = ?
        """, (short_code, facility_name, csv_facility_code or None, facility_id))


# === 取込 ===

def create_import(facility_id, service_ym, billing_ym, filename, file_hash, row_count):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO imports
            (facility_id, service_year_month, billing_year_month,
             source_filename, file_hash, row_count)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (facility_id, service_ym, billing_ym, filename, file_hash, row_count))
        return cur.lastrowid


def get_existing_records(facility_id, service_ym):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT * FROM monthly_records
            WHERE facility_id = ? AND service_year_month = ?
        """, (facility_id, service_ym)).fetchall()]


def upsert_monthly_record(rec, import_id):
    """1件UPSERT。既存の入金情報は保持。"""
    with get_conn() as conn:
        existing = conn.execute("""
            SELECT id FROM monthly_records
            WHERE service_year_month = ? AND facility_id = ? AND cert_number = ?
        """, (rec['service_year_month'], rec['facility_id'], rec['cert_number'])).fetchone()

        now = datetime.now().isoformat(sep=' ', timespec='seconds')

        if existing:
            # 請求額系のみ更新、入金関連は保持
            conn.execute("""
                UPDATE monthly_records SET
                  billing_year_month = ?,
                  guardian_name = ?, child_name = ?,
                  fee_limit = ?, total_cost = ?,
                  self_charge = ?, kokuho_charge = ?,
                  source_import_id = ?, updated_at = ?
                WHERE id = ?
            """, (
                rec.get('billing_year_month'),
                rec.get('guardian_name'), rec.get('child_name'),
                rec.get('fee_limit'), rec.get('total_cost'),
                rec.get('self_charge'), rec.get('kokuho_charge'),
                import_id, now, existing['id']
            ))
            return 'updated'
        else:
            conn.execute("""
                INSERT INTO monthly_records
                (service_year_month, billing_year_month, facility_id, cert_number,
                 guardian_name, child_name, fee_limit, total_cost,
                 self_charge, kokuho_charge, source_import_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rec['service_year_month'], rec.get('billing_year_month'),
                rec['facility_id'], rec['cert_number'],
                rec.get('guardian_name'), rec.get('child_name'),
                rec.get('fee_limit'), rec.get('total_cost'),
                rec.get('self_charge'), rec.get('kokuho_charge'),
                import_id
            ))
            return 'inserted'


# === レコード取得・更新 ===

def list_records(service_ym=None, facility_id=None):
    """フィルタ条件付きで一覧取得（差額計算込み）"""
    sql = """
        SELECT r.*, f.short_code AS facility_short_code, f.facility_name,
               (COALESCE(r.self_charge, 0) - COALESCE(r.self_paid_amount, 0)) AS self_diff,
               (COALESCE(r.kokuho_charge, 0) - COALESCE(r.kokuho_paid_amount, 0)) AS kokuho_diff
        FROM monthly_records r
        JOIN facilities f ON f.id = r.facility_id
        WHERE 1=1
    """
    params = []
    if service_ym:
        sql += " AND r.service_year_month = ?"
        params.append(service_ym)
    if facility_id:
        sql += " AND r.facility_id = ?"
        params.append(facility_id)
    sql += " ORDER BY r.service_year_month, f.short_code, r.id"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_records_for_child(facility_id, cert_number):
    """1利用者の全月データ"""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("""
            SELECT r.*, f.short_code AS facility_short_code, f.facility_name
            FROM monthly_records r
            JOIN facilities f ON f.id = r.facility_id
            WHERE r.facility_id = ? AND r.cert_number = ?
            ORDER BY r.service_year_month
        """, (facility_id, cert_number)).fetchall()]


def _compute_status(charge, paid):
    """請求額0円なら'対象外'（未収金にカウントしない）"""
    charge = charge or 0
    paid = paid or 0
    if charge <= 0:
        return '対象外'
    if paid <= 0:
        return '未入金'
    if paid < charge:
        return '一部入金'
    return '入金済'


def update_payment(record_id, kbn, paid_amount, paid_date, method, memo=None):
    """入金記録更新。kbn = 'self' or 'kokuho'"""
    update_record(record_id, kbn,
                  paid_amount=paid_amount, paid_date=paid_date,
                  method=method, memo=memo)


def update_record(record_id, kbn,
                  charge=None, paid_amount=None, paid_date=None,
                  method=None, memo=None):
    """請求額・回収額・回収手段・入金日・備考を一括更新。
    kbn = 'self' or 'kokuho'。memo は kbn 別の備考列に書き込む。"""
    if kbn not in ('self', 'kokuho'):
        raise ValueError(f"kbnは 'self' または 'kokuho' を指定: {kbn}")

    with get_conn() as conn:
        rec = conn.execute(
            "SELECT * FROM monthly_records WHERE id = ?", (record_id,)
        ).fetchone()
        if not rec:
            raise ValueError(f"レコードが見つかりません: id={record_id}")

        prefix = kbn  # 'self' or 'kokuho'
        new_charge = charge if charge is not None else rec[f'{prefix}_charge']
        new_paid = paid_amount if paid_amount is not None else rec[f'{prefix}_paid_amount']
        status = _compute_status(new_charge, new_paid)
        now = datetime.now().isoformat(sep=' ', timespec='seconds')

        # memo: None なら既存値を保持、'' なら明示的に空文字に上書き
        if memo is None:
            memo_clause = f"{prefix}_memo = {prefix}_memo"
            memo_param = None
        else:
            memo_clause = f"{prefix}_memo = ?"
            memo_param = memo

        if memo_param is None:
            conn.execute(f"""
                UPDATE monthly_records SET
                  {prefix}_charge = ?,
                  {prefix}_paid_amount = ?,
                  {prefix}_paid_date = ?,
                  {prefix}_payment_method = ?,
                  {prefix}_payment_status = ?,
                  updated_at = ?
                WHERE id = ?
            """, (new_charge, new_paid or 0, paid_date, method, status, now, record_id))
        else:
            conn.execute(f"""
                UPDATE monthly_records SET
                  {prefix}_charge = ?,
                  {prefix}_paid_amount = ?,
                  {prefix}_paid_date = ?,
                  {prefix}_payment_method = ?,
                  {prefix}_payment_status = ?,
                  {prefix}_memo = ?,
                  updated_at = ?
                WHERE id = ?
            """, (new_charge, new_paid or 0, paid_date, method, status,
                  memo_param, now, record_id))


def list_year_months():
    with get_conn() as conn:
        return [r['service_year_month'] for r in conn.execute(
            "SELECT DISTINCT service_year_month FROM monthly_records ORDER BY service_year_month DESC"
        ).fetchall()]


def list_records_in_range(start_ym=None, end_ym=None, facility_id=None):
    """指定期間（YYYY-MM）内のレコード一覧"""
    sql = """
        SELECT r.*, f.short_code AS facility_short_code, f.facility_name,
               (COALESCE(r.self_charge, 0) - COALESCE(r.self_paid_amount, 0)) AS self_diff,
               (COALESCE(r.kokuho_charge, 0) - COALESCE(r.kokuho_paid_amount, 0)) AS kokuho_diff
        FROM monthly_records r
        JOIN facilities f ON f.id = r.facility_id
        WHERE 1=1
    """
    params = []
    if start_ym:
        sql += " AND r.service_year_month >= ?"
        params.append(start_ym)
    if end_ym:
        sql += " AND r.service_year_month <= ?"
        params.append(end_ym)
    if facility_id:
        sql += " AND r.facility_id = ?"
        params.append(facility_id)
    sql += " ORDER BY r.service_year_month, f.short_code, r.id"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# === 年度ユーティリティ（4月始まり） ===

def fiscal_year_of(yyyymm):
    """'2025-12' -> 2025（年度）/ '2026-03' -> 2025"""
    y, m = int(yyyymm[:4]), int(yyyymm[5:7])
    return y if m >= 4 else y - 1


def fiscal_year_range(fy):
    """fy=2025 -> ('2025-04', '2026-03')"""
    return f"{fy}-04", f"{fy + 1}-03"


def fiscal_year_months(fy):
    """fy=2025 -> ['2025-04','2025-05',...,'2026-03']"""
    months = []
    for i in range(12):
        m = 4 + i
        if m <= 12:
            months.append(f"{fy}-{m:02d}")
        else:
            months.append(f"{fy + 1}-{m - 12:02d}")
    return months


def list_fiscal_years():
    """データが存在する年度の一覧（降順）"""
    yms = list_year_months()
    return sorted({fiscal_year_of(ym) for ym in yms}, reverse=True)


# === ユーザー管理 ===

def _now_jst_str():
    return datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M:%S")


def record_login_event(user):
    """ログイン成功を記録し、作成した履歴IDを返す。"""
    if not user:
        return None
    now = _now_jst_str()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO login_events (
                user_id, email, name, role, login_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user.get('id'),
            (user.get('email') or '').strip().lower(),
            user.get('name'),
            user.get('role'),
            now,
            now,
        ))
        row = conn.execute("SELECT last_insert_rowid() AS id").fetchone()
        if not row:
            return None
        try:
            return row['id']
        except (KeyError, TypeError, IndexError):
            return row[0]


def touch_login_event(event_id):
    """ログイン中セッションの最終操作時刻を更新する。"""
    if not event_id:
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE login_events SET last_seen_at = ? WHERE id = ? AND logout_at IS NULL",
            (_now_jst_str(), event_id),
        )


def record_logout_event(event_id, reason='手動ログアウト'):
    """ログアウト時刻を記録する。"""
    if not event_id:
        return
    now = _now_jst_str()
    with get_conn() as conn:
        conn.execute("""
            UPDATE login_events
            SET logout_at = ?, last_seen_at = ?, logout_reason = ?
            WHERE id = ? AND logout_at IS NULL
        """, (now, now, reason, event_id))


def list_login_events(limit=300):
    limit = max(1, min(int(limit or 300), 2000))
    columns = [
        'id', 'user_id', 'email', 'name', 'role',
        'login_at', 'last_seen_at', 'logout_at', 'logout_reason',
    ]
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                id, user_id, email, name, role,
                login_at, last_seen_at, logout_at, logout_reason
            FROM login_events
            ORDER BY login_at DESC, id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    results = []
    for row in rows:
        if hasattr(row, 'keys'):
            results.append({key: row[key] for key in row.keys()})
        else:
            results.append(dict(zip(columns, row)))
    return results


def get_user_by_email(email):
    if not email:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.strip().lower(),)
        ).fetchone()
        return dict(row) if row else None


def list_users():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM users ORDER BY role DESC, email"
        ).fetchall()]


def add_user(email, role, name=None, position=None):
    if role not in ('admin', 'user'):
        raise ValueError(f"role は 'admin' または 'user': {role}")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO users (email, role, name, position) VALUES (?, ?, ?, ?)",
            (email.strip().lower(), role, name, position)
        )


def set_user_password(user_id, password_hash):
    """パスワードハッシュを保存（auth.hash_passwordで生成済みの文字列を渡すこと）"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )


def clear_user_password(user_id):
    """パスワードを未設定状態に戻す（管理者操作用）"""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET password_hash = NULL WHERE id = ?",
            (user_id,),
        )


# === 一括削除（管理者専用） ===

def count_records(service_ym=None, facility_id=None):
    """指定条件にマッチするmonthly_recordsの件数を返す。Noneの引数は条件にしない。"""
    sql = "SELECT COUNT(*) AS c FROM monthly_records WHERE 1=1"
    params = []
    if service_ym is not None:
        sql += " AND service_year_month = ?"
        params.append(service_ym)
    if facility_id is not None:
        sql += " AND facility_id = ?"
        params.append(facility_id)
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()['c']


def delete_records(service_ym=None, facility_id=None):
    """指定条件のmonthly_recordsを削除。Noneの引数は条件にしない。
    関連する imports 履歴も同条件で削除する。
    戻り値: (削除レコード数, 削除import数)
    """
    rec_sql = "DELETE FROM monthly_records WHERE 1=1"
    imp_sql = "DELETE FROM imports WHERE 1=1"
    params = []
    if service_ym is not None:
        rec_sql += " AND service_year_month = ?"
        imp_sql += " AND service_year_month = ?"
        params.append(service_ym)
    if facility_id is not None:
        rec_sql += " AND facility_id = ?"
        imp_sql += " AND facility_id = ?"
        params.append(facility_id)
    with get_conn() as conn:
        rec_cur = conn.execute(rec_sql, params)
        rec_count = rec_cur.rowcount
        imp_cur = conn.execute(imp_sql, params)
        imp_count = imp_cur.rowcount
        return rec_count, imp_count


def delete_all_records():
    """monthly_records と imports を全削除（施設マスタ・ユーザーは保持）。
    戻り値: (削除レコード数, 削除import数)
    """
    with get_conn() as conn:
        rec_count = conn.execute("DELETE FROM monthly_records").rowcount
        imp_count = conn.execute("DELETE FROM imports").rowcount
        return rec_count, imp_count


def update_user(user_id, email=None, role=None, name=None, position=None):
    sets = []
    params = []
    if email is not None:
        sets.append("email = ?")
        params.append(email.strip().lower())
    if role is not None:
        if role not in ('admin', 'user'):
            raise ValueError(f"role は 'admin' または 'user': {role}")
        sets.append("role = ?")
        params.append(role)
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if position is not None:
        sets.append("position = ?")
        params.append(position)
    if not sets:
        return
    params.append(user_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", params)


def delete_user(user_id):
    with get_conn() as conn:
        target = conn.execute(
            "SELECT role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if not target:
            return
        if target['role'] == 'admin':
            admin_count = conn.execute(
                "SELECT COUNT(*) AS c FROM users WHERE role = 'admin'"
            ).fetchone()['c']
            if admin_count <= 1:
                raise ValueError("最後の管理者は削除できません")
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


# === 集計 ===

def summary_by_facility(service_ym=None):
    sql = """
        SELECT f.short_code, f.facility_name,
               COUNT(*) AS count,
               SUM(COALESCE(r.self_charge, 0)) AS self_charge_total,
               SUM(COALESCE(r.self_paid_amount, 0)) AS self_paid_total,
               SUM(COALESCE(r.kokuho_charge, 0)) AS kokuho_charge_total,
               SUM(COALESCE(r.kokuho_paid_amount, 0)) AS kokuho_paid_total
        FROM monthly_records r
        JOIN facilities f ON f.id = r.facility_id
        WHERE 1=1
    """
    params = []
    if service_ym:
        sql += " AND r.service_year_month = ?"
        params.append(service_ym)
    sql += " GROUP BY f.id ORDER BY f.short_code"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# =====================================================================
# 損益（部門別 P&L）モジュール
# =====================================================================
#
# 設計方針:
#   - pl_groups : 管理単位（10件）
#   - pl_subunits : 各グループに属する Excel 列名（複数）
#   - pl_accounts : 科目マスタ（売上12 + 売上原価10 + 販管費54 + 営業外/特損 ほか）
#   - pl_entries : 月次値（subunit × account × year_month）
#
# Excel の列順は毎月変わりうるため、列名（excel_name）でマッチする。
# 行（科目名）は安定している前提で、column A の文字列で照合。

# 管理対象10グループとサブ部門（Excel列名→表示名）の対応
# サブ部門は (excel_name, display_name) のタプル。
# excel_name は Excel 取込時のマッチに使う列名、display_name はUI表示用。
PL_GROUPS_SEED = [
    # (code, group_name, [(excel_name, display_name), ...], note)
    ("001", "SORATO（UMIE）いなみ",
     [("SORATOいなみ", "SORATOいなみ"),
      ("UMIEいなみ", "UMIEいなみ")], None),
    ("002", "SORATO（UMIE）いなみ第二教室",
     [("UMIEいなみ2", "UMIEいなみ第二教室"),
      ("SORATOいなみ2", "SORATOいなみ第二教室"),
      ("BLOOMいなみ", "BLOOMいなみ")], None),
    ("003", "SORATO（UMIE）てんり",
     [("SORATOてんり", "SORATOてんり"),
      ("UMIEてんり", "UMIEてんり"),
      ("BLOOMてんり", "BLOOMてんり")], None),
    ("004", "SORATO（UMIE）きたはま",
     [("SORATOUMIEきたはま", "SORATOUMIEきたはま")], "閉鎖済"),
    ("005", "Hinodeシェアホーム天理",
     [("シェア天理1.2", "シェア天理1.2"),
      ("シェア天理3", "シェア天理3"),
      ("シェアホーム天理1.2", "シェアホーム天理1.2"),
      ("シェアホーム天理3", "シェアホーム天理3")], None),
    ("006", "ジョブカレッジかこがわ",
     [("ジョブカレッジかこがわ", "ジョブカレッジかこがわ"),
      ("ナインカレッジ", "ナインカレッジ")], None),
    ("007", "カラダキッズかこがわ",
     [("カラダキッズかこがわ", "カラダキッズかこがわ"),
      ("ナインキッズ", "ナインキッズ")], None),
    ("008", "カラダキッズてんり",
     [("カラダキッズてんり", "カラダキッズてんり")], None),
    ("009", "Hinodeシェアホーム加古川",
     [("シェアホーム加古川", "シェアホーム加古川")], None),
    ("010", "相談支援NOAH加古川",
     [("NOAH（加古川）", "NOAH（加古川）")], None),
    # === NPO法人EMIFULL (旧: NPO法人のじぎく高砂、2026/1改称) ===
    ("011", "のじぎく高砂",
     [("のじぎく高砂", "のじぎく高砂")], "NPO"),
    ("012", "のじぎく稲美",
     [("のじぎく稲美", "のじぎく稲美"),
      ("のじぎく加古川", "のじぎく加古川"),
      ("こすもす稲美", "こすもす稲美"),
      ("だがし屋キューブ", "だがし屋キューブ"),
      ("キッチン", "大西キッチン")], "NPO"),
]

# 科目マスタ（カテゴリ・小計フラグ付き）
# category: revenue / revenue_total / cogs / cogs_total / gross_profit /
#           sga / sga_total / op_profit /
#           non_op_rev_total / non_op_rev / non_op_exp_total / non_op_exp /
#           ordinary_profit / special_gain / special_loss /
#           pretax_income / tax / net_income
PL_ACCOUNTS_SEED = [
    # (name, category, is_total, display_order)
    # NPO のじぎく系で出てくる科目を冒頭に追加
    ('生産活動収益', 'revenue', 0, 3),
    ('社保外来収益', 'revenue', 0, 4),
    ('国保外来収益', 'revenue', 0, 5),
    ('利用者負担金収益', 'revenue', 0, 6),
    ('入院窓口収益', 'revenue', 0, 7),
    ('外来窓口収益', 'revenue', 0, 8),
    ('介護保険収益', 'revenue', 0, 9),
    ('自由診療収益', 'revenue', 0, 10),
    ('保健予防活動収益', 'revenue', 0, 11),
    ('その他の医業収益', 'revenue', 0, 12),
    ('自立支援収益', 'revenue', 0, 13),
    ('自立支援負担金', 'revenue', 0, 14),
    ('家賃収益', 'revenue', 0, 15),
    ('売上高 計', 'revenue_total', 1, 16),
    ('期首商品棚卸', 'cogs', 0, 17),
    ('当期商品仕入', 'cogs', 0, 18),
    ('医薬品費', 'cogs', 0, 19),
    ('診療材料費', 'cogs', 0, 20),
    ('医療消耗器具備品費', 'cogs', 0, 21),
    ('検査委託費', 'cogs', 0, 22),
    ('他勘定振替高(商)', 'cogs', 0, 23),
    ('期末商品棚卸', 'cogs', 0, 24),
    ('期末商品棚卸高', 'cogs', 0, 25),
    ('商品売上原価', 'cogs_total', 1, 26),
    ('売上総損益金額', 'gross_profit', 1, 27),
    ('役員報酬', 'sga', 0, 28),
    ('本部負担金', 'sga', 0, 29),
    ('医師給', 'sga', 0, 30),
    ('看護師給', 'sga', 0, 31),
    ('事務員給', 'sga', 0, 32),
    ('本部事務員給', 'sga', 0, 33),
    ('広報職員給', 'sga', 0, 34),
    ('管理者給', 'sga', 0, 35),
    ('指導員給', 'sga', 0, 36),
    ('療法士給', 'sga', 0, 37),
    ('介護士給', 'sga', 0, 38),
    ('ケアマネ給', 'sga', 0, 39),
    ('その他給', 'sga', 0, 40),
    ('賞与', 'sga', 0, 41),
    ('出向負担金', 'sga', 0, 42),
    ('受入出向負担金', 'sga', 0, 43),
    ('法定福利費', 'sga', 0, 44),
    ('退職給付費用', 'sga', 0, 45),
    ('福利厚生費', 'sga', 0, 46),
    ('給食委託費', 'sga', 0, 47),
    ('清掃委託費', 'sga', 0, 48),
    ('その他の委託費', 'sga', 0, 49),
    ('支払報酬料', 'sga', 0, 50),
    ('業務委託費', 'sga', 0, 51),
    ('保守点検費', 'sga', 0, 52),
    ('旅費交通費', 'sga', 0, 53),
    ('燃料費', 'sga', 0, 54),
    ('車両費', 'sga', 0, 55),
    ('通信費', 'sga', 0, 56),
    ('広告宣伝費', 'sga', 0, 57),
    ('研修費', 'sga', 0, 58),
    ('採用教育費', 'sga', 0, 59),
    ('交際費', 'sga', 0, 60),
    ('会議費', 'sga', 0, 61),
    ('消耗品費', 'sga', 0, 62),
    ('食材費', 'sga', 0, 63),
    ('修繕費', 'sga', 0, 64),
    ('水道光熱費', 'sga', 0, 65),
    ('新聞図書費', 'sga', 0, 66),
    ('諸会費', 'sga', 0, 67),
    ('支払手数料', 'sga', 0, 68),
    ('賃借料', 'sga', 0, 69),
    ('リース料', 'sga', 0, 70),
    ('保険料', 'sga', 0, 71),
    ('動産保険料', 'sga', 0, 72),
    ('租税公課', 'sga', 0, 73),
    ('消費税等', 'sga', 0, 74),
    ('減価償却費', 'sga', 0, 75),
    ('負ののれん償却益', 'sga', 0, 76),
    ('寄付金', 'sga', 0, 77),
    ('雑費', 'sga', 0, 78),
    ('慰労引当金繰入', 'sga', 0, 79),
    ('ただおか共通経費按分', 'sga', 0, 80),
    ('販売管理費 計', 'sga_total', 1, 81),
    ('営業損益金額', 'op_profit', 1, 82),
    ('営業外収益', 'non_op_rev_total', 1, 83),
    ('受取利息', 'non_op_rev', 0, 84),
    ('受取配当金', 'non_op_rev', 0, 85),
    ('雑収入', 'non_op_rev', 0, 86),
    ('営業外費用', 'non_op_exp_total', 1, 87),
    ('支払利息', 'non_op_exp', 0, 88),
    ('雑損失', 'non_op_exp', 0, 89),
    ('経常損益金額', 'ordinary_profit', 1, 90),
    ('特別利益', 'special_gain', 0, 91),
    ('特別損失', 'special_loss', 0, 92),
    ('税引前当期純損益金額', 'pretax_income', 1, 93),
    ('法人税等', 'tax', 0, 94),
    ('法人税等調整額', 'tax', 0, 95),
    ('当期純損益金額', 'net_income', 1, 96),
    # === NPO のじぎく系で出てくる販管費 (既存科目に被らないものだけ追加) ===
    ('A型利用者給', 'sga', 0, 100),
    ('B型利用者工賃', 'sga', 0, 101),
    ('賞与引当金繰入', 'sga', 0, 102),
    ('給食費', 'sga', 0, 103),
    ('印刷製本費', 'sga', 0, 104),
]

# カテゴリの表示用ラベル
PL_CATEGORY_LABELS = {
    'revenue': '売上',
    'revenue_total': '売上高計',
    'cogs': '売上原価',
    'cogs_total': '商品売上原価',
    'gross_profit': '売上総損益',
    'sga': '販管費',
    'sga_total': '販管費計',
    'op_profit': '営業損益',
    'non_op_rev_total': '営業外収益計',
    'non_op_rev': '営業外収益',
    'non_op_exp_total': '営業外費用計',
    'non_op_exp': '営業外費用',
    'ordinary_profit': '経常損益',
    'special_gain': '特別利益',
    'special_loss': '特別損失',
    'pretax_income': '税引前損益',
    'tax': '法人税等',
    'net_income': '当期純損益',
}


def init_journal_schema():
    """仕訳帳テーブルを作成。"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS journal_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_date DATE NOT NULL,
            -- 借方
            debit_account TEXT,
            debit_amount INTEGER NOT NULL DEFAULT 0,
            debit_department TEXT,
            debit_dept_clean TEXT,
            debit_subunit_id INTEGER REFERENCES pl_subunits(id),
            debit_vendor TEXT,
            debit_memo TEXT,
            debit_item TEXT,
            -- 貸方
            credit_account TEXT,
            credit_amount INTEGER NOT NULL DEFAULT 0,
            credit_department TEXT,
            credit_dept_clean TEXT,
            credit_subunit_id INTEGER REFERENCES pl_subunits(id),
            credit_vendor TEXT,
            credit_memo TEXT,
            credit_item TEXT,
            -- メタ
            journal_id TEXT,
            journal_no TEXT,
            record_no TEXT,
            transaction_content TEXT,
            file_hash TEXT,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(journal_id, record_no, debit_account, credit_account, transaction_date)
        );

        CREATE INDEX IF NOT EXISTS idx_journal_date ON journal_entries(transaction_date);
        CREATE INDEX IF NOT EXISTS idx_journal_debit_acc ON journal_entries(debit_account);
        CREATE INDEX IF NOT EXISTS idx_journal_credit_acc ON journal_entries(credit_account);
        CREATE INDEX IF NOT EXISTS idx_journal_debit_sub ON journal_entries(debit_subunit_id);
        CREATE INDEX IF NOT EXISTS idx_journal_dept_clean ON journal_entries(debit_dept_clean);

        CREATE TABLE IF NOT EXISTS journal_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_filename TEXT,
            row_count INTEGER,
            inserted_count INTEGER,
            skipped_count INTEGER,
            file_hash TEXT,
            date_range TEXT,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # マイグレーション: 既存DBに 品目 列を追加
        cols = {r['name'] for r in conn.execute("PRAGMA table_info(journal_entries)").fetchall()}
        if 'debit_item' not in cols:
            conn.execute("ALTER TABLE journal_entries ADD COLUMN debit_item TEXT")
        if 'credit_item' not in cols:
            conn.execute("ALTER TABLE journal_entries ADD COLUMN credit_item TEXT")


def insert_journal_entries(rows: list[dict], filename: str, file_hash: str) -> dict:
    """仕訳行リストを一括 UPSERT (UNIQUE違反は無視) し、件数を返す。"""
    if not rows:
        return {'inserted': 0, 'skipped': 0}
    with get_conn() as conn:
        inserted = 0
        skipped = 0
        for r in rows:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO journal_entries
                    (transaction_date,
                     debit_account, debit_amount, debit_department,
                     debit_dept_clean, debit_subunit_id, debit_vendor, debit_memo, debit_item,
                     credit_account, credit_amount, credit_department,
                     credit_dept_clean, credit_subunit_id, credit_vendor, credit_memo, credit_item,
                     journal_id, journal_no, record_no, transaction_content, file_hash)
                    VALUES (:transaction_date,
                            :debit_account, :debit_amount, :debit_department,
                            :debit_dept_clean, :debit_subunit_id, :debit_vendor, :debit_memo, :debit_item,
                            :credit_account, :credit_amount, :credit_department,
                            :credit_dept_clean, :credit_subunit_id, :credit_vendor, :credit_memo, :credit_item,
                            :journal_id, :journal_no, :record_no, :transaction_content, :file_hash)
                """, {**r, 'file_hash': file_hash})
                if conn.total_changes:
                    pass
                inserted += 1
            except Exception:
                skipped += 1

        # (詳細件数は INSERT OR IGNORE では取れないので簡易的に rowcount を加算)

        # 取込履歴
        from_date = min((r['transaction_date'] for r in rows), default='')
        to_date = max((r['transaction_date'] for r in rows), default='')
        conn.execute("""
            INSERT INTO journal_imports
            (source_filename, row_count, inserted_count, skipped_count, file_hash, date_range)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (filename, len(rows), inserted, skipped, file_hash, f"{from_date}〜{to_date}"))
        return {'inserted': inserted, 'skipped': skipped, 'date_range': f"{from_date}〜{to_date}"}


def list_journal_imports(limit=10):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM journal_imports ORDER BY imported_at DESC LIMIT ?", (limit,)
        ).fetchall()]


def search_journal_for_account(account_name: str, year_month: str,
                               subunit_ids: list[int] | None = None,
                               side: str = 'debit',
                               limit: int = 20):
    """指定 (科目, 月, 部門) の仕訳明細を金額大きい順に返す。
    side: 'debit' or 'credit'  販管費の費用は通常 debit 側。"""
    where = ["1=1"]
    params: list = []
    if side == 'debit':
        where.append("debit_account = ?")
        params.append(account_name)
        amount_col = 'debit_amount'
        sub_col = 'debit_subunit_id'
        vendor_col = 'debit_vendor'
        memo_col = 'debit_memo'
    else:
        where.append("credit_account = ?")
        params.append(account_name)
        amount_col = 'credit_amount'
        sub_col = 'credit_subunit_id'
        vendor_col = 'credit_vendor'
        memo_col = 'credit_memo'

    where.append("strftime('%Y-%m', transaction_date) = ?")
    params.append(year_month)

    if subunit_ids:
        placeholders = ','.join('?' * len(subunit_ids))
        where.append(f"{sub_col} IN ({placeholders})")
        params.extend(subunit_ids)

    sql = f"""
        SELECT transaction_date, {amount_col} AS amount,
               {vendor_col} AS vendor, {memo_col} AS memo,
               transaction_content
        FROM journal_entries
        WHERE {' AND '.join(where)}
        ORDER BY {amount_col} DESC
        LIMIT ?
    """
    params.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _normalize_journal_label(label: str) -> str:
    """類似度比較用にラベルを正規化。月・日付・数字・空白を除去。
    例: 'Bizimo光 2月分' / 'Bizimo光 3月分' → ともに 'Bizimo光'"""
    import re
    if not label:
        return ''
    s = label
    # 「Y年M月D日」「2024/1/1」「12-25」などの日付
    s = re.sub(r'\d{2,4}[年/\-]\d{1,2}[月/\-]\d{1,2}日?', '', s)
    s = re.sub(r'\d{1,2}[月/\-]\d{1,2}日?', '', s)
    s = re.sub(r'\d{1,2}日', '', s)
    # 「N月分」「N月度」「N月」
    s = re.sub(r'\d+月(分|度)?', '', s)
    # 残った数字
    s = re.sub(r'\d+(\.\d+)?', '', s)
    # 各種空白(全角含む)
    s = re.sub(r'[\s　]+', '', s)
    return s.strip()


def _cluster_journal_items(items: list[dict], threshold: float = 0.3) -> list[dict]:
    """類似ラベル同士を貪欲法でクラスタリングし、合算した dict を返す。
    items: [{'label', 'vendor', 'detail', 'curr_amt', 'prev_amt', 'curr_cnt', 'prev_cnt'}, ...]
    threshold: SequenceMatcher.ratio() の閾値(0.0〜1.0)。"""
    from difflib import SequenceMatcher

    clusters = []  # [{'norm': str, 'members': [...], 'sums': dict}]
    for r in items:
        norm = _normalize_journal_label(r['label'])
        if not norm:
            norm = r['label']  # 全部記号/数字だった場合のフォールバック

        merged = False
        if norm:
            for c in clusters:
                # 完全一致 もしくは 類似度 >= threshold
                if norm == c['norm']:
                    ratio = 1.0
                else:
                    ratio = SequenceMatcher(None, norm, c['norm']).ratio()
                if ratio >= threshold:
                    c['members'].append(r)
                    c['sums']['curr_amt'] += r.get('curr_amt', 0) or 0
                    c['sums']['prev_amt'] += r.get('prev_amt', 0) or 0
                    c['sums']['curr_cnt'] += r.get('curr_cnt', 0) or 0
                    c['sums']['prev_cnt'] += r.get('prev_cnt', 0) or 0
                    # 既存norm が長い方を残す(より具体的な name を保持)
                    if len(norm) > len(c['norm']):
                        c['norm'] = norm
                    merged = True
                    break

        if not merged:
            clusters.append({
                'norm': norm,
                'members': [r],
                'sums': {
                    'curr_amt': r.get('curr_amt', 0) or 0,
                    'prev_amt': r.get('prev_amt', 0) or 0,
                    'curr_cnt': r.get('curr_cnt', 0) or 0,
                    'prev_cnt': r.get('prev_cnt', 0) or 0,
                },
            })

    out = []
    for c in clusters:
        # 代表ラベル: 最も長いメンバーのラベル
        rep = max(c['members'], key=lambda m: len(m.get('label', '')))['label']
        out.append({
            'label': rep,
            'vendor': c['members'][0].get('vendor', ''),
            'detail': c['members'][0].get('detail', ''),
            'curr_amt': c['sums']['curr_amt'],
            'prev_amt': c['sums']['prev_amt'],
            'curr_cnt': c['sums']['curr_cnt'],
            'prev_cnt': c['sums']['prev_cnt'],
            'diff': c['sums']['curr_amt'] - c['sums']['prev_amt'],
            'merged_count': len(c['members']),
        })
    return out


def journal_item_diffs_for_account(account_name: str,
                                    curr_yms: list[str], prev_yms: list[str],
                                    subunit_ids: list[int] | None = None,
                                    side: str = 'debit',
                                    top_n: int = 5,
                                    similarity_threshold: float = 0.3):
    """指定 (科目, 部門) の仕訳を 取引先＋品目(or 備考or取引内容) 単位で集計し、
    当期/前期 の差額が大きい順 上位N件を返す。

    取引先 OR 品目 OR 備考 OR 取引内容 のいずれかに情報がある行のみが対象。
    全て空(社内振替など)は除外。

    Returns: [{'vendor', 'detail', 'label', 'curr', 'prev', 'diff', 'curr_cnt', 'prev_cnt'}, ...]
    """
    if side == 'debit':
        amount_col = 'debit_amount'
        sub_col = 'debit_subunit_id'
        vendor_col = 'debit_vendor'
        memo_col = 'debit_memo'
        item_col = 'debit_item'
        acc_col = 'debit_account'
    else:
        amount_col = 'credit_amount'
        sub_col = 'credit_subunit_id'
        vendor_col = 'credit_vendor'
        memo_col = 'credit_memo'
        item_col = 'credit_item'
        acc_col = 'credit_account'

    all_yms = list(set(curr_yms or []) | set(prev_yms or []))
    if not all_yms:
        return []

    where = [f"{acc_col} = ?"]
    params: list = [account_name]
    yms_ph = ','.join('?' * len(all_yms))
    where.append(f"strftime('%Y-%m', transaction_date) IN ({yms_ph})")
    params.extend(all_yms)

    if subunit_ids:
        sub_ph = ','.join('?' * len(subunit_ids))
        where.append(f"{sub_col} IN ({sub_ph})")
        params.extend(subunit_ids)

    # 取引先・品目・備考・取引内容 のいずれかに値があれば対象
    where.append(f"""(
        NULLIF(TRIM({vendor_col}), '') IS NOT NULL
        OR NULLIF(TRIM({item_col}), '') IS NOT NULL
        OR NULLIF(TRIM({memo_col}), '') IS NOT NULL
        OR NULLIF(TRIM(transaction_content), '') IS NOT NULL
    )""")

    curr_yms_safe = curr_yms or ['__none__']
    prev_yms_safe = prev_yms or ['__none__']
    curr_in = ','.join('?' * len(curr_yms_safe))
    prev_in = ','.join('?' * len(prev_yms_safe))

    # クラスタリング前に多めに取得 (top_n*6)
    fetch_limit = max(top_n * 6, 30)
    sql = f"""
    WITH labeled AS (
      SELECT
        COALESCE(NULLIF(TRIM({vendor_col}), ''), '') AS vendor,
        COALESCE(
          NULLIF(TRIM({item_col}), ''),
          NULLIF(TRIM({memo_col}), ''),
          NULLIF(TRIM(transaction_content), ''),
          ''
        ) AS detail,
        {amount_col} AS amt,
        strftime('%Y-%m', transaction_date) AS ym
      FROM journal_entries
      WHERE {' AND '.join(where)}
    )
    SELECT
      vendor, detail,
      SUM(CASE WHEN ym IN ({curr_in}) THEN amt ELSE 0 END) AS curr_amt,
      SUM(CASE WHEN ym IN ({prev_in}) THEN amt ELSE 0 END) AS prev_amt,
      SUM(CASE WHEN ym IN ({curr_in}) THEN 1 ELSE 0 END) AS curr_cnt,
      SUM(CASE WHEN ym IN ({prev_in}) THEN 1 ELSE 0 END) AS prev_cnt
    FROM labeled
    WHERE (vendor != '' OR detail != '')
    GROUP BY vendor, detail
    HAVING (curr_amt + prev_amt) > 0
    ORDER BY (curr_amt + prev_amt) DESC
    LIMIT ?
    """

    full_params = list(params) + list(curr_yms_safe) + list(prev_yms_safe) \
                  + list(curr_yms_safe) + list(prev_yms_safe) + [fetch_limit]

    with get_conn() as conn:
        raw_items = []
        for r in conn.execute(sql, full_params).fetchall():
            d = dict(r)
            v = (d['vendor'] or '').strip()
            det = (d['detail'] or '').strip()
            if v and det:
                label = v if v == det else f"{v}／{det}"
            else:
                label = v or det or '(不明)'
            d['label'] = label
            d['diff'] = (d['curr_amt'] or 0) - (d['prev_amt'] or 0)
            raw_items.append(d)

    # 類似ラベルをクラスタリングして合算
    clustered = _cluster_journal_items(raw_items, threshold=similarity_threshold)
    # 差額の絶対値が大きい順
    clustered.sort(key=lambda x: -abs(x['diff']))
    return clustered[:top_n]


def journal_summary_for_period(account_name: str, year_months: list[str],
                                subunit_ids: list[int] | None = None,
                                side: str = 'debit',
                                top_n: int = 5):
    """指定期間(複数月)の仕訳を取引先別に集計。
    side: 'debit'=借方, 'credit'=貸方。販管費の費用は通常 debit 側。"""
    if not year_months:
        return []
    if side == 'debit':
        amount_col = 'debit_amount'
        sub_col = 'debit_subunit_id'
        vendor_col = 'debit_vendor'
        item_col = 'debit_item'
        memo_col = 'debit_memo'
        acc_col = 'debit_account'
    else:
        amount_col = 'credit_amount'
        sub_col = 'credit_subunit_id'
        vendor_col = 'credit_vendor'
        item_col = 'credit_item'
        memo_col = 'credit_memo'
        acc_col = 'credit_account'

    where = [f"{acc_col} = ?"]
    params: list = [account_name]
    placeholders = ','.join('?' * len(year_months))
    where.append(f"strftime('%Y-%m', transaction_date) IN ({placeholders})")
    params.extend(year_months)
    if subunit_ids:
        sub_placeholders = ','.join('?' * len(subunit_ids))
        where.append(f"{sub_col} IN ({sub_placeholders})")
        params.extend(subunit_ids)

    sql = f"""
        SELECT COALESCE(NULLIF(TRIM({vendor_col}), ''), '') AS vendor,
               COALESCE(
                   NULLIF(TRIM({item_col}), ''),
                   NULLIF(TRIM({memo_col}), ''),
                   NULLIF(TRIM(transaction_content), ''),
                   ''
               ) AS detail,
               SUM({amount_col}) AS total_amount,
               COUNT(*) AS cnt,
               GROUP_CONCAT(DISTINCT NULLIF(TRIM({memo_col}), '')) AS memos
        FROM journal_entries
        WHERE {' AND '.join(where)}
        GROUP BY vendor, detail
        ORDER BY total_amount DESC
        LIMIT ?
    """
    params.append(top_n)
    with get_conn() as conn:
        rows = []
        for r in conn.execute(sql, params).fetchall():
            d = dict(r)
            vendor = (d.get('vendor') or '').strip()
            detail = (d.get('detail') or '').strip()
            if vendor and detail:
                d['label'] = vendor if vendor == detail else f"{vendor}／{detail}"
            else:
                d['label'] = vendor or detail or '(取引先なし)'
            rows.append(d)
        return rows


def journal_summary_for_account(account_name: str, year_month: str,
                                subunit_ids: list[int] | None = None,
                                side: str = 'debit',
                                top_n: int = 5):
    """指定 (科目, 月, 部門) の仕訳明細を 取引先別に集計し、上位 top_n を返す。"""
    if side == 'debit':
        amount_col = 'debit_amount'
        sub_col = 'debit_subunit_id'
        vendor_col = 'debit_vendor'
        acc_col = 'debit_account'
    else:
        amount_col = 'credit_amount'
        sub_col = 'credit_subunit_id'
        vendor_col = 'credit_vendor'
        acc_col = 'credit_account'

    where = [f"{acc_col} = ?", "strftime('%Y-%m', transaction_date) = ?"]
    params: list = [account_name, year_month]
    if subunit_ids:
        placeholders = ','.join('?' * len(subunit_ids))
        where.append(f"{sub_col} IN ({placeholders})")
        params.extend(subunit_ids)

    sql = f"""
        SELECT COALESCE({vendor_col}, '(取引先なし)') AS vendor,
               SUM({amount_col}) AS total_amount,
               COUNT(*) AS cnt
        FROM journal_entries
        WHERE {' AND '.join(where)}
        GROUP BY vendor
        ORDER BY total_amount DESC
        LIMIT ?
    """
    params.append(top_n)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def init_pl_schema():
    """損益関連テーブルを作成し、グループ・サブ部門・科目マスタを seed する。
    init_db() 末尾から呼び出す想定。"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS pl_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            note TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            display_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pl_subunits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL REFERENCES pl_groups(id) ON DELETE CASCADE,
            excel_name TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            display_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pl_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            category TEXT NOT NULL,
            is_total INTEGER NOT NULL DEFAULT 0,
            display_order INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS pl_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subunit_id INTEGER NOT NULL REFERENCES pl_subunits(id) ON DELETE CASCADE,
            account_id INTEGER NOT NULL REFERENCES pl_accounts(id) ON DELETE CASCADE,
            year_month TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (subunit_id, account_id, year_month)
        );
        CREATE INDEX IF NOT EXISTS idx_pl_entries_ym ON pl_entries(year_month);
        CREATE INDEX IF NOT EXISTS idx_pl_entries_subunit ON pl_entries(subunit_id);
        CREATE INDEX IF NOT EXISTS idx_pl_entries_account ON pl_entries(account_id);

        CREATE TABLE IF NOT EXISTS pl_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_filename TEXT,
            fiscal_label TEXT,
            fiscal_start_ym TEXT,
            sheet_count INTEGER,
            entry_count INTEGER,
            year_months TEXT,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # === マイグレーション: 旧コード G01〜G10 → 001〜010 ===
        old_codes = {r['code'] for r in conn.execute(
            "SELECT code FROM pl_groups WHERE code LIKE 'G%'"
        ).fetchall()}
        for old in old_codes:
            new = old.replace('G', '').zfill(3)  # G01 -> 001
            existing = conn.execute(
                "SELECT 1 FROM pl_groups WHERE code = ?", (new,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "UPDATE pl_groups SET code = ? WHERE code = ?",
                    (new, old),
                )

        # === グループ & サブ部門 seed (UPSERT) ===
        for order_idx, (code, name, subunits, note) in enumerate(PL_GROUPS_SEED):
            conn.execute("""
                INSERT INTO pl_groups (code, name, note, display_order)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    note = excluded.note,
                    display_order = excluded.display_order
            """, (code, name, note, order_idx))
            grp_id = conn.execute(
                "SELECT id FROM pl_groups WHERE code = ?", (code,)
            ).fetchone()['id']
            for sub_idx, (excel_name, display_name) in enumerate(subunits):
                conn.execute("""
                    INSERT INTO pl_subunits (group_id, excel_name, display_name, display_order)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(excel_name) DO UPDATE SET
                        group_id = excluded.group_id,
                        display_name = excluded.display_name,
                        display_order = excluded.display_order
                """, (grp_id, excel_name, display_name, sub_idx))

        # === 科目マスタ seed (UPSERT) ===
        for name, category, is_total, order in PL_ACCOUNTS_SEED:
            conn.execute("""
                INSERT INTO pl_accounts (name, category, is_total, display_order)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    category = excluded.category,
                    is_total = excluded.is_total,
                    display_order = excluded.display_order
            """, (name, category, is_total, order))


# === 損益マスタ参照 ===

def list_pl_groups(active_only=True):
    sql = "SELECT * FROM pl_groups"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY display_order, code"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def list_pl_subunits(group_id=None):
    sql = """
        SELECT s.*, g.code AS group_code, g.name AS group_name, g.is_active AS group_active
        FROM pl_subunits s
        JOIN pl_groups g ON g.id = s.group_id
    """
    params = []
    if group_id is not None:
        sql += " WHERE s.group_id = ?"
        params.append(group_id)
    sql += " ORDER BY g.display_order, s.display_order"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_pl_accounts(category=None, include_total=True):
    sql = "SELECT * FROM pl_accounts WHERE 1=1"
    params = []
    if category is not None:
        sql += " AND category = ?"
        params.append(category)
    if not include_total:
        sql += " AND is_total = 0"
    sql += " ORDER BY display_order"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_pl_subunit_by_excel_name(excel_name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pl_subunits WHERE excel_name = ?", (excel_name,)
        ).fetchone()
        return dict(row) if row else None


def get_pl_account_by_name(name):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pl_accounts WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None


def list_pl_year_months():
    """損益データが存在する年月一覧（降順）"""
    with get_conn() as conn:
        return [r['year_month'] for r in conn.execute(
            "SELECT DISTINCT year_month FROM pl_entries ORDER BY year_month DESC"
        ).fetchall()]


# === 損益用の会計年度ユーティリティ ===
# 法人決算(6月始まり)と現場評価(4月始まり)の両方に対応する。
# 各関数は start_month を引数に取る。

PL_FISCAL_START_MONTH_CORP = 6   # 法人決算（第N期）
PL_FISCAL_START_MONTH_OPS = 4    # 現場評価（年度）


def pl_fiscal_year_of(yyyymm, start_month=PL_FISCAL_START_MONTH_CORP):
    """'2025-03' -> 2024 (start_month=6, つまり 第5期：2024/6-2025/5)
    '2025-03' -> 2024 (start_month=4, つまり 2024年度：2024/4-2025/3)"""
    y, m = int(yyyymm[:4]), int(yyyymm[5:7])
    return y if m >= start_month else y - 1


def pl_fiscal_year_months(fy, start_month=PL_FISCAL_START_MONTH_CORP):
    """fy=2024, start=6 -> ['2024-06',...,'2025-05']
       fy=2024, start=4 -> ['2024-04',...,'2025-03']"""
    months = []
    y, m = fy, start_month
    for _ in range(12):
        months.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


def pl_fiscal_year_range(fy, start_month=PL_FISCAL_START_MONTH_CORP):
    months = pl_fiscal_year_months(fy, start_month)
    return months[0], months[-1]


def list_pl_fiscal_years(start_month=PL_FISCAL_START_MONTH_CORP):
    """データが存在する期(fy)一覧（降順）"""
    yms = list_pl_year_months()
    return sorted({pl_fiscal_year_of(ym, start_month) for ym in yms}, reverse=True)


def pl_fiscal_year_label(fy, start_month=PL_FISCAL_START_MONTH_CORP,
                        period_number_base=2021):
    """ラベル文字列。
    - 6月始まり: '第N期 (YYYY/6〜YYYY+1/5)'
    - 4月始まり: 'YYYY年度 (YYYY/4〜YYYY+1/3)'
    - その他:    'FYYYYY (YYYY/M〜YYYY+1/M-1)'
    period_number_base: 第1期の開始年（既定 2021 = 2021/6 開始 = 第1期）。
    つまり 2024/6 開始 = 第4期、2025/6 開始 = 第5期。"""
    end_month = start_month - 1 if start_month > 1 else 12
    if start_month == 6:
        n = fy - period_number_base + 1
        return f"第{n}期 ({fy}/{start_month}〜{fy+1}/{end_month})"
    if start_month == 4:
        return f"{fy}年度 ({fy}/{start_month}〜{fy+1}/{end_month})"
    return f"FY{fy} ({fy}/{start_month}〜{fy+1}/{end_month})"


def pl_period_to_year_months(fy, end_ym=None,
                              start_month=PL_FISCAL_START_MONTH_CORP):
    """期(fy)の始まり〜end_ym までの年月リスト。end_ym 省略時は期全体。"""
    months = pl_fiscal_year_months(fy, start_month)
    if end_ym is None:
        return months
    return [m for m in months if m <= end_ym]


# === 取込（書き込み） ===

def replace_pl_entries(year_month, subunit_id, entries):
    """指定 (year_month, subunit_id) の既存 entries を全削除し、新しい値で置換。
    entries: [(account_id, amount), ...]
    None や 0 も含めて投入する（ただし None は 0 として扱う）。"""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM pl_entries WHERE year_month = ? AND subunit_id = ?",
            (year_month, subunit_id)
        )
        rows = [(subunit_id, acc_id, year_month, int(amount or 0))
                for acc_id, amount in entries]
        conn.executemany("""
            INSERT INTO pl_entries (subunit_id, account_id, year_month, amount)
            VALUES (?, ?, ?, ?)
        """, rows)
        return len(rows)


def record_pl_import(filename, fiscal_label, fiscal_start_ym,
                     sheet_count, entry_count, year_months_list):
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO pl_imports
            (source_filename, fiscal_label, fiscal_start_ym,
             sheet_count, entry_count, year_months)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (filename, fiscal_label, fiscal_start_ym,
              sheet_count, entry_count, ','.join(year_months_list)))
        return cur.lastrowid


def list_pl_imports(limit=20):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pl_imports ORDER BY imported_at DESC LIMIT ?", (limit,)
        ).fetchall()]


def delete_pl_entries(year_month=None, group_id=None):
    """指定条件の損益エントリを削除。"""
    sql = "DELETE FROM pl_entries WHERE 1=1"
    params = []
    if year_month is not None:
        sql += " AND year_month = ?"
        params.append(year_month)
    if group_id is not None:
        sql += " AND subunit_id IN (SELECT id FROM pl_subunits WHERE group_id = ?)"
        params.append(group_id)
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount


def count_pl_entries(year_month=None, group_id=None):
    sql = "SELECT COUNT(*) AS c FROM pl_entries WHERE 1=1"
    params = []
    if year_month is not None:
        sql += " AND year_month = ?"
        params.append(year_month)
    if group_id is not None:
        sql += " AND subunit_id IN (SELECT id FROM pl_subunits WHERE group_id = ?)"
        params.append(group_id)
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()['c']


# === 損益データ取得（集計用） ===

def fetch_pl_entries(year_months=None, group_ids=None,
                     subunit_ids=None, categories=None, account_ids=None):
    """フィルタ条件付きで損益データを取得。
    戻り値: [{year_month, group_id, group_code, group_name, subunit_id,
              subunit_name, account_id, account_name, category, is_total, amount}, ...]"""
    sql = """
        SELECT e.year_month,
               g.id AS group_id, g.code AS group_code, g.name AS group_name,
               g.display_order AS group_order,
               s.id AS subunit_id, s.display_name AS subunit_name,
               s.display_order AS subunit_order,
               a.id AS account_id, a.name AS account_name,
               a.category, a.is_total, a.display_order AS account_order,
               e.amount
        FROM pl_entries e
        JOIN pl_subunits s ON s.id = e.subunit_id
        JOIN pl_groups g ON g.id = s.group_id
        JOIN pl_accounts a ON a.id = e.account_id
        WHERE 1=1
    """
    params = []
    if year_months:
        sql += f" AND e.year_month IN ({','.join('?'*len(year_months))})"
        params.extend(year_months)
    if group_ids:
        sql += f" AND g.id IN ({','.join('?'*len(group_ids))})"
        params.extend(group_ids)
    if subunit_ids:
        sql += f" AND s.id IN ({','.join('?'*len(subunit_ids))})"
        params.extend(subunit_ids)
    if categories:
        sql += f" AND a.category IN ({','.join('?'*len(categories))})"
        params.extend(categories)
    if account_ids:
        sql += f" AND a.id IN ({','.join('?'*len(account_ids))})"
        params.extend(account_ids)
    sql += " ORDER BY e.year_month, g.display_order, s.display_order, a.display_order"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# =====================================================================
# 損益報告書（月次振り返り）
# =====================================================================

def init_profit_reports_schema():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS profit_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fiscal_year_type TEXT,
            target_month TEXT NOT NULL,
            facility_id INTEGER,
            facility_name TEXT NOT NULL,
            reporter_role TEXT NOT NULL,
            reporter_name TEXT NOT NULL,
            issue_review TEXT NOT NULL,
            next_actions TEXT NOT NULL,
            other_notes TEXT,
            ai_summary TEXT,
            created_by_user TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_profit_reports_month
            ON profit_reports(target_month);
        CREATE INDEX IF NOT EXISTS idx_profit_reports_facility_month
            ON profit_reports(target_month, facility_id, facility_name);
        CREATE INDEX IF NOT EXISTS idx_profit_reports_created_by
            ON profit_reports(created_by_user);
        """)


def save_profit_report(fiscal_year_type, target_month, facility_id, facility_name,
                       reporter_role, reporter_name, issue_review, next_actions,
                       other_notes=None, ai_summary=None, created_by_user=None,
                       report_id=None):
    now = datetime.now().isoformat(timespec='seconds')
    with get_conn() as conn:
        if report_id:
            conn.execute("""
                UPDATE profit_reports
                   SET fiscal_year_type = ?,
                       target_month = ?,
                       facility_id = ?,
                       facility_name = ?,
                       reporter_role = ?,
                       reporter_name = ?,
                       issue_review = ?,
                       next_actions = ?,
                       other_notes = ?,
                       ai_summary = ?,
                       updated_at = ?
                 WHERE id = ?
            """, (
                fiscal_year_type, target_month, facility_id, facility_name,
                reporter_role, reporter_name, issue_review, next_actions,
                other_notes, ai_summary, now, report_id,
            ))
            return report_id
        cur = conn.execute("""
            INSERT INTO profit_reports
            (fiscal_year_type, target_month, facility_id, facility_name,
             reporter_role, reporter_name, issue_review, next_actions,
             other_notes, ai_summary, created_by_user, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            fiscal_year_type, target_month, facility_id, facility_name,
            reporter_role, reporter_name, issue_review, next_actions,
            other_notes, ai_summary, created_by_user, now, now,
        ))
        return cur.lastrowid


def update_profit_report_ai_summary(report_id, ai_summary):
    with get_conn() as conn:
        conn.execute("""
            UPDATE profit_reports
               SET ai_summary = ?, updated_at = ?
             WHERE id = ?
        """, (ai_summary, datetime.now().isoformat(timespec='seconds'), report_id))


def delete_profit_report(report_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM profit_reports WHERE id = ?", (report_id,))
        return cur.rowcount


def get_profit_reports(target_month=None, facility_id=None, facility_name=None,
                       created_by_user=None):
    sql = "SELECT * FROM profit_reports WHERE 1=1"
    params = []
    if target_month:
        sql += " AND target_month = ?"
        params.append(target_month)
    if facility_id is not None:
        sql += " AND facility_id = ?"
        params.append(facility_id)
    if facility_name:
        sql += " AND facility_name = ?"
        params.append(facility_name)
    if created_by_user:
        sql += " AND created_by_user = ?"
        params.append(created_by_user)
    sql += " ORDER BY target_month DESC, facility_name, created_at DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_profit_report(report_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM profit_reports WHERE id = ?", (report_id,)
        ).fetchone()
        return dict(row) if row else None


def get_report_status_by_facility(target_month, facilities):
    reports = get_profit_reports(target_month=target_month)
    submitted = defaultdict(list)
    for r in reports:
        key = r.get('facility_id') if r.get('facility_id') is not None else r.get('facility_name')
        submitted[key].append(r)
    rows = []
    for f in facilities:
        key = f.get('id') if f.get('id') is not None else f.get('name')
        rows.append({
            'facility_id': f.get('id'),
            'facility_name': f.get('name'),
            'status': '提出済み' if submitted.get(key) else '未提出',
            'report_count': len(submitted.get(key, [])),
        })
    return rows


# =====================================================================
# 既存集計関数（売上入金管理）
# =====================================================================

def summary_by_method(service_ym=None):
    """回収手段別の集計（自己負担と国保請求を合算）"""
    sql = """
        SELECT method, kbn,
               SUM(charge) AS charge_total,
               SUM(paid) AS paid_total,
               COUNT(*) AS count
        FROM (
            SELECT 'self' AS kbn,
                   COALESCE(self_payment_method, '(未設定)') AS method,
                   COALESCE(self_charge, 0) AS charge,
                   COALESCE(self_paid_amount, 0) AS paid
            FROM monthly_records
            WHERE 1=1 {ym_filter}
            UNION ALL
            SELECT 'kokuho' AS kbn,
                   COALESCE(kokuho_payment_method, '(未設定)') AS method,
                   COALESCE(kokuho_charge, 0) AS charge,
                   COALESCE(kokuho_paid_amount, 0) AS paid
            FROM monthly_records
            WHERE 1=1 {ym_filter}
        )
        GROUP BY method, kbn
        ORDER BY kbn, method
    """
    params = []
    if service_ym:
        ym_filter = "AND service_year_month = ?"
        params = [service_ym, service_ym]
    else:
        ym_filter = ""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql.format(ym_filter=ym_filter), params).fetchall()]


# ============================================================
# 給与台帳 (payroll) スキーマ＆関数
# ============================================================

# 法人プリセット
PAYROLL_CORP_PRESETS = [
    # (code, 表示名, display_order)
    ('EMIFULL_MED', '医療法人社団EMIFULL', 0),
    ('EMIFULL_NPO', 'NPO法人EMIFULL', 1),
    ('HOUSHIKAI',   '医療法人社団奉志会', 2),  # 2023/3以前の旧法人
]

# 給与台帳の年度始まり月（4月）
PAYROLL_FISCAL_START_MONTH = 4


# 都道府県別最低賃金プリセット（時給）
# 適用開始日基準。同都道府県で複数あれば effective_from が新しいものが優先。
MIN_WAGE_PRESETS = [
    # (都道府県, 適用開始年月日 'YYYY-MM-DD', 時給)
    ('兵庫県', '2023-10-01', 1001),
    ('兵庫県', '2024-10-01', 1052),
    ('兵庫県', '2025-10-01', 1116),
    ('奈良県', '2023-10-01', 936),
    ('奈良県', '2024-10-01', 986),
    ('奈良県', '2025-10-01', 1052),
]

# 部署名 → 都道府県マッピング
# 「てんり」「天理」を含めば奈良、それ以外は兵庫がデフォルト
def department_to_prefecture(department: str) -> str:
    """部署名から都道府県を判定。デフォルトは兵庫県。"""
    if not department:
        return '兵庫県'
    s = department
    if 'てんり' in s or '天理' in s or 'TENRI' in s.upper():
        return '奈良県'
    return '兵庫県'


def init_payroll_schema():
    """給与台帳テーブルを作成し、法人マスタを seed する。"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS payroll_corps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            display_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS payroll_employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corp_id INTEGER NOT NULL REFERENCES payroll_corps(id) ON DELETE CASCADE,
            emp_code TEXT NOT NULL,
            name TEXT NOT NULL,
            employment_type TEXT,                 -- '正社員' / 'パート' / '不明' (NULL=未設定)
            department TEXT,                       -- 直近の所属部署
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (corp_id, emp_code)
        );
        CREATE INDEX IF NOT EXISTS idx_payroll_employees_corp ON payroll_employees(corp_id);

        CREATE TABLE IF NOT EXISTS payroll_periods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corp_id INTEGER NOT NULL REFERENCES payroll_corps(id) ON DELETE CASCADE,
            year_month TEXT NOT NULL,             -- 'YYYY-MM'
            pay_type TEXT NOT NULL,               -- '給与' or '賞与'
            bonus_round TEXT,                      -- '夏季' / '冬季' / 'その他' / NULL
            source_filename TEXT,
            source_hash TEXT,
            row_count INTEGER,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (corp_id, year_month, pay_type, bonus_round)
        );
        CREATE INDEX IF NOT EXISTS idx_payroll_periods_ym ON payroll_periods(year_month);
        CREATE INDEX IF NOT EXISTS idx_payroll_periods_corp ON payroll_periods(corp_id);

        CREATE TABLE IF NOT EXISTS payroll_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_id INTEGER NOT NULL REFERENCES payroll_periods(id) ON DELETE CASCADE,
            employee_id INTEGER NOT NULL REFERENCES payroll_employees(id) ON DELETE CASCADE,
            department TEXT,
            base_salary INTEGER,                   -- 本給
            total_payment INTEGER,                 -- 総支給金額
            total_deduction INTEGER,               -- 控除合計額
            net_payment INTEGER,                   -- 差引支給額
            taxable_payment INTEGER,               -- 課税支給額
            items_json TEXT,                       -- 全項目のJSON
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (period_id, employee_id)
        );
        CREATE INDEX IF NOT EXISTS idx_payroll_records_period ON payroll_records(period_id);
        CREATE INDEX IF NOT EXISTS idx_payroll_records_emp ON payroll_records(employee_id);
        """)

        # 法人 seed
        for code, name, order in PAYROLL_CORP_PRESETS:
            conn.execute("""
                INSERT INTO payroll_corps (code, name, display_order)
                VALUES (?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    display_order = excluded.display_order
            """, (code, name, order))

        # === 最低賃金マスタ ===
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS minimum_wages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prefecture TEXT NOT NULL,
            effective_from DATE NOT NULL,           -- 適用開始日 'YYYY-MM-DD'
            hourly_wage INTEGER NOT NULL,           -- 時給(円)
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (prefecture, effective_from)
        );
        CREATE INDEX IF NOT EXISTS idx_min_wages_pref
            ON minimum_wages(prefecture, effective_from);
        """)
        # seed
        for pref, eff, wage in MIN_WAGE_PRESETS:
            conn.execute("""
                INSERT OR IGNORE INTO minimum_wages
                (prefecture, effective_from, hourly_wage)
                VALUES (?, ?, ?)
            """, (pref, eff, wage))


# === 法人マスタ ===

def list_payroll_corps():
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM payroll_corps ORDER BY display_order, code"
        ).fetchall()]


def get_payroll_corp_by_code(code):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM payroll_corps WHERE code = ?", (code,)
        ).fetchone()
        return dict(row) if row else None


# === 職員マスタ ===

def upsert_payroll_employee(corp_id, emp_code, name, department=None,
                             employment_type=None):
    """既存があれば名前/部署を更新、なければ新規。雇用区分は未設定なら推定値で初期化。"""
    now = datetime.now().isoformat(sep=' ', timespec='seconds')
    with get_conn() as conn:
        existing = conn.execute("""
            SELECT id, employment_type FROM payroll_employees
            WHERE corp_id = ? AND emp_code = ?
        """, (corp_id, emp_code)).fetchone()
        if existing:
            # 雇用区分は手動編集を尊重: 既に値があれば上書きしない
            if existing['employment_type']:
                conn.execute("""
                    UPDATE payroll_employees
                    SET name = ?, department = COALESCE(?, department),
                        updated_at = ?
                    WHERE id = ?
                """, (name, department, now, existing['id']))
            else:
                conn.execute("""
                    UPDATE payroll_employees
                    SET name = ?, department = COALESCE(?, department),
                        employment_type = ?, updated_at = ?
                    WHERE id = ?
                """, (name, department, employment_type, now, existing['id']))
            return existing['id']
        cur = conn.execute("""
            INSERT INTO payroll_employees
            (corp_id, emp_code, name, department, employment_type)
            VALUES (?, ?, ?, ?, ?)
        """, (corp_id, emp_code, name, department, employment_type))
        return cur.lastrowid


def list_payroll_employees(corp_id=None):
    sql = "SELECT e.*, c.code AS corp_code, c.name AS corp_name FROM payroll_employees e JOIN payroll_corps c ON c.id = e.corp_id"
    params = []
    if corp_id is not None:
        sql += " WHERE e.corp_id = ?"
        params.append(corp_id)
    sql += " ORDER BY c.display_order, e.emp_code"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def update_payroll_employee(employee_id, employment_type=None, note=None,
                              department=None, name=None):
    sets = []
    params = []
    if employment_type is not None:
        sets.append("employment_type = ?")
        params.append(employment_type)
    if note is not None:
        sets.append("note = ?")
        params.append(note)
    if department is not None:
        sets.append("department = ?")
        params.append(department)
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if not sets:
        return
    sets.append("updated_at = ?")
    params.append(datetime.now().isoformat(sep=' ', timespec='seconds'))
    params.append(employee_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE payroll_employees SET {', '.join(sets)} WHERE id = ?",
            params,
        )


# === 取込（期間/レコード） ===

def upsert_payroll_period(corp_id, year_month, pay_type, bonus_round,
                           source_filename, source_hash, row_count):
    """期間レコードをUPSERTし、id を返す（既存なら子レコードを削除）"""
    with get_conn() as conn:
        existing = conn.execute("""
            SELECT id FROM payroll_periods
            WHERE corp_id = ? AND year_month = ? AND pay_type = ?
              AND COALESCE(bonus_round, '') = COALESCE(?, '')
        """, (corp_id, year_month, pay_type, bonus_round)).fetchone()
        now = datetime.now().isoformat(sep=' ', timespec='seconds')
        if existing:
            conn.execute("""
                UPDATE payroll_periods
                SET source_filename = ?, source_hash = ?, row_count = ?,
                    imported_at = ?
                WHERE id = ?
            """, (source_filename, source_hash, row_count, now, existing['id']))
            # 既存レコードを削除（再取込で置き換え）
            conn.execute(
                "DELETE FROM payroll_records WHERE period_id = ?",
                (existing['id'],)
            )
            return existing['id']
        cur = conn.execute("""
            INSERT INTO payroll_periods
            (corp_id, year_month, pay_type, bonus_round,
             source_filename, source_hash, row_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (corp_id, year_month, pay_type, bonus_round,
              source_filename, source_hash, row_count))
        return cur.lastrowid


def insert_payroll_record(period_id, employee_id, department, items_dict):
    """1職員のレコードをINSERT。
    items_dictはキー名のゆらぎ（『本給/基本給』『総支給金額/総支給額』等）を
    payroll_parser.normalize_items() で正規化してから保存する。"""
    import json
    from lib import payroll_parser as _pp

    items_dict = _pp.normalize_items(items_dict)

    def _pick(*keys):
        for k in keys:
            v = items_dict.get(k)
            if isinstance(v, int):
                return v
        return None

    base_salary = _pick('本給')
    total_payment = _pick('総支給')
    total_deduction = _pick('控除合計')
    net_payment = _pick('差引支給')
    taxable_payment = _pick('課税支給')

    items_json = json.dumps(items_dict, ensure_ascii=False)

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO payroll_records
            (period_id, employee_id, department,
             base_salary, total_payment, total_deduction,
             net_payment, taxable_payment, items_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (period_id, employee_id, department,
              base_salary, total_payment, total_deduction,
              net_payment, taxable_payment, items_json))


# === 参照クエリ ===

def list_payroll_periods(corp_id=None, year_month=None, pay_type=None):
    sql = """
        SELECT p.*, c.code AS corp_code, c.name AS corp_name
        FROM payroll_periods p
        JOIN payroll_corps c ON c.id = p.corp_id
        WHERE 1=1
    """
    params = []
    if corp_id is not None:
        sql += " AND p.corp_id = ?"
        params.append(corp_id)
    if year_month is not None:
        sql += " AND p.year_month = ?"
        params.append(year_month)
    if pay_type is not None:
        sql += " AND p.pay_type = ?"
        params.append(pay_type)
    sql += " ORDER BY p.year_month DESC, c.display_order, p.pay_type, p.bonus_round"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_payroll_year_months(corp_id=None):
    sql = "SELECT DISTINCT year_month FROM payroll_periods"
    params = []
    if corp_id is not None:
        sql += " WHERE corp_id = ?"
        params.append(corp_id)
    sql += " ORDER BY year_month DESC"
    with get_conn() as conn:
        return [r['year_month'] for r in conn.execute(sql, params).fetchall()]


def list_payroll_records(period_id=None, corp_id=None, year_month=None,
                          pay_type=None, employee_id=None):
    """給与レコード一覧（職員名/法人/部署を join）"""
    sql = """
        SELECT r.*, e.emp_code, e.name AS emp_name, e.employment_type,
               p.year_month, p.pay_type, p.bonus_round, p.corp_id,
               c.code AS corp_code, c.name AS corp_name
        FROM payroll_records r
        JOIN payroll_periods p ON p.id = r.period_id
        JOIN payroll_employees e ON e.id = r.employee_id
        JOIN payroll_corps c ON c.id = p.corp_id
        WHERE 1=1
    """
    params = []
    if period_id is not None:
        sql += " AND r.period_id = ?"
        params.append(period_id)
    if corp_id is not None:
        sql += " AND p.corp_id = ?"
        params.append(corp_id)
    if year_month is not None:
        sql += " AND p.year_month = ?"
        params.append(year_month)
    if pay_type is not None:
        sql += " AND p.pay_type = ?"
        params.append(pay_type)
    if employee_id is not None:
        sql += " AND r.employee_id = ?"
        params.append(employee_id)
    sql += " ORDER BY c.display_order, e.emp_code, p.year_month"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def list_payroll_records_in_range(corp_id=None, start_ym=None, end_ym=None,
                                    pay_type=None):
    sql = """
        SELECT r.*, e.emp_code, e.name AS emp_name, e.employment_type,
               p.year_month, p.pay_type, p.bonus_round, p.corp_id,
               c.code AS corp_code, c.name AS corp_name
        FROM payroll_records r
        JOIN payroll_periods p ON p.id = r.period_id
        JOIN payroll_employees e ON e.id = r.employee_id
        JOIN payroll_corps c ON c.id = p.corp_id
        WHERE 1=1
    """
    params = []
    if corp_id is not None:
        sql += " AND p.corp_id = ?"
        params.append(corp_id)
    if start_ym is not None:
        sql += " AND p.year_month >= ?"
        params.append(start_ym)
    if end_ym is not None:
        sql += " AND p.year_month <= ?"
        params.append(end_ym)
    if pay_type is not None:
        sql += " AND p.pay_type = ?"
        params.append(pay_type)
    sql += " ORDER BY c.display_order, e.emp_code, p.year_month"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def delete_payroll_period(period_id):
    """期間（とその子レコード）を削除"""
    with get_conn() as conn:
        conn.execute("DELETE FROM payroll_periods WHERE id = ?", (period_id,))


def delete_all_payroll():
    """全給与データをリセット（テスト/再取込用）"""
    with get_conn() as conn:
        conn.execute("DELETE FROM payroll_records")
        conn.execute("DELETE FROM payroll_periods")
        conn.execute("DELETE FROM payroll_employees")


def payroll_fiscal_year_of(year_month: str) -> int:
    """4月始まり年度: 対象月ベース。'2025-04' → 2025, '2026-03' → 2025"""
    y, m = int(year_month[:4]), int(year_month[5:7])
    return y if m >= PAYROLL_FISCAL_START_MONTH else y - 1


def payroll_fiscal_year_months(fy: int) -> list[str]:
    """fy=2025 → ['2025-04', ..., '2026-03'] (対象月)"""
    months = []
    for i in range(12):
        m = PAYROLL_FISCAL_START_MONTH + i
        if m <= 12:
            months.append(f"{fy}-{m:02d}")
        else:
            months.append(f"{fy + 1}-{m - 12:02d}")
    return months


# === 支給月 ↔ 対象月の変換 ===

def payroll_target_ym(pay_year_month: str, pay_type: str = '給与') -> str | None:
    """支給月から対象月（労働月）を算出。
    給与: 支給月の前月（4月支給 → 3月分労働）
    賞与: 対象月の概念なし。年度判定の都合で支給月そのまま返す。"""
    if not pay_year_month:
        return None
    if pay_type == '賞与':
        return pay_year_month
    y, m = int(pay_year_month[:4]), int(pay_year_month[5:7])
    m -= 1
    if m == 0:
        m, y = 12, y - 1
    return f"{y:04d}-{m:02d}"


def payroll_pay_ym(target_year_month: str) -> str:
    """対象月→支給月（給与のみ。賞与には使わない）"""
    if not target_year_month:
        return None
    y, m = int(target_year_month[:4]), int(target_year_month[5:7])
    m += 1
    if m == 13:
        m, y = 1, y + 1
    return f"{y:04d}-{m:02d}"


def fiscal_year_of_pay(pay_year_month: str, pay_type: str = '給与') -> int:
    """支給月＋区分から年度（対象月ベース）を算出"""
    return payroll_fiscal_year_of(payroll_target_ym(pay_year_month, pay_type))


def list_payroll_fiscal_years(corp_id=None) -> list[int]:
    """対象月ベースの年度一覧"""
    sql = "SELECT DISTINCT year_month, pay_type FROM payroll_periods"
    params = []
    if corp_id is not None:
        sql += " WHERE corp_id = ?"
        params.append(corp_id)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return sorted(
        {fiscal_year_of_pay(r['year_month'], r['pay_type']) for r in rows},
        reverse=True,
    )


def list_payroll_target_year_months(corp_id=None) -> list[str]:
    """対象月（給与のみ）の一覧。賞与は除く。降順。"""
    sql = "SELECT DISTINCT year_month FROM payroll_periods WHERE pay_type = '給与'"
    params = []
    if corp_id is not None:
        sql += " AND corp_id = ?"
        params.append(corp_id)
    with get_conn() as conn:
        pay_yms = [r['year_month'] for r in conn.execute(sql, params).fetchall()]
    target_yms = sorted({payroll_target_ym(ym, '給与') for ym in pay_yms}, reverse=True)
    return target_yms


def list_payroll_records_by_target_ym(corp_id=None, target_ym=None,
                                        pay_type='給与'):
    """対象月での絞り込み"""
    if target_ym is None:
        return list_payroll_records(corp_id=corp_id, pay_type=pay_type)
    if pay_type == '給与':
        pay_ym = payroll_pay_ym(target_ym)
    else:
        pay_ym = target_ym
    return list_payroll_records(
        corp_id=corp_id, year_month=pay_ym, pay_type=pay_type,
    )


# === 最低賃金マスタ ===

# 正社員の標準勤務時間（月）
FULLTIME_STANDARD_HOURS = 176


def list_minimum_wages(prefecture=None):
    sql = "SELECT * FROM minimum_wages"
    params = []
    if prefecture:
        sql += " WHERE prefecture = ?"
        params.append(prefecture)
    sql += " ORDER BY prefecture, effective_from"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def upsert_minimum_wage(prefecture, effective_from, hourly_wage):
    """最低賃金を登録/更新。effective_from='YYYY-MM-DD'"""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO minimum_wages (prefecture, effective_from, hourly_wage)
            VALUES (?, ?, ?)
            ON CONFLICT(prefecture, effective_from) DO UPDATE SET
                hourly_wage = excluded.hourly_wage
        """, (prefecture, effective_from, hourly_wage))


def delete_minimum_wage(min_wage_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM minimum_wages WHERE id = ?", (min_wage_id,))


def get_minimum_wage_for(prefecture: str, target_year_month: str) -> int | None:
    """指定都道府県の指定対象月における最低賃金時給を取得。
    対象月の月初日 ≥ effective_from の中で最新のレコードを返す。"""
    if not prefecture or not target_year_month:
        return None
    target_date = f"{target_year_month}-01"
    with get_conn() as conn:
        row = conn.execute("""
            SELECT hourly_wage FROM minimum_wages
            WHERE prefecture = ? AND effective_from <= ?
            ORDER BY effective_from DESC
            LIMIT 1
        """, (prefecture, target_date)).fetchone()
    return row['hourly_wage'] if row else None


def parse_attendance_hours(value) -> float | None:
    """『160:00』『176:30』のような出勤時間を時間(float)に変換。
    純粋な数値（金額の可能性）は 0〜300 の範囲のみ時間として認識する。"""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # 'HH:MM' 形式
    if ':' in s:
        try:
            h, m = s.split(':')
            return float(h) + float(m) / 60.0
        except (ValueError, TypeError):
            return None
    # 数値そのまま：常識的な月時間範囲のみ時間と判定（金額値の誤読を防止）
    try:
        f = float(s)
        if 0 <= f <= 300:
            return f
        return None  # 300超は金額と判断
    except (ValueError, TypeError):
        return None


def calc_shogu_kaizen_for_record(record_dict: dict) -> dict:
    """1つの給与レコードに対して処遇改善計算を行う。

    入力: list_payroll_records 結果の1要素 (items_jsonを含む)
    出力: {
        '都道府県', '最低賃金時給', '勤務時間（基準）',
        '最低賃金月額', '本給', '差額①',
        '役職手当②', '資格手当③', '処遇改善手当④',
        '処遇改善計上額（①+②+③+④）'
    }
    """
    import json as _json

    department = record_dict.get('department') or ''
    prefecture = department_to_prefecture(department)
    pay_type = record_dict.get('pay_type') or '給与'
    target_ym = (
        payroll_target_ym(record_dict.get('year_month'), pay_type)
        or record_dict.get('year_month')
    )
    hourly = get_minimum_wage_for(prefecture, target_ym)

    items = record_dict.get('items_json')
    if isinstance(items, str):
        try:
            items = _json.loads(items)
        except Exception:
            items = {}
    items = items or {}

    # 雇用区分（職員マスタ側の値を尊重）
    emp_type = record_dict.get('employment_type') or '未設定'

    # 基準時間
    att = items.get('出勤時間')
    actual_h = parse_attendance_hours(att)

    if emp_type == '正社員':
        # 標準は176h。ただし休職・欠勤・早退・短時間勤務などで
        # 実出勤時間が176hを下回る場合は実時間で時給計算する。
        if actual_h is None:
            std_hours = float(FULLTIME_STANDARD_HOURS)
            hours_source = f'正社員 標準{FULLTIME_STANDARD_HOURS}h（出勤時間 取得不可）'
        elif actual_h < FULLTIME_STANDARD_HOURS:
            std_hours = actual_h
            hours_source = (
                f'正社員 実出勤 {att}（{FULLTIME_STANDARD_HOURS}h未満：休職/欠勤/短時間 等）'
            )
        else:
            std_hours = float(FULLTIME_STANDARD_HOURS)
            hours_source = f'正社員 標準{FULLTIME_STANDARD_HOURS}h（実{att}）'
    else:
        # パート/不明/未設定: 実出勤時間で算出
        if actual_h is None:
            std_hours = 0.0
            hours_source = '出勤時間 取得不可'
        else:
            std_hours = actual_h
            hours_source = f'実勤務 {att}'

    min_wage_amount = int(round(hourly * std_hours)) if hourly else 0

    base_salary = record_dict.get('base_salary') or 0
    diff_1 = base_salary - min_wage_amount  # ① 差額（本給 - 最低賃金月額）

    def _get(*keys):
        for k in keys:
            v = items.get(k)
            if isinstance(v, int):
                return v
        return 0

    # normalize_items により SUM/PICKは既に正規化済み
    # ②役職・職位手当: 役職手当+職位手当+役職・職位手当の合算
    # ④処遇改善手当: 処遇改善金+処遇改善手当の合算
    # ⑥インセンティブ: インセンティブ+その他手当の合算
    role_2 = _get('役職・職位手当', '役職手当')                # ②
    qual_3 = _get('資格手当')                                  # ③
    shogu_4 = _get('処遇改善手当', '処遇改善金')               # ④
    biz_5 = _get('業務手当')                                   # ⑤
    incen_6 = _get('インセンティブ')                           # ⑥（その他手当を含む）
    medical_7 = _get('医療費補助', '医療費補助(課税)')         # ⑦

    total = diff_1 + role_2 + qual_3 + shogu_4 + biz_5 + incen_6 + medical_7

    return {
        '都道府県': prefecture,
        '最低賃金時給': hourly,
        '基準時間': std_hours,
        '基準時間根拠': hours_source,
        '最低賃金月額': min_wage_amount,
        '本給': base_salary,
        '差額①(本給-最低賃金)': diff_1,
        '役職・職位手当②': role_2,
        '資格手当③': qual_3,
        '処遇改善④': shogu_4,
        '業務手当⑤': biz_5,
        'インセンティブ⑥': incen_6,
        '医療費補助⑦': medical_7,
        '処遇改善計上額': total,
    }


# === 残業集計 ===

def collect_overtime_for_record(record_dict: dict) -> dict:
    """1レコードの残業情報を取り出す。時間(float)と金額(円)を返す。
    法人による項目名違いを吸収:
      医療法人: 法定内残業時間/普通残業時間/深夜残業時間/休出残業時間（時間）
                 残業手当/定額時間外手当（金額）
      NPO法人:  早出残業（金額）/早朝深夜（時間）/遅早時間（時間）
    """
    import json as _json
    items = record_dict.get('items_json')
    if isinstance(items, str):
        try:
            items = _json.loads(items)
        except Exception:
            items = {}
    items = items or {}

    def _h(*keys):
        """値を時間として読む（HH:MM or 0〜300の数値のみ）"""
        for k in keys:
            v = items.get(k)
            h = parse_attendance_hours(v)
            if h is not None and h > 0:
                return h
        return 0.0

    def _yen(*keys):
        """値を金額として読む。HH:MM文字列は除外。"""
        for k in keys:
            v = items.get(k)
            if isinstance(v, int):
                return v
        return 0

    h_legal = _h('法定内残業時間')
    h_normal = _h('普通残業時間')
    h_night = _h('深夜残業時間')
    h_holiday = _h('休出残業時間')
    # NPO系: 早朝深夜は時間
    h_early_night = _h('早朝深夜')
    h_early = _h('早出残業時間')  # 念のため「早出残業時間」項目も拾う

    h_late_early = _h('遅早時間')

    # 金額（残業手当）
    overtime_pay = _yen('残業手当')
    fixed_ot_pay = _yen('定額時間外手当')

    # NPO系: 「早出残業」は金額として記録される（医療法人には存在しない項目）
    es = items.get('早出残業')
    if isinstance(es, int) and es != 0:
        # 整数なら金額とみなして残業手当に合算
        overtime_pay += es

    late_early_minus = _yen('遅早控除減額', '遅早控除', '欠勤控除減額')

    # 残業時間合計（法定外のみ）
    total_extralegal = h_normal + h_night + h_holiday + h_early + h_early_night
    total_all = total_extralegal + h_legal

    return {
        '法定内残業時間': h_legal,
        '普通残業時間': h_normal,
        '深夜残業時間': h_night,
        '休出残業時間': h_holiday,
        '早出残業時間': h_early + h_early_night,
        '残業時間合計(法定内含む)': total_all,
        '残業時間合計(法定外のみ)': total_extralegal,
        '遅早時間': h_late_early,
        '残業手当': overtime_pay,
        '定額時間外手当': fixed_ot_pay,
        '残業金額合計': overtime_pay + fixed_ot_pay,
        '遅早控除': late_early_minus,
    }


def reclassify_payroll_employees_by_base_salary(corp_id, threshold=200_000):
    """過去の給与レコードから職員ごとの『基本給の最大値』を求め、
    閾値以上を正社員、未満（>0）をパートに一括更新する。
    基本給が一度も計上されていない職員は変更しない。
    返り値: 更新件数 dict {正社員: n1, パート: n2, 据え置き: n3}"""
    sql = """
        SELECT e.id AS emp_id,
               COALESCE(MAX(CASE WHEN p.pay_type='給与' THEN r.base_salary END), 0)
                   AS max_base
        FROM payroll_employees e
        LEFT JOIN payroll_records r ON r.employee_id = e.id
        LEFT JOIN payroll_periods p ON p.id = r.period_id
        WHERE e.corp_id = ?
        GROUP BY e.id
    """
    now = datetime.now().isoformat(sep=' ', timespec='seconds')
    result = {'正社員': 0, 'パート': 0, '据え置き': 0}
    with get_conn() as conn:
        rows = conn.execute(sql, (corp_id,)).fetchall()
        for r in rows:
            mb = r['max_base'] or 0
            if mb <= 0:
                result['据え置き'] += 1
                continue
            new_type = '正社員' if mb >= threshold else 'パート'
            conn.execute("""
                UPDATE payroll_employees
                SET employment_type = ?, updated_at = ?
                WHERE id = ?
            """, (new_type, now, r['emp_id']))
            result[new_type] += 1
    return result


def list_payroll_records_in_target_range(corp_id=None,
                                           start_target_ym=None,
                                           end_target_ym=None,
                                           pay_type=None):
    """対象月の範囲指定。給与は支給月=対象月+1、賞与は対象月=支給月扱い。
    pay_type=None で給与+賞与の両方を含める。"""
    records = []
    if pay_type in (None, '給与'):
        s_pay = payroll_pay_ym(start_target_ym) if start_target_ym else None
        e_pay = payroll_pay_ym(end_target_ym) if end_target_ym else None
        records.extend(list_payroll_records_in_range(
            corp_id=corp_id, start_ym=s_pay, end_ym=e_pay, pay_type='給与',
        ))
    if pay_type in (None, '賞与'):
        records.extend(list_payroll_records_in_range(
            corp_id=corp_id, start_ym=start_target_ym, end_ym=end_target_ym,
            pay_type='賞与',
        ))
    return records


# =====================================================================
# 財務／経理 — デビットカード明細
# =====================================================================

def init_debit_schema():
    """デビットカード明細テーブルを作成。"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS debit_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corporation TEXT NOT NULL,            -- '医療法人' / 'NPO法人' 等
            transaction_date DATE NOT NULL,
            year_month TEXT NOT NULL,             -- 'YYYY-MM'
            debit_account TEXT,                   -- 借方勘定科目
            tax_class TEXT,                       -- 借方税区分
            debit_item TEXT,                      -- 借方品目
            department_raw TEXT,                  -- 借方部門 (raw)
            department_clean TEXT,                -- prefix除去後
            subunit_id INTEGER REFERENCES pl_subunits(id),
            amount INTEGER NOT NULL DEFAULT 0,
            credit_account TEXT,                  -- 貸方勘定科目
            description TEXT,                     -- 摘要 (raw)
            vendor TEXT,                          -- 購入先 (摘要解析)
            purpose TEXT,                         -- 用途 (摘要解析)
            items_text TEXT,                      -- 品目リスト raw
            items_count INTEGER NOT NULL DEFAULT 0,
            check_status TEXT,
            sheet_name TEXT,
            source_filename TEXT,
            file_hash TEXT,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corporation, transaction_date, debit_account,
                   department_raw, amount, description)
        );
        CREATE INDEX IF NOT EXISTS idx_debit_ym ON debit_entries(year_month);
        CREATE INDEX IF NOT EXISTS idx_debit_corp ON debit_entries(corporation);
        CREATE INDEX IF NOT EXISTS idx_debit_acc ON debit_entries(debit_account);
        CREATE INDEX IF NOT EXISTS idx_debit_dept ON debit_entries(department_clean);
        CREATE INDEX IF NOT EXISTS idx_debit_vendor ON debit_entries(vendor);
        CREATE INDEX IF NOT EXISTS idx_debit_subunit ON debit_entries(subunit_id);

        CREATE TABLE IF NOT EXISTS debit_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_filename TEXT,
            corporation TEXT,
            file_hash TEXT,
            row_count INTEGER,
            inserted_count INTEGER,
            skipped_count INTEGER,
            year_months TEXT,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        -- レシート画像 → デビットExcel 自動追記の処理履歴
        -- file_path をユニークキーにして重複処理を防止
        CREATE TABLE IF NOT EXISTS receipt_processed (
            file_path TEXT PRIMARY KEY,
            file_name TEXT,
            file_size INTEGER,
            file_mtime REAL,
            facility TEXT,
            corporation TEXT,
            status TEXT,                    -- success / failed / manual_pending
            transaction_date TEXT,
            amount INTEGER,
            debit_account TEXT,
            vendor TEXT,
            excel_path TEXT,
            excel_sheet TEXT,
            excel_row INTEGER,
            ocr_confidence TEXT,
            ocr_model TEXT,
            ocr_input_tokens INTEGER,
            ocr_output_tokens INTEGER,
            ocr_result_json TEXT,
            error_message TEXT,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_recproc_status
            ON receipt_processed(status);
        CREATE INDEX IF NOT EXISTS idx_recproc_date
            ON receipt_processed(transaction_date);
        """)


def insert_debit_entries(rows: list[dict], filename: str,
                         corporation: str, file_hash: str) -> dict:
    """デビット明細を一括 INSERT OR IGNORE。重複は description 等で除外。"""
    if not rows:
        return {'inserted': 0, 'skipped': 0, 'year_months': []}

    with get_conn() as conn:
        inserted = 0
        skipped = 0
        for r in rows:
            cur = conn.execute("""
                INSERT OR IGNORE INTO debit_entries
                (corporation, transaction_date, year_month,
                 debit_account, tax_class, debit_item,
                 department_raw, department_clean, subunit_id,
                 amount, credit_account, description,
                 vendor, purpose, items_text, items_count,
                 check_status, sheet_name, source_filename, file_hash)
                VALUES (:corporation, :transaction_date, :year_month,
                        :debit_account, :tax_class, :debit_item,
                        :department_raw, :department_clean, :subunit_id,
                        :amount, :credit_account, :description,
                        :vendor, :purpose, :items_text, :items_count,
                        :check_status, :sheet_name, :filename, :file_hash)
            """, {**r, 'filename': filename, 'file_hash': file_hash})
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1

        yms = sorted({r['year_month'] for r in rows if r.get('year_month')})
        conn.execute("""
            INSERT INTO debit_imports
            (source_filename, corporation, file_hash, row_count,
             inserted_count, skipped_count, year_months)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (filename, corporation, file_hash, len(rows),
              inserted, skipped, ','.join(yms)))
        return {'inserted': inserted, 'skipped': skipped, 'year_months': yms}


def list_debit_imports(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM debit_imports ORDER BY imported_at DESC LIMIT ?",
            (limit,)
        ).fetchall()]


def is_receipt_processed(file_path: str) -> bool:
    """レシート画像が既に処理済 (success) か判定。"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM receipt_processed WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
    return bool(row) and row['status'] == 'success'


def get_receipt_processed(file_path: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM receipt_processed WHERE file_path = ?",
            (str(file_path),),
        ).fetchone()
    return dict(row) if row else None


def list_receipt_processed(limit: int = 50, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM receipt_processed"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY processed_at DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def upsert_receipt_processed(record: dict) -> None:
    """receipt_processed に UPSERT。
    file_path 必須。既存レコードがあれば status / 結果を上書き。"""
    if not record.get('file_path'):
        raise ValueError("file_path is required")
    cols = [
        'file_path', 'file_name', 'file_size', 'file_mtime',
        'facility', 'corporation', 'status',
        'transaction_date', 'amount', 'debit_account', 'vendor',
        'excel_path', 'excel_sheet', 'excel_row',
        'ocr_confidence', 'ocr_model',
        'ocr_input_tokens', 'ocr_output_tokens',
        'ocr_result_json', 'error_message',
    ]
    placeholders = ', '.join(f':{c}' for c in cols)
    update_cols = ', '.join(f'{c}=excluded.{c}' for c in cols if c != 'file_path')
    sql = (
        f"INSERT INTO receipt_processed ({', '.join(cols)}, processed_at) "
        f"VALUES ({placeholders}, CURRENT_TIMESTAMP) "
        f"ON CONFLICT(file_path) DO UPDATE SET "
        f"{update_cols}, processed_at=CURRENT_TIMESTAMP"
    )
    payload = {c: record.get(c) for c in cols}
    payload['file_path'] = str(payload['file_path'])
    with get_conn() as conn:
        conn.execute(sql, payload)


def delete_receipt_processed(file_path: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM receipt_processed WHERE file_path = ?",
            (str(file_path),),
        )


def get_debit_last_imported_at():
    """直近のデビット取込実行時刻を datetime で返す。未取込なら None。"""
    from datetime import datetime
    with get_conn() as conn:
        row = conn.execute(
            "SELECT imported_at FROM debit_imports "
            "ORDER BY imported_at DESC LIMIT 1"
        ).fetchone()
    if row is None or row['imported_at'] is None:
        return None
    s = str(row['imported_at'])
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def list_debit_year_months() -> list[str]:
    with get_conn() as conn:
        return [r['year_month'] for r in conn.execute(
            "SELECT DISTINCT year_month FROM debit_entries ORDER BY year_month DESC"
        ).fetchall()]


def list_debit_corporations() -> list[str]:
    with get_conn() as conn:
        return [r['corporation'] for r in conn.execute(
            "SELECT DISTINCT corporation FROM debit_entries ORDER BY corporation"
        ).fetchall()]


def list_debit_accounts() -> list[str]:
    with get_conn() as conn:
        return [r['debit_account'] for r in conn.execute(
            "SELECT DISTINCT debit_account FROM debit_entries "
            "WHERE debit_account IS NOT NULL AND debit_account != '' "
            "ORDER BY debit_account"
        ).fetchall()]


def list_debit_departments() -> list[str]:
    """登場した借方部門 (raw) の一覧"""
    with get_conn() as conn:
        return [r['department_raw'] for r in conn.execute(
            "SELECT DISTINCT department_raw FROM debit_entries "
            "WHERE department_raw IS NOT NULL AND department_raw != '' "
            "ORDER BY department_raw"
        ).fetchall()]


def query_debit_entries(year_months: list[str] | None = None,
                         corporation: str | None = None,
                         departments: list[str] | None = None,
                         accounts: list[str] | None = None,
                         vendor_like: str | None = None,
                         keyword: str | None = None) -> list[dict]:
    """フィルタ付きで debit_entries を取得。"""
    where = ["1=1"]
    params: list = []
    if year_months:
        ph = ','.join('?' * len(year_months))
        where.append(f"year_month IN ({ph})")
        params.extend(year_months)
    if corporation:
        where.append("corporation = ?")
        params.append(corporation)
    if departments:
        ph = ','.join('?' * len(departments))
        where.append(f"department_raw IN ({ph})")
        params.extend(departments)
    if accounts:
        ph = ','.join('?' * len(accounts))
        where.append(f"debit_account IN ({ph})")
        params.extend(accounts)
    if vendor_like:
        where.append("vendor LIKE ?")
        params.append(f"%{vendor_like}%")
    if keyword:
        where.append("(description LIKE ? OR debit_item LIKE ? OR vendor LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])

    sql = f"""
        SELECT * FROM debit_entries
        WHERE {' AND '.join(where)}
        ORDER BY transaction_date, id
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def delete_debit_by_year_month(year_month: str,
                                corporation: str | None = None) -> int:
    """指定月のデビット明細を削除（再取込用）。"""
    where = ["year_month = ?"]
    params: list = [year_month]
    if corporation:
        where.append("corporation = ?")
        params.append(corporation)
    with get_conn() as conn:
        cur = conn.execute(
            f"DELETE FROM debit_entries WHERE {' AND '.join(where)}",
            params,
        )
        return cur.rowcount


# ============================================================
# 現金立替清算スキーマ (cash_advance_*)
# ============================================================
#
# 設計:
#   1レシート = 1 cash_advance_receipts レコード
#   1レシートが複数施設に按分される場合、cash_advance_entries は
#   施設ごとに 1 行ずつ展開される (出納帳の現状と同じ表現)。
#   出納帳としてはこの entries を日付順に並べたものを意味する。
#
def init_cash_advance_schema():
    """現金立替清算系テーブルを作成。"""
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cash_advance_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE,                -- 元レシートのファイルパス (NULL可: 手動入力時)
            file_name TEXT,
            file_size INTEGER,
            file_mtime REAL,
            corporation TEXT,                     -- '医療法人' / 'NPO法人'
            facility_folder TEXT,                 -- レシートが置かれていた施設フォルダ名
            transaction_date DATE,
            year_month TEXT,                      -- 'YYYY-MM' (transaction_date より)
            vendor TEXT,                          -- 支払先 (店舗名)
            purpose TEXT,                         -- 用途
            total_amount INTEGER,                 -- レシート合計 (税込)
            payee TEXT,                           -- 立替えた人 (氏名)
            payment_status TEXT DEFAULT 'pending',-- pending / settled
            ocr_status TEXT,                      -- success / failed / manual / null
            ocr_confidence TEXT,
            ocr_model TEXT,
            ocr_input_tokens INTEGER,
            ocr_output_tokens INTEGER,
            ocr_result_json TEXT,
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_cashrcp_corp ON cash_advance_receipts(corporation);
        CREATE INDEX IF NOT EXISTS idx_cashrcp_ym   ON cash_advance_receipts(year_month);
        CREATE INDEX IF NOT EXISTS idx_cashrcp_fac  ON cash_advance_receipts(facility_folder);

        CREATE TABLE IF NOT EXISTS cash_advance_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER REFERENCES cash_advance_receipts(id) ON DELETE SET NULL,
            corporation TEXT NOT NULL,
            transaction_date DATE NOT NULL,
            year_month TEXT NOT NULL,             -- 'YYYY-MM'
            debit_account TEXT,                   -- 借方勘定科目 (例: 燃料費)
            tax_class TEXT,                       -- 借方税区分 (例: 課対仕入10%)
            debit_item TEXT,                      -- 品目
            department_raw TEXT,                  -- 借方部門 (例: '4.1.SORATOいなみ')
            department_clean TEXT,
            subunit_id INTEGER REFERENCES pl_subunits(id),
            amount_in INTEGER NOT NULL DEFAULT 0, -- 入金 (現金が増える側)
            amount_out INTEGER NOT NULL DEFAULT 0,-- 出金 (現金が減る側)
            description TEXT,                     -- 備考 (出納帳備考列)
            vendor TEXT,                          -- 購入先
            purpose TEXT,
            payee TEXT,                           -- 立替者
            entry_kind TEXT DEFAULT 'expense',    -- expense / income / transfer / opening
            split_total_amount INTEGER,           -- 元レシート合計 (按分時の参考)
            split_facility_count INTEGER DEFAULT 1,
            sheet_name TEXT,                      -- パース元のシート名
            source_filename TEXT,
            file_hash TEXT,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(corporation, transaction_date, debit_account,
                   department_raw, amount_in, amount_out, description)
        );
        CREATE INDEX IF NOT EXISTS idx_cash_ym   ON cash_advance_entries(year_month);
        CREATE INDEX IF NOT EXISTS idx_cash_corp ON cash_advance_entries(corporation);
        CREATE INDEX IF NOT EXISTS idx_cash_acc  ON cash_advance_entries(debit_account);
        CREATE INDEX IF NOT EXISTS idx_cash_dept ON cash_advance_entries(department_raw);
        CREATE INDEX IF NOT EXISTS idx_cash_rcpt ON cash_advance_entries(receipt_id);

        CREATE TABLE IF NOT EXISTS cash_advance_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_filename TEXT,
            corporation TEXT,
            file_hash TEXT,
            row_count INTEGER,
            inserted_count INTEGER,
            skipped_count INTEGER,
            year_months TEXT,
            imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)


def insert_cash_entries(rows: list[dict], filename: str,
                        corporation: str, file_hash: str) -> dict:
    """現金立替明細を一括 INSERT OR IGNORE。"""
    if not rows:
        return {'inserted': 0, 'skipped': 0, 'year_months': []}

    with get_conn() as conn:
        inserted = 0
        skipped = 0
        for r in rows:
            cur = conn.execute("""
                INSERT OR IGNORE INTO cash_advance_entries
                (receipt_id, corporation, transaction_date, year_month,
                 debit_account, tax_class, debit_item,
                 department_raw, department_clean, subunit_id,
                 amount_in, amount_out, description, vendor, purpose,
                 payee, entry_kind,
                 split_total_amount, split_facility_count,
                 sheet_name, source_filename, file_hash)
                VALUES (:receipt_id, :corporation, :transaction_date, :year_month,
                        :debit_account, :tax_class, :debit_item,
                        :department_raw, :department_clean, :subunit_id,
                        :amount_in, :amount_out, :description, :vendor, :purpose,
                        :payee, :entry_kind,
                        :split_total_amount, :split_facility_count,
                        :sheet_name, :filename, :file_hash)
            """, {
                'receipt_id': r.get('receipt_id'),
                'corporation': r.get('corporation', corporation),
                'transaction_date': r.get('transaction_date'),
                'year_month': r.get('year_month'),
                'debit_account': r.get('debit_account', ''),
                'tax_class': r.get('tax_class', ''),
                'debit_item': r.get('debit_item', ''),
                'department_raw': r.get('department_raw', ''),
                'department_clean': r.get('department_clean', ''),
                'subunit_id': r.get('subunit_id'),
                'amount_in': r.get('amount_in', 0),
                'amount_out': r.get('amount_out', 0),
                'description': r.get('description', ''),
                'vendor': r.get('vendor', ''),
                'purpose': r.get('purpose', ''),
                'payee': r.get('payee', ''),
                'entry_kind': r.get('entry_kind', 'expense'),
                'split_total_amount': r.get('split_total_amount'),
                'split_facility_count': r.get('split_facility_count', 1),
                'sheet_name': r.get('sheet_name', ''),
                'filename': filename,
                'file_hash': file_hash,
            })
            if cur.rowcount > 0:
                inserted += 1
            else:
                skipped += 1

        yms = sorted({r['year_month'] for r in rows if r.get('year_month')})
        conn.execute("""
            INSERT INTO cash_advance_imports
            (source_filename, corporation, file_hash, row_count,
             inserted_count, skipped_count, year_months)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (filename, corporation, file_hash, len(rows),
              inserted, skipped, ','.join(yms)))
        return {'inserted': inserted, 'skipped': skipped, 'year_months': yms}


def insert_manual_cash_entry(record: dict) -> int:
    """1行だけ手入力で出納帳に追加。重複制約は緩和し常にINSERT。
    Returns: 作成されたcash_advance_entries.id"""
    fields = [
        'receipt_id', 'corporation', 'transaction_date', 'year_month',
        'debit_account', 'tax_class', 'debit_item',
        'department_raw', 'department_clean', 'subunit_id',
        'amount_in', 'amount_out', 'description',
        'vendor', 'purpose', 'payee', 'entry_kind',
        'split_total_amount', 'split_facility_count',
        'sheet_name', 'source_filename', 'file_hash',
    ]
    payload = {f: record.get(f) for f in fields}
    payload.setdefault('entry_kind', 'expense')
    payload.setdefault('source_filename', '_manual')
    payload.setdefault('file_hash', 'manual')
    cols = ', '.join(fields)
    ph = ', '.join(f':{f}' for f in fields)
    sql = f"INSERT INTO cash_advance_entries ({cols}) VALUES ({ph})"
    with get_conn() as conn:
        cur = conn.execute(sql, payload)
        return cur.lastrowid


def update_cash_entry(entry_id: int, fields: dict) -> int:
    """cash_advance_entries の UPDATE。fields は更新対象のカラム名→値。"""
    if not fields:
        return 0
    sets = ', '.join(f"{k} = :{k}" for k in fields.keys())
    payload = {**fields, 'id': entry_id}
    sql = f"UPDATE cash_advance_entries SET {sets} WHERE id = :id"
    with get_conn() as conn:
        cur = conn.execute(sql, payload)
        return cur.rowcount


def delete_cash_entry(entry_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM cash_advance_entries WHERE id = ?", (entry_id,)
        )
        return cur.rowcount


def list_cash_imports(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM cash_advance_imports ORDER BY imported_at DESC LIMIT ?",
            (limit,)
        ).fetchall()]


def list_cash_year_months() -> list[str]:
    with get_conn() as conn:
        return [r['year_month'] for r in conn.execute(
            "SELECT DISTINCT year_month FROM cash_advance_entries ORDER BY year_month DESC"
        ).fetchall()]


def list_cash_corporations() -> list[str]:
    with get_conn() as conn:
        return [r['corporation'] for r in conn.execute(
            "SELECT DISTINCT corporation FROM cash_advance_entries ORDER BY corporation"
        ).fetchall()]


def list_cash_accounts() -> list[str]:
    with get_conn() as conn:
        return [r['debit_account'] for r in conn.execute(
            "SELECT DISTINCT debit_account FROM cash_advance_entries "
            "WHERE debit_account IS NOT NULL AND debit_account != '' "
            "ORDER BY debit_account"
        ).fetchall()]


def list_cash_departments() -> list[str]:
    with get_conn() as conn:
        return [r['department_raw'] for r in conn.execute(
            "SELECT DISTINCT department_raw FROM cash_advance_entries "
            "WHERE department_raw IS NOT NULL AND department_raw != '' "
            "ORDER BY department_raw"
        ).fetchall()]


def query_cash_entries(year_months: list[str] | None = None,
                       corporation: str | None = None,
                       departments: list[str] | None = None,
                       accounts: list[str] | None = None,
                       keyword: str | None = None,
                       order_desc: bool = False) -> list[dict]:
    """フィルタ付きで cash_advance_entries を取得。"""
    where = ["1=1"]
    params: list = []
    if year_months:
        ph = ','.join('?' * len(year_months))
        where.append(f"year_month IN ({ph})")
        params.extend(year_months)
    if corporation:
        where.append("corporation = ?")
        params.append(corporation)
    if departments:
        ph = ','.join('?' * len(departments))
        where.append(f"department_raw IN ({ph})")
        params.extend(departments)
    if accounts:
        ph = ','.join('?' * len(accounts))
        where.append(f"debit_account IN ({ph})")
        params.extend(accounts)
    if keyword:
        where.append("(description LIKE ? OR vendor LIKE ? OR purpose LIKE ?)")
        kw = f"%{keyword}%"
        params.extend([kw, kw, kw])

    order = "DESC" if order_desc else "ASC"
    sql = f"""
        SELECT * FROM cash_advance_entries
        WHERE {' AND '.join(where)}
        ORDER BY transaction_date {order}, id {order}
    """
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def delete_cash_by_year_month(year_month: str,
                               corporation: str | None = None) -> int:
    where = ["year_month = ?"]
    params: list = [year_month]
    if corporation:
        where.append("corporation = ?")
        params.append(corporation)
    with get_conn() as conn:
        cur = conn.execute(
            f"DELETE FROM cash_advance_entries WHERE {' AND '.join(where)}",
            params,
        )
        return cur.rowcount


def get_cash_last_imported_at():
    from datetime import datetime
    with get_conn() as conn:
        row = conn.execute(
            "SELECT imported_at FROM cash_advance_imports "
            "ORDER BY imported_at DESC LIMIT 1"
        ).fetchone()
    if row is None or row['imported_at'] is None:
        return None
    s = str(row['imported_at'])
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f'):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def upsert_cash_receipt(record: dict) -> int:
    """cash_advance_receipts に UPSERT。file_path がキー。
    file_path が None ならINSERTのみ。返り値=receipt_id。"""
    cols = [
        'file_path', 'file_name', 'file_size', 'file_mtime',
        'corporation', 'facility_folder',
        'transaction_date', 'year_month',
        'vendor', 'purpose', 'total_amount',
        'payee', 'payment_status',
        'ocr_status', 'ocr_confidence', 'ocr_model',
        'ocr_input_tokens', 'ocr_output_tokens', 'ocr_result_json',
        'note',
    ]
    payload = {c: record.get(c) for c in cols}

    with get_conn() as conn:
        if payload.get('file_path'):
            placeholders = ', '.join(f':{c}' for c in cols)
            update_cols = ', '.join(
                f'{c}=excluded.{c}' for c in cols if c != 'file_path'
            )
            sql = (
                f"INSERT INTO cash_advance_receipts ({', '.join(cols)}, updated_at) "
                f"VALUES ({placeholders}, CURRENT_TIMESTAMP) "
                f"ON CONFLICT(file_path) DO UPDATE SET "
                f"{update_cols}, updated_at=CURRENT_TIMESTAMP"
            )
            conn.execute(sql, payload)
            row = conn.execute(
                "SELECT id FROM cash_advance_receipts WHERE file_path = ?",
                (payload['file_path'],)
            ).fetchone()
            return row['id'] if row else 0
        else:
            placeholders = ', '.join(f':{c}' for c in cols)
            sql = (
                f"INSERT INTO cash_advance_receipts ({', '.join(cols)}) "
                f"VALUES ({placeholders})"
            )
            cur = conn.execute(sql, payload)
            return cur.lastrowid


def get_cash_receipt(receipt_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM cash_advance_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
    return dict(row) if row else None


def get_cash_receipt_by_path(file_path: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM cash_advance_receipts WHERE file_path = ?",
            (str(file_path),)
        ).fetchone()
    return dict(row) if row else None


def list_cash_entries_by_receipt(receipt_id: int) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM cash_advance_entries WHERE receipt_id = ? "
            "ORDER BY id", (receipt_id,)
        ).fetchall()]


def delete_cash_entries_by_receipt(receipt_id: int) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM cash_advance_entries WHERE receipt_id = ?",
            (receipt_id,)
        )
        return cur.rowcount


# ============================================================
# 車両管理スキーマ (vehicles / vehicle_inspections)
# ============================================================

def init_vehicle_schema():
    """車両管理テーブルを作成。

    vehicles            … 車両マスタ（1台=1行）
    vehicle_inspections … 車検証履歴（車検更新ごとに行追加。最新行が現行有効）
    """
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corporation TEXT NOT NULL,            -- '医療法人社団EMIFULL' / 'NPO法人EMIFULL' / '奉志会(旧)' 等
            facility_name TEXT NOT NULL,          -- 配属施設名（PDFファイル名【】より）/ '本部'
            registration_number TEXT,             -- 自動車登録番号 (例: 奈良 501 め 4000)
            chassis_number TEXT,                  -- 車台番号 (例: NCP81-5169925)
            maker TEXT,                           -- メーカー (例: トヨタ)
            car_name TEXT,                        -- 車名 (例: シエンタ)
            model_code TEXT,                      -- 型式 (例: DBA-NCP81G)
            body_shape TEXT,                      -- 車体の形状
            seating_capacity INTEGER,             -- 乗車定員
            first_registration_ym TEXT,           -- 初度登録年月 (YYYY-MM)
            insurance_status TEXT DEFAULT '未加入', -- '加入済' / '未加入'
            insurance_company TEXT,               -- 自賠責保険会社
            insurance_expiry DATE,                -- 自賠責保険満了日
            child_safety_device TEXT DEFAULT '未設置',  -- '設置済' / '対象外' / '未設置'
            photo_path TEXT,                      -- 車両写真の保存パス（オプション）
            scrapped INTEGER DEFAULT 0,           -- 0=現役 1=廃車
            scrapped_date DATE,                   -- 廃車年月日
            scrap_reason TEXT,                    -- 廃車理由メモ
            memo TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chassis_number)
        );

        CREATE INDEX IF NOT EXISTS idx_vehicle_corp     ON vehicles(corporation);
        CREATE INDEX IF NOT EXISTS idx_vehicle_facility ON vehicles(facility_name);
        CREATE INDEX IF NOT EXISTS idx_vehicle_scrap    ON vehicles(scrapped);

        CREATE TABLE IF NOT EXISTS vehicle_inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
            inspection_date DATE,                 -- 登録/交付年月日
            expiry_date DATE,                     -- 有効期間の満了する日（NULL: 古い車検証で日付不明）
            mileage_km INTEGER,                   -- 走行距離計表示値 (km)
            mileage_recorded_date DATE,           -- 走行距離記録日
            pdf_path TEXT,                        -- 車検証PDFのファイルパス
            pdf_filename TEXT,                    -- 元ファイル名
            document_path TEXT,                   -- 電子車検証PDF/スクショの保存パス
            document_filename TEXT,               -- 電子車検証の元ファイル名
            document_kind TEXT,                   -- 'pdf' / 'image'
            qr_text TEXT,                         -- QRコード読取結果（生データ）
            qr_status TEXT,                       -- QR読取ステータス
            extracted_json TEXT,                  -- QR/PDF本文から抽出した候補JSON
            is_current INTEGER DEFAULT 1,         -- 1=現行(最新) 0=過去
            note TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_insp_vehicle ON vehicle_inspections(vehicle_id);
        CREATE INDEX IF NOT EXISTS idx_insp_expiry  ON vehicle_inspections(expiry_date);
        CREATE INDEX IF NOT EXISTS idx_insp_current ON vehicle_inspections(is_current);
        """)

        # === マイグレーション: vehicles に tax_exemption_status 列を追加 ===
        v_cols = {c['name'] for c in conn.execute("PRAGMA table_info(vehicles)").fetchall()}
        if 'tax_exemption_status' not in v_cols:
            conn.execute(
                "ALTER TABLE vehicles ADD COLUMN tax_exemption_status TEXT DEFAULT '未'"
            )

        # === マイグレーション: 旧スキーマで expiry_date NOT NULL の場合は緩和 ===
        cols = conn.execute("PRAGMA table_info(vehicle_inspections)").fetchall()
        insp_cols = {c['name'] for c in cols}
        for col_name, col_def in {
            'document_path': 'TEXT',
            'document_filename': 'TEXT',
            'document_kind': 'TEXT',
            'qr_text': 'TEXT',
            'qr_status': 'TEXT',
            'extracted_json': 'TEXT',
        }.items():
            if col_name not in insp_cols:
                conn.execute(f"ALTER TABLE vehicle_inspections ADD COLUMN {col_name} {col_def}")

        conn.execute("""
            UPDATE vehicle_inspections
               SET document_path = COALESCE(document_path, pdf_path),
                   document_filename = COALESCE(document_filename, pdf_filename),
                   document_kind = COALESCE(document_kind, CASE WHEN pdf_path IS NOT NULL THEN 'pdf' END)
             WHERE pdf_path IS NOT NULL
        """)

        has_not_null = any(c['name'] == 'expiry_date' and c['notnull'] for c in cols)
        if has_not_null:
            conn.executescript("""
                CREATE TABLE vehicle_inspections_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                    inspection_date DATE,
                    expiry_date DATE,
                    mileage_km INTEGER,
                    mileage_recorded_date DATE,
                    pdf_path TEXT,
                    pdf_filename TEXT,
                    document_path TEXT,
                    document_filename TEXT,
                    document_kind TEXT,
                    qr_text TEXT,
                    qr_status TEXT,
                    extracted_json TEXT,
                    is_current INTEGER DEFAULT 1,
                    note TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO vehicle_inspections_new
                  SELECT id, vehicle_id, inspection_date, expiry_date, mileage_km,
                         mileage_recorded_date, pdf_path, pdf_filename,
                         COALESCE(document_path, pdf_path),
                         COALESCE(document_filename, pdf_filename),
                         COALESCE(document_kind, CASE WHEN pdf_path IS NOT NULL THEN 'pdf' END),
                         qr_text, qr_status, extracted_json,
                         is_current, note, created_at
                  FROM vehicle_inspections;
                DROP TABLE vehicle_inspections;
                ALTER TABLE vehicle_inspections_new RENAME TO vehicle_inspections;

                CREATE INDEX IF NOT EXISTS idx_insp_vehicle ON vehicle_inspections(vehicle_id);
                CREATE INDEX IF NOT EXISTS idx_insp_expiry  ON vehicle_inspections(expiry_date);
                CREATE INDEX IF NOT EXISTS idx_insp_current ON vehicle_inspections(is_current);
            """)


def upsert_vehicle(v: dict) -> int:
    """車両マスタをUPSERT。chassis_numberをキー。

    chassis_numberがNoneの場合は同じ corporation+registration_number を持つ既存と突合。
    戻り値: vehicle_id
    """
    now = datetime.now().isoformat(sep=' ', timespec='seconds')

    # Noneだと SQLite の DEFAULT 句が無視されて NULL が入ってしまうので明示的に補う
    v = dict(v)
    if v.get('scrapped') is None:
        v['scrapped'] = 0
    if not v.get('insurance_status'):
        v['insurance_status'] = '未加入'
    if not v.get('child_safety_device'):
        v['child_safety_device'] = '未設置'

    with get_conn() as conn:
        existing = None
        if v.get('chassis_number'):
            existing = conn.execute(
                "SELECT id FROM vehicles WHERE chassis_number = ?",
                (v['chassis_number'],),
            ).fetchone()
        if not existing and v.get('registration_number'):
            existing = conn.execute(
                "SELECT id FROM vehicles WHERE corporation = ? AND registration_number = ?",
                (v['corporation'], v['registration_number']),
            ).fetchone()

        fields = [
            'corporation', 'facility_name', 'registration_number',
            'chassis_number', 'maker', 'car_name', 'model_code',
            'body_shape', 'seating_capacity', 'first_registration_ym',
            'insurance_status', 'insurance_company', 'insurance_expiry',
            'child_safety_device', 'tax_exemption_status', 'photo_path',
            'scrapped', 'scrapped_date', 'scrap_reason', 'memo',
        ]

        if existing:
            sets = ', '.join(f"{f} = COALESCE(:{f}, {f})" for f in fields)
            sql = f"UPDATE vehicles SET {sets}, updated_at = :updated_at WHERE id = :id"
            payload = {f: v.get(f) for f in fields}
            payload.update({'updated_at': now, 'id': existing['id']})
            conn.execute(sql, payload)
            return existing['id']
        else:
            cols = ', '.join(fields)
            ph = ', '.join(f":{f}" for f in fields)
            sql = f"INSERT INTO vehicles ({cols}) VALUES ({ph})"
            payload = {f: v.get(f) for f in fields}
            cur = conn.execute(sql, payload)
            return cur.lastrowid


def list_vehicles(include_scrapped: bool = False) -> list[dict]:
    sql = """
        SELECT v.*,
               i.expiry_date AS current_expiry_date,
               i.inspection_date AS current_inspection_date,
               i.mileage_km AS current_mileage_km,
               i.mileage_recorded_date AS current_mileage_recorded_date,
               i.pdf_path AS current_pdf_path,
               i.pdf_filename AS current_pdf_filename,
               i.document_path AS current_document_path,
               i.document_filename AS current_document_filename,
               i.document_kind AS current_document_kind,
               i.qr_status AS current_qr_status
          FROM vehicles v
          LEFT JOIN vehicle_inspections i
                 ON i.vehicle_id = v.id AND i.is_current = 1
    """
    if not include_scrapped:
        sql += " WHERE v.scrapped = 0"
    sql += " ORDER BY v.corporation, v.facility_name, v.registration_number"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql).fetchall()]


def get_vehicle(vehicle_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT v.*,
                      i.expiry_date AS current_expiry_date,
                      i.inspection_date AS current_inspection_date,
                      i.mileage_km AS current_mileage_km,
                      i.mileage_recorded_date AS current_mileage_recorded_date,
                      i.pdf_path AS current_pdf_path,
                      i.pdf_filename AS current_pdf_filename,
                      i.document_path AS current_document_path,
                      i.document_filename AS current_document_filename,
                      i.document_kind AS current_document_kind,
                      i.qr_status AS current_qr_status,
                      i.id AS current_inspection_id
                 FROM vehicles v
                 LEFT JOIN vehicle_inspections i
                        ON i.vehicle_id = v.id AND i.is_current = 1
                WHERE v.id = ?""",
            (vehicle_id,),
        ).fetchone()
        return dict(row) if row else None


def list_inspections(vehicle_id: int) -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            """SELECT * FROM vehicle_inspections
                WHERE vehicle_id = ?
                ORDER BY expiry_date DESC, id DESC""",
            (vehicle_id,),
        ).fetchall()]


def add_inspection(vehicle_id: int, insp: dict) -> int:
    """新しい車検証履歴を追加し、過去行の is_current=0 にする。

    新規 expiry_date が既存の最新より古ければ is_current は付け替えしない。
    """
    with get_conn() as conn:
        latest = conn.execute(
            """SELECT id, expiry_date FROM vehicle_inspections
                WHERE vehicle_id = ?
                ORDER BY expiry_date DESC, id DESC
                LIMIT 1""",
            (vehicle_id,),
        ).fetchone()

        new_expiry = insp.get('expiry_date')
        becomes_current = True
        if latest and latest['expiry_date'] and new_expiry:
            becomes_current = str(new_expiry) >= str(latest['expiry_date'])

        if becomes_current:
            conn.execute(
                "UPDATE vehicle_inspections SET is_current = 0 WHERE vehicle_id = ?",
                (vehicle_id,),
            )

        cur = conn.execute(
            """INSERT INTO vehicle_inspections
               (vehicle_id, inspection_date, expiry_date, mileage_km,
                mileage_recorded_date, pdf_path, pdf_filename,
                document_path, document_filename, document_kind,
                qr_text, qr_status, extracted_json, is_current, note)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                vehicle_id,
                insp.get('inspection_date'),
                insp.get('expiry_date'),
                insp.get('mileage_km'),
                insp.get('mileage_recorded_date'),
                insp.get('pdf_path'),
                insp.get('pdf_filename'),
                insp.get('document_path') or insp.get('pdf_path'),
                insp.get('document_filename') or insp.get('pdf_filename'),
                insp.get('document_kind') or ('pdf' if insp.get('pdf_path') else None),
                insp.get('qr_text'),
                insp.get('qr_status'),
                insp.get('extracted_json'),
                1 if becomes_current else 0,
                insp.get('note'),
            ),
        )
        return cur.lastrowid


def update_vehicle_fields(vehicle_id: int, **fields) -> None:
    """車両マスタの個別フィールドを更新。"""
    if not fields:
        return
    sets = ', '.join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [
        datetime.now().isoformat(sep=' ', timespec='seconds'),
        vehicle_id,
    ]
    with get_conn() as conn:
        conn.execute(
            f"UPDATE vehicles SET {sets}, updated_at = ? WHERE id = ?",
            params,
        )


def scrap_vehicle(vehicle_id: int, scrapped_date: str, reason: str | None = None) -> None:
    update_vehicle_fields(
        vehicle_id,
        scrapped=1, scrapped_date=scrapped_date, scrap_reason=reason,
    )


def unscrap_vehicle(vehicle_id: int) -> None:
    update_vehicle_fields(
        vehicle_id,
        scrapped=0, scrapped_date=None, scrap_reason=None,
    )


def delete_vehicle(vehicle_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM vehicles WHERE id = ?", (vehicle_id,))


def find_vehicle_by_registration(reg: str, *, only_active: bool = False) -> dict | None:
    """自動車登録番号で車両を検索（揺らぎ対応: スペース正規化のうえ部分一致）。

    例: '姫路 501 ま 36-90' でも '姫路501ま3690' でもヒットする。
    """
    if not reg:
        return None
    norm = reg.replace(' ', '').replace('　', '').replace('-', '').replace('ー', '')
    sql = "SELECT * FROM vehicles"
    if only_active:
        sql += " WHERE scrapped = 0"
    with get_conn() as conn:
        rows = conn.execute(sql).fetchall()
    for r in rows:
        rd = dict(r)
        cur = (rd.get('registration_number') or '').replace(' ', '').replace('　', '').replace('-', '').replace('ー', '')
        if cur and cur == norm:
            return rd
    return None


def vehicle_corporation_counts(include_scrapped: bool = False) -> dict[str, int]:
    sql = "SELECT corporation, COUNT(*) AS cnt FROM vehicles"
    if not include_scrapped:
        sql += " WHERE scrapped = 0"
    sql += " GROUP BY corporation"
    with get_conn() as conn:
        return {r['corporation']: r['cnt'] for r in conn.execute(sql).fetchall()}
