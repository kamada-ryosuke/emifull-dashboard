"""Import only PRIME CSV data into the configured Turso database.

This script deliberately touches only PRIME tables:
  - prime_pl_entries / prime_pl_imports
  - prime_journal_entries / prime_journal_imports

It never drops or rewrites the main disability-business tables.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_DIR = (
    Path.home()
    / "株式会社ＥＭＩＦＵＬＬ Dropbox"
    / "障がい事業部"
    / "99.ゴミ箱"
    / "2606"
)


def _add_project_to_path() -> None:
    root = str(ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def _find_pl_files(source_dir: Path) -> list[Path]:
    return sorted(source_dir.glob("試算表：損益計算書_株式会社PRIME*.csv"))


def _find_journal_files(source_dir: Path) -> list[Path]:
    return sorted(source_dir.glob("仕訳帳*.csv"))


def _validate_credentials() -> None:
    for key in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        value = os.getenv(key)
        if value:
            os.environ[key] = _normalize_secret_value(value, key)

    missing = [
        key
        for key in ("TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN")
        if not os.getenv(key)
    ]
    if missing:
        raise SystemExit(
            "Missing environment variables: "
            + ", ".join(missing)
            + "\nRun import_prime_to_turso.ps1 so the values can be entered safely."
        )
    database_url = os.getenv("TURSO_DATABASE_URL", "")
    if not database_url.startswith("libsql://"):
        raise SystemExit(
            "TURSO_DATABASE_URL must start with libsql:// . "
            "Paste only the value, not the whole TOML line."
        )


def _normalize_secret_value(value: str, key: str) -> str:
    value = (value or "").strip()
    prefix = f"{key}="
    compact = value.replace(" ", "")
    if compact.startswith(prefix):
        value = value.split("=", 1)[1].strip()
    elif "=" in value and value.split("=", 1)[0].strip() == key:
        value = value.split("=", 1)[1].strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1].strip()
    return value


def _delete_prime_journal_file(file_hash: str) -> None:
    from lib import db

    with db.get_conn() as conn:
        conn.execute(
            "DELETE FROM prime_journal_entries WHERE file_hash = ?",
            (file_hash,),
        )
        conn.execute(
            "DELETE FROM prime_journal_imports WHERE file_hash = ?",
            (file_hash,),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import PRIME P/L and journal CSV files to Turso safely."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Folder containing PRIME trial balance and journal CSV files.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse files and print counts without connecting to Turso.",
    )
    parser.add_argument(
        "--skip-journal",
        action="store_true",
        help="Import only PRIME P/L CSV files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_dir = args.source_dir.expanduser().resolve()
    if not source_dir.exists():
        raise SystemExit(f"Source folder was not found: {source_dir}")

    _add_project_to_path()
    from lib import prime_parser

    pl_files = _find_pl_files(source_dir)
    journal_files = [] if args.skip_journal else _find_journal_files(source_dir)

    if not pl_files:
        raise SystemExit(f"No PRIME P/L CSV files were found in: {source_dir}")

    print(f"Source folder: {source_dir}")
    print(f"P/L files: {len(pl_files)}")
    print(f"Journal files: {len(journal_files)}")

    parsed_pl = []
    for path in pl_files:
        result = prime_parser.parse_prime_pl_csv(path, path.name)
        if result.error:
            raise SystemExit(f"P/L parse error: {path.name}: {result.error}")
        parsed_pl.append((path, result))

    parsed_journals = []
    for path in journal_files:
        result = prime_parser.parse_prime_journal_csv(path, path.name)
        if result.error:
            raise SystemExit(f"Journal parse error: {path.name}: {result.error}")
        parsed_journals.append((path, result))

    pl_entries = sum(len(result.entries) for _, result in parsed_pl)
    journal_rows = sum(len(result.rows) for _, result in parsed_journals)
    pl_months = sorted({result.year_month for _, result in parsed_pl if result.year_month})
    journal_months = sorted(
        {
            row.get("year_month")
            for _, result in parsed_journals
            for row in result.rows
            if row.get("year_month")
        }
    )

    print(f"Parsed P/L rows: {pl_entries:,} ({pl_months[0]} to {pl_months[-1]})")
    if journal_rows:
        print(
            f"Parsed journal rows: {journal_rows:,} "
            f"({journal_months[0]} to {journal_months[-1]})"
        )

    if args.dry_run:
        print("Dry run only. Nothing was written to the database.")
        return 0

    _validate_credentials()
    try:
        import libsql  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "libsql is not installed for this Python. "
            "Run: py -3.12 -m pip install \"libsql>=0.1.7\""
        ) from exc

    from lib import db

    if not getattr(db, "_use_cloud_db", lambda: False)():
        raise SystemExit("Cloud DB credentials were not detected. Aborting.")

    db.init_prime_schema()

    total_written = 0
    for path, result in parsed_pl:
        summary = db.replace_prime_pl_entries(
            result.year_month,
            result.entries,
            path.name,
            result.file_hash,
        )
        total_written += int(summary.get("entries") or 0)
        print(f"P/L imported: {result.year_month} {summary.get('entries', 0):,} rows")

    total_journal_inserted = 0
    for path, result in parsed_journals:
        _delete_prime_journal_file(result.file_hash)
        summary = db.insert_prime_journal_entries(
            result.rows,
            path.name,
            result.file_hash,
            result.date_range,
        )
        inserted = int(summary.get("inserted") or 0)
        total_journal_inserted += inserted
        print(
            f"Journal imported: {path.name} "
            f"{inserted:,} inserted / {summary.get('skipped', 0):,} skipped"
        )

    print("Done. Only PRIME tables were updated.")
    print(f"P/L rows written: {total_written:,}")
    print(f"Journal rows inserted: {total_journal_inserted:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
