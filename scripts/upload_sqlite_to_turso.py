"""Upload the local SQLite database into an empty Turso database."""
import os
import sqlite3
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCAL_DB = ROOT / "data" / "uriage.db"
UPLOAD_BATCH_SIZE = 100
UPLOAD_RETRIES = 6


def connect_remote():
    url = os.getenv("TURSO_DATABASE_URL")
    token = os.getenv("TURSO_AUTH_TOKEN")
    if not url or not token:
        raise SystemExit(
            "TURSO_DATABASE_URL と TURSO_AUTH_TOKEN をPowerShellで設定してください。"
        )

    try:
        import libsql
    except ImportError as exc:
        raise SystemExit("libsql が見つかりません。upload_db_to_turso.ps1 から実行してください。") from exc

    return libsql.connect(database=url, auth_token=token)


def sqlite_rows(conn, sql, params=()):
    conn.row_factory = sqlite3.Row
    return conn.execute(sql, params).fetchall()


def remote_table_names(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [row[0] for row in rows]


def execute_script(conn, script):
    statement = ""
    for line in script.splitlines():
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            sql = statement.strip()
            if sql:
                conn.execute(sql)
            statement = ""
    if statement.strip():
        conn.execute(statement)


def reconnect_remote():
    conn = connect_remote()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.commit()
    return conn


def run_with_retry(remote, action, label):
    for attempt in range(1, UPLOAD_RETRIES + 1):
        try:
            action(remote)
            return remote
        except Exception as exc:
            if attempt >= UPLOAD_RETRIES:
                raise
            wait_seconds = min(20, attempt * 3)
            print(f"  {label}: 通信が切れました。{wait_seconds}秒後に再試行します ({attempt}/{UPLOAD_RETRIES})")
            print(f"    {exc}")
            time.sleep(wait_seconds)
            remote = reconnect_remote()
    return remote


def main():
    if not LOCAL_DB.exists():
        raise SystemExit(f"SQLite DBが見つかりません: {LOCAL_DB}")

    replace = "--replace" in sys.argv
    local = sqlite3.connect(str(LOCAL_DB))
    remote = connect_remote()
    remote.execute("PRAGMA foreign_keys = OFF")
    remote.commit()

    existing = remote_table_names(remote)
    if existing and not replace:
        print("Turso側にすでにテーブルがあります。安全のため中止します。")
        print("もう一度すべて入れ直す場合だけ --replace を付けて実行してください。")
        print("既存テーブル:", ", ".join(existing[:20]))
        raise SystemExit(1)

    if replace:
        for name in reversed(existing):
            remote.execute(f'DROP TABLE IF EXISTS "{name}"')
        remote.commit()

    objects = sqlite_rows(
        local,
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_master
        WHERE sql IS NOT NULL
          AND name NOT LIKE 'sqlite_%'
        ORDER BY
          CASE type
            WHEN 'table' THEN 1
            WHEN 'index' THEN 2
            WHEN 'trigger' THEN 3
            WHEN 'view' THEN 4
            ELSE 5
          END,
          rowid
        """,
    )

    tables = [row for row in objects if row["type"] == "table"]
    others = [row for row in objects if row["type"] != "table"]

    for row in tables:
        execute_script(remote, row["sql"])
    remote.commit()

    for table in tables:
        name = table["name"]
        cols = sqlite_rows(local, f'PRAGMA table_info("{name}")')
        col_names = [col["name"] for col in cols]
        if not col_names:
            continue
        placeholders = ", ".join(["?"] * len(col_names))
        quoted_cols = ", ".join(f'"{col}"' for col in col_names)
        insert_sql = f'INSERT OR REPLACE INTO "{name}" ({quoted_cols}) VALUES ({placeholders})'
        rows = [tuple(row) for row in local.execute(f'SELECT {quoted_cols} FROM "{name}"').fetchall()]
        if rows:
            print(f"Uploading {name}: {len(rows)} rows")
            for start in range(0, len(rows), UPLOAD_BATCH_SIZE):
                batch = rows[start:start + UPLOAD_BATCH_SIZE]
                def upload_batch(conn):
                    conn.executemany(insert_sql, batch)
                    conn.commit()

                remote = run_with_retry(
                    remote,
                    upload_batch,
                    f"{name} {start + 1}-{min(start + UPLOAD_BATCH_SIZE, len(rows))}/{len(rows)}",
                )
                if len(rows) > UPLOAD_BATCH_SIZE:
                    done = min(start + UPLOAD_BATCH_SIZE, len(rows))
                    print(f"  {name}: {done}/{len(rows)}")
    remote.commit()

    for row in others:
        try:
            execute_script(remote, row["sql"])
        except Exception as exc:
            print(f"注意: {row['type']} {row['name']} は作成をスキップしました: {exc}")
    remote.commit()

    table_count = len(remote_table_names(remote))
    remote.execute("PRAGMA foreign_keys = ON")
    remote.commit()
    print("OK: Tursoへアップロードできました。")
    print(f"Tables: {table_count}")


if __name__ == "__main__":
    main()
