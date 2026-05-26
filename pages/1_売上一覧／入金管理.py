"""売上一覧 / 入金管理
   CSV取込・売上一覧/入金管理・未入金一覧・売上確認を統合"""
import io
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd

from lib import db, csv_parser, styling, auth, notification


SELF_EXTRA_COLUMNS = {
    'self_refund_charge': 'INTEGER DEFAULT 0',
    'self_unpaid_charge': 'INTEGER DEFAULT 0',
    'self_rent_charge': 'INTEGER DEFAULT 0',
    'self_utilities_charge': 'INTEGER DEFAULT 0',
    'self_daily_supplies_charge': 'INTEGER DEFAULT 0',
    'self_breakfast_charge': 'INTEGER DEFAULT 0',
    'self_lunch_charge': 'INTEGER DEFAULT 0',
    'self_dinner_charge': 'INTEGER DEFAULT 0',
    'self_rice_charge': 'INTEGER DEFAULT 0',
    'self_special_benefit_charge': 'INTEGER DEFAULT 0',
    'self_housing_subsidy_charge': 'INTEGER DEFAULT 0',
}

REPORT_COLUMNS = {
    'self_report_to_supervisor': 'INTEGER DEFAULT 0',
    'self_reported_at': 'TEXT',
    'kokuho_report_to_supervisor': 'INTEGER DEFAULT 0',
    'kokuho_reported_at': 'TEXT',
}

SELF_FIELD_PARAMS = {
    'self_charge': 'charge',
    'self_snack_charge': 'snack_charge',
    'self_exam_charge': 'exam_charge',
    'self_other_charge': 'other_charge',
    'self_refund_charge': 'refund_charge',
    'self_unpaid_charge': 'unpaid_charge',
    'self_rent_charge': 'rent_charge',
    'self_utilities_charge': 'utilities_charge',
    'self_daily_supplies_charge': 'daily_supplies_charge',
    'self_breakfast_charge': 'breakfast_charge',
    'self_lunch_charge': 'lunch_charge',
    'self_dinner_charge': 'dinner_charge',
    'self_rice_charge': 'rice_charge',
    'self_special_benefit_charge': 'special_benefit_charge',
    'self_housing_subsidy_charge': 'housing_subsidy_charge',
}

NORMAL_SELF_ITEMS = [
    ('自己負担額', 'self_charge', 1),
    ('おやつ代', 'self_snack_charge', 1),
    ('検査代', 'self_exam_charge', 1),
    ('その他', 'self_other_charge', 1),
    ('返金', 'self_refund_charge', -1),
    ('未収金', 'self_unpaid_charge', 1),
]

SHARE_SELF_ITEMS = [
    ('サービス料', 'self_charge', 1),
    ('家賃', 'self_rent_charge', 1),
    ('水光熱費', 'self_utilities_charge', 1),
    ('日用品費', 'self_daily_supplies_charge', 1),
    ('朝食', 'self_breakfast_charge', 1),
    ('昼食', 'self_lunch_charge', 1),
    ('夕食', 'self_dinner_charge', 1),
    ('白米', 'self_rice_charge', 1),
    ('特別給付費', 'self_special_benefit_charge', -1),
    ('住宅補助', 'self_housing_subsidy_charge', -1),
    ('その他', 'self_other_charge', 1),
    ('返金', 'self_refund_charge', -1),
    ('未収金', 'self_unpaid_charge', 1),
]


def _row_value(row, key, default=None):
    try:
        if hasattr(row, 'keys') and key in row.keys():
            return row[key]
    except Exception:
        pass
    return default


def _ensure_sales_schema():
    """自己負担の手入力列を安全に用意する。既存データは変更しない。"""
    if hasattr(db, 'ensure_sales_schema'):
        try:
            db.ensure_sales_schema()
            return
        except Exception:
            pass

    with db.get_conn() as conn:
        try:
            record_cols = {
                r['name'] for r in conn.execute("PRAGMA table_info(monthly_records)").fetchall()
            }
        except Exception:
            return

        optional_columns = {
            'self_memo': 'TEXT',
            'kokuho_memo': 'TEXT',
            'self_snack_charge': 'INTEGER DEFAULT 0',
            'self_exam_charge': 'INTEGER DEFAULT 0',
            'self_other_charge': 'INTEGER DEFAULT 0',
            **SELF_EXTRA_COLUMNS,
            **REPORT_COLUMNS,
            'kokuho_addition_charge': 'INTEGER DEFAULT 0',
            'kokuho_adjustment_charge': 'INTEGER DEFAULT 0',
            'kokuho_other_charge': 'INTEGER DEFAULT 0',
        }
        for col, col_type in optional_columns.items():
            if col not in record_cols:
                conn.execute(f"ALTER TABLE monthly_records ADD COLUMN {col} {col_type}")


def _is_share_facility_label(label):
    return 'シェア' in str(label or '')


def _is_share_facility_record(record):
    return _is_share_facility_label(record.get('facility_name') if isinstance(record, dict) else '')


def _self_items(is_share_layout=False):
    return SHARE_SELF_ITEMS if is_share_layout else NORMAL_SELF_ITEMS


def _self_total_from_values(
    base=0, snack=0, exam=0, other=0, refund=0, unpaid=0,
    rent=0, utilities=0, daily_supplies=0, breakfast=0, lunch=0, dinner=0,
    rice=0, special_benefit=0, housing_subsidy=0,
):
    return (
        (base or 0)
        + (snack or 0)
        + (exam or 0)
        + (other or 0)
        + (rent or 0)
        + (utilities or 0)
        + (daily_supplies or 0)
        + (breakfast or 0)
        + (lunch or 0)
        + (dinner or 0)
        + (rice or 0)
        - (special_benefit or 0)
        - (housing_subsidy or 0)
        - (refund or 0)
        + (unpaid or 0)
    )


def _self_total_from_labels(row, items):
    total = 0
    for label, _field, sign in items:
        total += sign * _as_int(row.get(label, 0))
    return total


def _self_total_from_record_values(record):
    return _self_total_from_values(
        base=record.get('self_charge') or 0,
        snack=record.get('self_snack_charge') or 0,
        exam=record.get('self_exam_charge') or 0,
        other=record.get('self_other_charge') or 0,
        refund=record.get('self_refund_charge') or 0,
        unpaid=record.get('self_unpaid_charge') or 0,
        rent=record.get('self_rent_charge') or 0,
        utilities=record.get('self_utilities_charge') or 0,
        daily_supplies=record.get('self_daily_supplies_charge') or 0,
        breakfast=record.get('self_breakfast_charge') or 0,
        lunch=record.get('self_lunch_charge') or 0,
        dinner=record.get('self_dinner_charge') or 0,
        rice=record.get('self_rice_charge') or 0,
        special_benefit=record.get('self_special_benefit_charge') or 0,
        housing_subsidy=record.get('self_housing_subsidy_charge') or 0,
    )


def _charge_total(base=0, extra1=0, extra2=0, extra3=0):
    return (base or 0) + (extra1 or 0) + (extra2 or 0) + (extra3 or 0)


def _report_state_from_input(rec, kbn, memo, report_to_supervisor, now):
    memo_col = f'{kbn}_memo'
    flag_col = f'{kbn}_report_to_supervisor'
    reported_at_col = f'{kbn}_reported_at'
    effective_memo = _row_value(rec, memo_col, '') if memo is None else memo
    if report_to_supervisor is None:
        flag = 1 if _as_int(_row_value(rec, flag_col, 0)) else 0
        reported_at = _row_value(rec, reported_at_col, None)
    elif report_to_supervisor and str(effective_memo or '').strip():
        flag = 1
        reported_at = now
    else:
        flag = 0
        reported_at = None
    return effective_memo, flag, reported_at


def _update_sales_record(record_id, kbn, charge=None, paid_amount=None,
                         paid_date=None, method=None, memo=None,
                         snack_charge=None, exam_charge=None, other_charge=None,
                         refund_charge=None, unpaid_charge=None,
                         rent_charge=None, utilities_charge=None,
                         daily_supplies_charge=None, breakfast_charge=None,
                         lunch_charge=None, dinner_charge=None, rice_charge=None,
                         special_benefit_charge=None, housing_subsidy_charge=None,
                         addition_charge=None, adjustment_charge=None,
                         report_to_supervisor=None):
    """売上一覧ページ用の更新。古いdb.pyが公開中でも同じ処理で保存する。"""
    if kbn not in ('self', 'kokuho'):
        raise ValueError("区分は self または kokuho を指定してください。")

    with db.get_conn() as conn:
        rec = conn.execute(
            "SELECT * FROM monthly_records WHERE id = ?", (record_id,)
        ).fetchone()
        if not rec:
            raise ValueError(f"レコードが見つかりません: id={record_id}")

        if kbn == 'self':
            new_charge = charge if charge is not None else _row_value(rec, 'self_charge', 0)
            new_snack = (
                snack_charge if snack_charge is not None
                else _row_value(rec, 'self_snack_charge', 0)
            )
            new_exam = (
                exam_charge if exam_charge is not None
                else _row_value(rec, 'self_exam_charge', 0)
            )
            new_other = (
                other_charge if other_charge is not None
                else _row_value(rec, 'self_other_charge', 0)
            )
            new_refund = (
                refund_charge if refund_charge is not None
                else _row_value(rec, 'self_refund_charge', 0)
            )
            new_unpaid = (
                unpaid_charge if unpaid_charge is not None
                else _row_value(rec, 'self_unpaid_charge', 0)
            )
            new_rent = (
                rent_charge if rent_charge is not None
                else _row_value(rec, 'self_rent_charge', 0)
            )
            new_utilities = (
                utilities_charge if utilities_charge is not None
                else _row_value(rec, 'self_utilities_charge', 0)
            )
            new_daily_supplies = (
                daily_supplies_charge if daily_supplies_charge is not None
                else _row_value(rec, 'self_daily_supplies_charge', 0)
            )
            new_breakfast = (
                breakfast_charge if breakfast_charge is not None
                else _row_value(rec, 'self_breakfast_charge', 0)
            )
            new_lunch = (
                lunch_charge if lunch_charge is not None
                else _row_value(rec, 'self_lunch_charge', 0)
            )
            new_dinner = (
                dinner_charge if dinner_charge is not None
                else _row_value(rec, 'self_dinner_charge', 0)
            )
            new_rice = (
                rice_charge if rice_charge is not None
                else _row_value(rec, 'self_rice_charge', 0)
            )
            new_special_benefit = (
                special_benefit_charge if special_benefit_charge is not None
                else _row_value(rec, 'self_special_benefit_charge', 0)
            )
            new_housing_subsidy = (
                housing_subsidy_charge if housing_subsidy_charge is not None
                else _row_value(rec, 'self_housing_subsidy_charge', 0)
            )
            new_paid = (
                paid_amount if paid_amount is not None
                else _row_value(rec, 'self_paid_amount', 0)
            )
            status = _status_from_amounts(
                _self_total_from_values(
                    new_charge, new_snack, new_exam, new_other,
                    new_refund, new_unpaid, new_rent, new_utilities,
                    new_daily_supplies, new_breakfast, new_lunch, new_dinner,
                    new_rice, new_special_benefit, new_housing_subsidy,
                ),
                new_paid,
            )
            now = datetime.now().isoformat(sep=' ', timespec='seconds')
            new_memo, new_report, new_reported_at = _report_state_from_input(
                rec, 'self', memo, report_to_supervisor, now
            )
            conn.execute("""
                UPDATE monthly_records SET
                  self_charge = ?,
                  self_snack_charge = ?,
                  self_exam_charge = ?,
                  self_other_charge = ?,
                  self_refund_charge = ?,
                  self_unpaid_charge = ?,
                  self_rent_charge = ?,
                  self_utilities_charge = ?,
                  self_daily_supplies_charge = ?,
                  self_breakfast_charge = ?,
                  self_lunch_charge = ?,
                  self_dinner_charge = ?,
                  self_rice_charge = ?,
                  self_special_benefit_charge = ?,
                  self_housing_subsidy_charge = ?,
                  self_paid_amount = ?,
                  self_paid_date = ?,
                  self_payment_method = ?,
                  self_payment_status = ?,
                  self_memo = ?,
                  self_report_to_supervisor = ?,
                  self_reported_at = ?,
                  updated_at = ?
                WHERE id = ?
            """, (
                new_charge or 0, new_snack or 0, new_exam or 0, new_other or 0,
                new_refund or 0, new_unpaid or 0, new_rent or 0,
                new_utilities or 0, new_daily_supplies or 0, new_breakfast or 0,
                new_lunch or 0, new_dinner or 0, new_rice or 0,
                new_special_benefit or 0, new_housing_subsidy or 0,
                new_paid or 0, paid_date, method, status, new_memo,
                new_report, new_reported_at, now, record_id,
            ))
            return

        new_charge = charge if charge is not None else _row_value(rec, 'kokuho_charge', 0)
        new_addition = (
            addition_charge if addition_charge is not None
            else _row_value(rec, 'kokuho_addition_charge', 0)
        )
        new_adjustment = (
            adjustment_charge if adjustment_charge is not None
            else _row_value(rec, 'kokuho_adjustment_charge', 0)
        )
        new_other = (
            other_charge if other_charge is not None
            else _row_value(rec, 'kokuho_other_charge', 0)
        )
        new_paid = (
            paid_amount if paid_amount is not None
            else _row_value(rec, 'kokuho_paid_amount', 0)
        )
        status = _status_from_amounts(new_charge, new_paid)
        now = datetime.now().isoformat(sep=' ', timespec='seconds')
        new_memo, new_report, new_reported_at = _report_state_from_input(
            rec, 'kokuho', memo, report_to_supervisor, now
        )
        conn.execute("""
            UPDATE monthly_records SET
              kokuho_charge = ?,
              kokuho_addition_charge = ?,
              kokuho_adjustment_charge = ?,
              kokuho_other_charge = ?,
              kokuho_paid_amount = ?,
              kokuho_paid_date = ?,
              kokuho_payment_method = ?,
              kokuho_payment_status = ?,
              kokuho_memo = ?,
              kokuho_report_to_supervisor = ?,
              kokuho_reported_at = ?,
              updated_at = ?
            WHERE id = ?
        """, (
            new_charge or 0, new_addition or 0, new_adjustment or 0,
            new_other or 0, new_paid or 0, paid_date, method, status,
            new_memo, new_report, new_reported_at, now, record_id,
        ))


def _add_manual_self_record(service_ym, facility_id, cert_number, child_name,
                            self_charge=0, snack_charge=0, exam_charge=0,
                            other_charge=0, refund_charge=0, unpaid_charge=0,
                            rent_charge=0, utilities_charge=0, daily_supplies_charge=0,
                            breakfast_charge=0, lunch_charge=0, dinner_charge=0,
                            rice_charge=0, special_benefit_charge=0,
                            housing_subsidy_charge=0, paid_amount=0, method=None,
                            memo=None, report_to_supervisor=False):
    if not service_ym or not facility_id or not cert_number or not child_name:
        raise ValueError("サービス年月、施設、受給者証番号、利用者氏名は必須です。")

    base = int(self_charge or 0)
    snack = int(snack_charge or 0)
    exam = int(exam_charge or 0)
    other = int(other_charge or 0)
    refund = int(refund_charge or 0)
    unpaid = int(unpaid_charge or 0)
    rent = int(rent_charge or 0)
    utilities = int(utilities_charge or 0)
    daily_supplies = int(daily_supplies_charge or 0)
    breakfast = int(breakfast_charge or 0)
    lunch = int(lunch_charge or 0)
    dinner = int(dinner_charge or 0)
    rice = int(rice_charge or 0)
    special_benefit = int(special_benefit_charge or 0)
    housing_subsidy = int(housing_subsidy_charge or 0)
    paid = int(paid_amount or 0)
    paid_date = _today_jst().isoformat() if paid > 0 else None
    status = _status_from_amounts(
        _self_total_from_values(
            base, snack, exam, other, refund, unpaid, rent, utilities,
            daily_supplies, breakfast, lunch, dinner, rice, special_benefit,
            housing_subsidy,
        ),
        paid,
    )
    reported_at = datetime.now().isoformat(sep=' ', timespec='seconds') if (
        report_to_supervisor and str(memo or '').strip()
    ) else None
    report_flag = 1 if reported_at else 0

    with db.get_conn() as conn:
        existing = conn.execute("""
            SELECT id FROM monthly_records
            WHERE service_year_month = ? AND facility_id = ? AND cert_number = ?
        """, (service_ym, facility_id, cert_number.strip())).fetchone()
        if existing:
            raise ValueError(
                "同じサービス年月・施設・受給者証番号の行が既にあります。既存行を編集してください。"
            )

        conn.execute("""
            INSERT INTO monthly_records (
                service_year_month, facility_id, cert_number, child_name,
                self_charge, kokuho_charge,
                self_snack_charge, self_exam_charge, self_other_charge,
                self_refund_charge, self_unpaid_charge,
                self_rent_charge, self_utilities_charge, self_daily_supplies_charge,
                self_breakfast_charge, self_lunch_charge, self_dinner_charge,
                self_rice_charge, self_special_benefit_charge, self_housing_subsidy_charge,
                self_paid_amount, self_paid_date, self_payment_method,
                self_payment_status, self_memo,
                self_report_to_supervisor, self_reported_at
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            service_ym, facility_id, cert_number.strip(), child_name.strip(),
            base, snack, exam, other, refund, unpaid, rent, utilities,
            daily_supplies, breakfast, lunch, dinner, rice, special_benefit,
            housing_subsidy, paid, paid_date, method or None, status, memo,
            report_flag, reported_at,
        ))


def _add_manual_kokuho_record(service_ym, facility_id, cert_number, child_name,
                              kokuho_charge=0, addition_charge=0,
                              adjustment_charge=0, other_charge=0,
                              paid_amount=0, method=None, memo=None,
                              report_to_supervisor=False):
    if hasattr(db, 'add_manual_kokuho_record'):
        return db.add_manual_kokuho_record(
            service_ym=service_ym,
            facility_id=facility_id,
            cert_number=cert_number,
            child_name=child_name,
            kokuho_charge=kokuho_charge,
            addition_charge=addition_charge,
            adjustment_charge=adjustment_charge,
            other_charge=other_charge,
            paid_amount=paid_amount,
            method=method,
            memo=memo,
            report_to_supervisor=report_to_supervisor,
        )

    if not service_ym or not facility_id or not cert_number or not child_name:
        raise ValueError("サービス年月、施設、受給者証番号、利用者氏名は必須です。")

    base = int(kokuho_charge or 0)
    addition = int(addition_charge or 0)
    adjustment = int(adjustment_charge or 0)
    other = int(other_charge or 0)
    paid = int(paid_amount or 0)
    paid_date = _today_jst().isoformat() if paid > 0 else None
    status = _status_from_amounts(base, paid)
    reported_at = datetime.now().isoformat(sep=' ', timespec='seconds') if (
        report_to_supervisor and str(memo or '').strip()
    ) else None
    report_flag = 1 if reported_at else 0

    with db.get_conn() as conn:
        existing = conn.execute("""
            SELECT id FROM monthly_records
            WHERE service_year_month = ? AND facility_id = ? AND cert_number = ?
        """, (service_ym, facility_id, cert_number.strip())).fetchone()
        if existing:
            raise ValueError(
                "同じサービス年月・施設・受給者証番号の行が既にあります。既存行を編集してください。"
            )

        conn.execute("""
            INSERT INTO monthly_records (
                service_year_month, facility_id, cert_number, child_name,
                self_charge, kokuho_charge,
                kokuho_addition_charge, kokuho_adjustment_charge, kokuho_other_charge,
                kokuho_paid_amount, kokuho_paid_date, kokuho_payment_method,
                kokuho_payment_status, kokuho_memo,
                kokuho_report_to_supervisor, kokuho_reported_at
            )
            VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            service_ym, facility_id, cert_number.strip(), child_name.strip(),
            base, addition, adjustment, other, paid, paid_date, method or None, status, memo,
            report_flag, reported_at,
        ))


styling.inject_global_css()
auth.require_admin()
auth.render_sidebar_navigation()
_ensure_sales_schema()

st.title("売上一覧 / 入金管理")

year_months = db.list_year_months()
facilities = db.list_facilities()

_is_admin = auth.is_admin()
_top_titles = ["📥 CSV取込", "🔵 自己負担", "🟡 国保請求", "🔔 未入金確認"]
if _is_admin:
    _top_titles.append("📊 売上確認")
    _top_titles.append("📣 報告確認")
_top_tabs = st.tabs(_top_titles)


# ============================================================
# ① CSV取込タブ
# ============================================================
def render_csv_import():
    st.markdown("#### 手動アップロード")
    st.markdown("""
ここでは、国保連から出した給付費CSVを手動で取り込みます。

- 先に「どの施設のCSVか」を選びます。
- その施設の「自己負担」と「国保請求」として保存します。
- CSVは1施設につき1ファイルで取り込んでください。
- ファイル名が `SK` で始まるCSVを想定しています。
- Drive連携は使わず、この画面で選んだCSVだけを取り込みます。
""")

    facility_options = {
        f"{f['short_code']}: {f['facility_name']}": f
        for f in facilities
    }
    if not facility_options:
        st.error("施設マスタがありません。先に施設マスタを登録してください。")
        return

    selected_facility_label = st.selectbox(
        "取り込む施設",
        list(facility_options.keys()),
        key='csv_import_facility',
        help="このCSVをどの施設の売上として保存するかを選んでください。",
    )
    selected_facility = facility_options[selected_facility_label]
    facility_id = selected_facility['id']

    st.info(
        f"このCSVは **{selected_facility_label}** の "
        "**自己負担・国保請求** として取り込みます。"
    )

    uploaded = st.file_uploader("CSVファイル", type=['csv'], key='csv_uploader')

    if uploaded is None:
        st.info("CSVファイルをドラッグ＆ドロップ、またはクリックで選択してください")
        return

    data = uploaded.getvalue()
    parsed = csv_parser.parse_csv_bytes(data)

    if 'error' in parsed:
        st.error(f"解析エラー: {parsed['error']}")
        return

    if not parsed['records']:
        st.error("K122-01 / J121-01 のレコードが見つかりませんでした。CSVの形式を確認してください。")
        return

    st.success(f"**{parsed['row_count']}名分** のレコードを検出しました")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("サービス提供年月", parsed['service_year_month'] or "-")
    with col2:
        st.metric("請求年月", parsed['billing_year_month'] or "-")
    with col3:
        st.metric("件数", parsed['row_count'])

    codes = parsed['csv_facility_codes']
    if len(codes) == 0:
        st.error("事業所番号が検出できませんでした")
        return
    if len(codes) > 1:
        st.warning(f"複数の事業所番号 {codes} が含まれています。最初のコードのみ使用します。")
    csv_code = codes[0]

    mapped_facility = db.get_facility_by_csv_code(csv_code)
    if mapped_facility and mapped_facility['id'] != facility_id:
        st.warning(
            f"CSV内の事業所コード `{csv_code}` は、施設マスタでは "
            f"**{mapped_facility['short_code']}: {mapped_facility['facility_name']}** に紐付いています。  \n"
            f"いま選んでいる施設は **{selected_facility_label}** です。"
        )
    elif mapped_facility:
        st.success(
            f"CSV内の事業所コード `{csv_code}` は、選択中の施設と一致しています。"
        )
    else:
        st.warning(
            f"CSV内の事業所コード `{csv_code}` は、まだ施設マスタに紐付いていません。"
        )
        if st.button(
            f"このCSVコードを {selected_facility_label} に紐付ける",
            type='secondary',
            key='csv_link_btn',
        ):
            db.update_facility_csv_code(facility_id, csv_code)
            st.success("施設マスタへ紐付けました。このまま保存できます。")

    st.markdown(f"### プレビュー（{selected_facility_label} の自己負担・国保請求）")
    df_preview = pd.DataFrame([
        {
            '受給者証番号': r['cert_number'],
            '利用者氏名': r['child_name'],
            '保護者氏名': r['guardian_name'],
            '上限月額': r['fee_limit'],
            '総費用額': r['total_cost'],
            '自己負担(請求額)': r['self_charge'],
            '国保請求(請求額)': r['kokuho_charge'],
        }
        for r in parsed['records']
    ])
    st.dataframe(df_preview, width='stretch', height=400)

    c1, c2, c3 = st.columns(3)
    c1.metric("自己負担合計", f"{df_preview['自己負担(請求額)'].sum():,} 円")
    c2.metric("国保請求合計", f"{df_preview['国保請求(請求額)'].sum():,} 円")
    c3.metric("総費用額合計", f"{df_preview['総費用額'].sum():,} 円")

    existing_records = db.get_existing_records(facility_id, parsed['service_year_month'])
    if existing_records:
        st.warning(
            f"⚠ 既に **{parsed['service_year_month']}** 月の "
            f"**{selected_facility['facility_name']}** のデータが **{len(existing_records)}件** 登録されています。\n\n"
            f"上書き保存すると、請求額・氏名等は新CSVで更新されます。\n"
            f"**入金情報（入金日・入金額・回収方法・メモ）は保持されます。**"
        )
        confirm_label = "上書き保存する"
    else:
        confirm_label = "保存する"

    if parsed['errors']:
        with st.expander(f"解析時の警告 ({len(parsed['errors'])}件)"):
            for err in parsed['errors']:
                st.text(err)

    if st.button(confirm_label, type='primary', key='csv_save_btn'):
        import_id = db.create_import(
            facility_id=facility_id,
            service_ym=parsed['service_year_month'],
            billing_ym=parsed['billing_year_month'],
            filename=uploaded.name,
            file_hash=parsed['file_hash'],
            row_count=parsed['row_count'],
        )

        inserted = 0
        updated = 0
        progress = st.progress(0, text="保存中...")
        for i, rec in enumerate(parsed['records']):
            rec_with_facility = dict(rec)
            rec_with_facility['facility_id'] = facility_id
            result = db.upsert_monthly_record(rec_with_facility, import_id)
            if result == 'inserted':
                inserted += 1
            else:
                updated += 1
            progress.progress((i + 1) / len(parsed['records']),
                              text=f"保存中... {i+1}/{len(parsed['records'])}")
        progress.empty()

        st.success(f"保存完了: 新規 {inserted} 件、更新 {updated} 件")
        st.balloons()
        st.markdown("「**🔵 自己負担**」「**🟡 国保請求**」タブで確認できます。")


# ============================================================
# ② 売上一覧 / 入金管理タブ
# ============================================================
def _as_int(value):
    if pd.isna(value):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _self_total_from_record(r):
    return _self_total_from_record_values(r)


def _kokuho_total_from_record(r):
    return r.get('kokuho_charge') or 0


def _status_from_amounts(charge, paid):
    charge = _as_int(charge)
    paid = _as_int(paid)
    if charge <= 0 and paid <= 0:
        return '対象外'
    if paid <= 0:
        return '未入金'
    if paid < charge:
        return '一部入金'
    if paid > charge:
        return '過入金'
    return '入金済'


def _today_jst():
    return datetime.now(ZoneInfo("Asia/Tokyo")).date()


def _auto_paid_date(orig_paid, new_paid, current_date):
    orig_paid = _as_int(orig_paid)
    new_paid = _as_int(new_paid)
    if new_paid <= 0:
        return None
    if new_paid != orig_paid or current_date is None:
        return _today_jst()
    return current_date


def _is_blank_date(value):
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def _apply_paid_date_for_display(df, paid_col, date_col):
    if df is None or df.empty or paid_col not in df.columns or date_col not in df.columns:
        return df

    today = _today_jst()

    def display_date(row):
        paid = _as_int(row[paid_col])
        current_date = row.get(date_col)
        if paid <= 0:
            return None
        if _is_blank_date(current_date):
            return today
        return current_date

    df[date_col] = df.apply(display_date, axis=1)
    return df


def _recompute_self_invoice_totals(df, is_share_layout=False):
    """自己負担の入力項目から、画面表示用DataFrameへ合計を即時反映する。"""
    if df is None or df.empty:
        return df

    df = df.copy()
    item_labels = [label for label, _field, _sign in _self_items(is_share_layout)]
    for col in item_labels + ['回収額']:
        if col not in df.columns:
            df[col] = 0
        df[col] = df[col].fillna(0).map(_as_int)

    df['合計請求額'] = df.apply(
        lambda row: _self_total_from_labels(row, _self_items(is_share_layout)),
        axis=1,
    )
    df['差'] = df['合計請求額'] - df['回収額']
    df['ステータス'] = df.apply(
        lambda row: _status_from_amounts(row['合計請求額'], row['回収額']),
        axis=1,
    )
    df = _apply_paid_date_for_display(df, '回収額', '入金日')
    return df


def _apply_editor_state_to_self_df(df, editor_key, is_share_layout=False):
    """data_editorの未保存編集を反映してから合計請求額を再計算する。"""
    df = df.copy()
    editor_state = st.session_state.get(editor_key)
    if isinstance(editor_state, dict):
        edited_rows = editor_state.get('edited_rows') or {}
        for row_idx, changes in edited_rows.items():
            try:
                row_idx = int(row_idx)
            except (TypeError, ValueError):
                continue
            if row_idx < 0 or row_idx >= len(df) or not isinstance(changes, dict):
                continue
            paid_changed = False
            for col, value in changes.items():
                if col in df.columns:
                    df.at[row_idx, col] = value
                    if col == '回収額':
                        paid_changed = True
            if paid_changed and '入金日' in df.columns:
                df.at[row_idx, '入金日'] = _today_jst() if _as_int(df.at[row_idx, '回収額']) > 0 else None
    return _recompute_self_invoice_totals(df, is_share_layout)


def _recompute_kokuho_invoice_totals(df):
    """国保請求額と回収額から、画面表示用の差額・ステータスを即時反映する。"""
    if df is None or df.empty:
        return df

    df = df.copy()
    for col in ['国保請求額', '回収額']:
        if col not in df.columns:
            df[col] = 0
        df[col] = df[col].fillna(0).map(_as_int)

    df['差'] = df['国保請求額'] - df['回収額']
    df['ステータス'] = df.apply(
        lambda row: _status_from_amounts(row['国保請求額'], row['回収額']),
        axis=1,
    )
    df = _apply_paid_date_for_display(df, '回収額', '記載日')
    return df


def _apply_editor_state_to_kokuho_df(df, editor_key):
    df = df.copy()
    editor_state = st.session_state.get(editor_key)
    if isinstance(editor_state, dict):
        edited_rows = editor_state.get('edited_rows') or {}
        for row_idx, changes in edited_rows.items():
            try:
                row_idx = int(row_idx)
            except (TypeError, ValueError):
                continue
            if row_idx < 0 or row_idx >= len(df) or not isinstance(changes, dict):
                continue
            paid_changed = False
            for col, value in changes.items():
                if col in df.columns:
                    df.at[row_idx, col] = value
                    if col == '回収額':
                        paid_changed = True
            if paid_changed and '記載日' in df.columns:
                df.at[row_idx, '記載日'] = _today_jst() if _as_int(df.at[row_idx, '回収額']) > 0 else None
    return _recompute_kokuho_invoice_totals(df)


def _build_split_df(records_list, show_diff_only=False):
    rows_self = []
    rows_kokuho = []
    for r in records_list:
        base = {
            'id': r['id'],
            '一括対象': False,
            '利用者氏名': r['child_name'] or '',
            '受給者証番号': r['cert_number'],
            '施設': r['facility_name'],
        }
        s_charge = r['self_charge'] or 0
        snack_charge = r.get('self_snack_charge') or 0
        exam_charge = r.get('self_exam_charge') or 0
        other_charge = r.get('self_other_charge') or 0
        refund_charge = r.get('self_refund_charge') or 0
        unpaid_charge = r.get('self_unpaid_charge') or 0
        rent_charge = r.get('self_rent_charge') or 0
        utilities_charge = r.get('self_utilities_charge') or 0
        daily_supplies_charge = r.get('self_daily_supplies_charge') or 0
        breakfast_charge = r.get('self_breakfast_charge') or 0
        lunch_charge = r.get('self_lunch_charge') or 0
        dinner_charge = r.get('self_dinner_charge') or 0
        rice_charge = r.get('self_rice_charge') or 0
        special_benefit_charge = r.get('self_special_benefit_charge') or 0
        housing_subsidy_charge = r.get('self_housing_subsidy_charge') or 0
        s_total = _self_total_from_record_values(r)
        s_paid = r['self_paid_amount'] or 0
        s_status = _status_from_amounts(s_total, s_paid)
        rows_self.append({
            **base,
            '自己負担額': s_charge,
            'サービス料': s_charge,
            'おやつ代': snack_charge,
            '検査代': exam_charge,
            'その他': other_charge,
            '返金': refund_charge,
            '未収金': unpaid_charge,
            '家賃': rent_charge,
            '水光熱費': utilities_charge,
            '日用品費': daily_supplies_charge,
            '朝食': breakfast_charge,
            '昼食': lunch_charge,
            '夕食': dinner_charge,
            '白米': rice_charge,
            '特別給付費': special_benefit_charge,
            '住宅補助': housing_subsidy_charge,
            '合計請求額': s_total,
            '回収額': s_paid,
            '差': s_total - s_paid if (s_total > 0 or s_paid > 0) else 0,
            '回収方法': r['self_payment_method'] or '',
            '入金日': pd.to_datetime(r['self_paid_date']).date() if r['self_paid_date'] else None,
            'ステータス': s_status,
            '備考': r.get('self_memo') or '',
            '上司報告': bool(_as_int(r.get('self_report_to_supervisor') or 0)),
            '報告更新日': r.get('self_reported_at') or '',
        })
        k_charge = r['kokuho_charge'] or 0
        k_paid = r['kokuho_paid_amount'] or 0
        k_status = _status_from_amounts(k_charge, k_paid)
        rows_kokuho.append({
            **base,
            '国保請求額': k_charge,
            '回収額': k_paid,
            '差': k_charge - k_paid if (k_charge > 0 or k_paid > 0) else 0,
            '記載日': pd.to_datetime(r['kokuho_paid_date']).date() if r['kokuho_paid_date'] else None,
            'ステータス': k_status,
            '備考': r.get('kokuho_memo') or '',
            '上司報告': bool(_as_int(r.get('kokuho_report_to_supervisor') or 0)),
            '報告更新日': r.get('kokuho_reported_at') or '',
        })
    self_df = pd.DataFrame(rows_self)
    kokuho_df = pd.DataFrame(rows_kokuho)
    if show_diff_only:
        self_df = self_df[self_df['差'] != 0].reset_index(drop=True)
        kokuho_df = kokuho_df[kokuho_df['差'] != 0].reset_index(drop=True)
    return self_df, kokuho_df


def _empty_payment_df(kbn):
    base_columns = ['id', '一括対象', '利用者氏名', '受給者証番号', '施設']
    if kbn == 'self':
        item_columns = [
            '自己負担額', 'サービス料', 'おやつ代', '検査代', 'その他', '返金', '未収金',
            '家賃', '水光熱費', '日用品費', '朝食', '昼食', '夕食', '白米',
            '特別給付費', '住宅補助',
        ]
        return pd.DataFrame(columns=[
            *base_columns,
            *item_columns,
            '合計請求額', '回収額', '差', '回収方法', '入金日', 'ステータス', '備考',
            '上司報告', '報告更新日',
        ])
    return pd.DataFrame(columns=[
        *base_columns,
        '国保請求額', '回収額', '差', '記載日', 'ステータス', '備考',
        '上司報告', '報告更新日',
    ])


def _detect_changes(edited, original, kbn_key, is_share_layout=False):
    changes = []
    for i in range(len(edited)):
        if i >= len(original):
            continue
        orig = original.iloc[i]
        new = edited.iloc[i]
        if kbn_key == 'self':
            items = _self_items(is_share_layout)
            charge_col = 'サービス料' if is_share_layout else '自己負担額'
            extra_cols = [label for label, _field, _sign in items if label != charge_col]
            date_col = '入金日'
            old_total = _as_int(orig['合計請求額'])
            new_total = _self_total_from_labels(new, items)
            extra_changed = any(_as_int(orig[c]) != _as_int(new[c]) for c in extra_cols)
            method_changed = (orig['回収方法'] or '') != (new['回収方法'] or '')
            new_method = new['回収方法']
        else:
            charge_col = '国保請求額' if '国保請求額' in new.index else '請求額'
            date_col = '記載日' if '記載日' in orig.index else '入金日'
            old_total = _as_int(orig[charge_col])
            new_total = _as_int(new[charge_col])
            extra_changed = False
            method_changed = False
            new_method = None
        old_paid = _as_int(orig['回収額'])
        new_paid = _as_int(new['回収額'])
        charge_changed = _as_int(orig[charge_col]) != _as_int(new[charge_col])
        paid_changed = old_paid != new_paid
        auto_paid_date = _auto_paid_date(old_paid, new_paid, orig[date_col])
        date_changed = orig[date_col] != auto_paid_date
        memo_changed = (orig.get('備考') or '') != (new.get('備考') or '')
        if charge_changed or extra_changed or paid_changed or method_changed or date_changed or memo_changed:
            changes.append({
                'id': int(orig['id']),
                'name': orig['利用者氏名'],
                'kbn': kbn_key,
                'kbn_label': '自己負担' if kbn_key == 'self' else '国保請求',
                'old_charge': _as_int(orig[charge_col]),
                'new_charge': _as_int(new[charge_col]),
                'old_total_charge': old_total,
                'new_total_charge': new_total,
                'old_paid': old_paid,
                'new_paid': new_paid,
                'paid_date': auto_paid_date,
                'method': new_method,
                'self_values': {
                    SELF_FIELD_PARAMS[field]: _as_int(new[label])
                    for label, field, _sign in _self_items(is_share_layout)
                    if kbn_key == 'self'
                },
                'addition_charge': None,
                'adjustment_charge': None,
                'old_memo': orig.get('備考') or '',
                'new_memo': new.get('備考') or '',
                'memo_changed': memo_changed,
                'old_report_to_supervisor': bool(orig.get('上司報告', False)),
                'amount_changed': charge_changed or extra_changed or paid_changed,
                'risky': (charge_changed or extra_changed) and old_paid > 0,
            })
    return changes


def _render_report_prompt(changes, kbn, selected_ym, selected_facility_id):
    memo_changes = [c for c in changes if c.get('memo_changed')]
    if not memo_changes:
        for c in changes:
            c['report_to_supervisor'] = None
        return

    st.markdown("#### 備考の報告確認")
    st.info("備考欄を変更した行があります。上司に報告しますか？ 報告する行だけ「はい、上司に報告します」にチェックしてください。")
    for c in changes:
        if not c.get('memo_changed'):
            c['report_to_supervisor'] = None
            continue

        memo_text = str(c.get('new_memo') or '').strip()
        if not memo_text:
            c['report_to_supervisor'] = False
            st.caption(f"{c['kbn_label']} / {c['name']}：備考が空になったため、報告確認から外します。")
            continue

        default_report = bool(c.get('old_report_to_supervisor'))
        st.markdown(
            f"**{c['kbn_label']} / {c['name']}**  \n"
            f"{memo_text}"
        )
        c['report_to_supervisor'] = st.checkbox(
            "はい、上司に報告します",
            value=default_report,
            key=f"report_confirm_{kbn}_{selected_ym}_{selected_facility_id}_{c['id']}",
        )


def render_uriage_main():
    if not year_months:
        st.info("まだデータがありません。「**📥 CSV取込**」タブから取込んでください。")
        return

    st.markdown(
        "<div style='background:#fef9c3; border-left:5px solid #facc15; "
        "padding:10px 16px; border-radius:6px; margin-bottom:12px; font-size:13px;'>"
        "✏️ <b>編集可能セル</b>：<b>自己負担額 / おやつ代 / 検査代 / その他 / 回収額 / 回収方法 / 備考</b>　"
        "（セルをダブルクリック）<br>"
        "💡 回収額を入力して保存すると、入金日は保存した日で自動反映します。"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.container():
        st.markdown("#### 絞り込み")
        col1, col2 = st.columns(2)
        with col1:
            selected_ym = st.selectbox("サービス提供年月", year_months,
                                        index=0, key='uriage_ym')
        with col2:
            facility_options = {"（すべて）": None}
            for f in facilities:
                if f['csv_facility_code']:
                    facility_options[f"{f['short_code']}: {f['facility_name']}"] = f['id']
            selected_facility_label = st.selectbox(
                "施設", list(facility_options.keys()), key='uriage_fac',
            )
            selected_facility_id = facility_options[selected_facility_label]

    records = db.list_records(service_ym=selected_ym, facility_id=selected_facility_id)
    if not records:
        st.warning("条件に合うレコードがありません")
        return

    self_charge_sum = sum(_self_total_from_record(r) for r in records)
    self_paid_sum = sum(r['self_paid_amount'] or 0 for r in records)
    kokuho_charge_sum = sum(r['kokuho_charge'] or 0 for r in records)
    kokuho_paid_sum = sum(r['kokuho_paid_amount'] or 0 for r in records)

    st.markdown("#### サマリ")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("対象利用者", f"{len(records)} 名")
    c2.metric("自己負担 合計請求", f"{self_charge_sum:,} 円")
    c3.metric("自己負担 入金", f"{self_paid_sum:,} 円",
              delta=f"差 {self_charge_sum - self_paid_sum:,}", delta_color="inverse")
    c4.metric("国保請求 請求", f"{kokuho_charge_sum:,} 円")
    c5.metric("国保請求 入金", f"{kokuho_paid_sum:,} 円",
              delta=f"差 {kokuho_charge_sum - kokuho_paid_sum:,}", delta_color="inverse")

    st.markdown("---")

    _tab_titles = ["📋 一覧 / 個別編集", "💳 一括入金登録"]
    if _is_admin:
        _tab_titles.append("🗑 一括削除（管理者・許可制）")
    _tabs = st.tabs(_tab_titles)
    tab_view = _tabs[0]
    tab_bulk = _tabs[1]
    tab_delete = _tabs[2] if _is_admin else None

    # ---- 一覧 / 個別編集 ----
    with tab_view:
        show_diff_only = st.checkbox("差額ありのみ表示", value=False, key='diff_filter')
        self_df, kokuho_df = _build_split_df(records, show_diff_only=show_diff_only)

        state_key_self = f"orig_self_{selected_ym}_{selected_facility_id}_{show_diff_only}"
        state_key_kokuho = f"orig_kokuho_{selected_ym}_{selected_facility_id}_{show_diff_only}"
        if state_key_self not in st.session_state:
            st.session_state[state_key_self] = self_df.copy()
        if state_key_kokuho not in st.session_state:
            st.session_state[state_key_kokuho] = kokuho_df.copy()
        original_self = st.session_state[state_key_self]
        original_kokuho = st.session_state[state_key_kokuho]
        self_editor_key = f"editor_self_{selected_ym}_{selected_facility_id}_{show_diff_only}"
        self_display_df = _apply_editor_state_to_self_df(self_df, self_editor_key)

        base_columns = {
            '一括対象': st.column_config.CheckboxColumn(
                '一括対象',
                help='チェックした自己負担行は、この一覧内の一括入金登録で処理できます。',
                width='small',
            ),
            'id': st.column_config.NumberColumn('id', disabled=True, width='small'),
            '利用者氏名': st.column_config.TextColumn('利用者氏名', disabled=True),
            '受給者証番号': st.column_config.TextColumn('受給者証番号', disabled=True),
            '施設': st.column_config.TextColumn('施設', disabled=True),
            '回収額': st.column_config.NumberColumn(
                '回収額', format='localized', step=1, min_value=0,
                help='手入力で入金額を調整',
            ),
            '差': st.column_config.NumberColumn(
                '差', format='localized', disabled=True,
                help='合計請求額 − 回収額（0円同士は対象外）',
            ),
            '入金日': st.column_config.DateColumn(
                '入金日', format='YYYY-MM-DD', disabled=True,
                help='回収額を保存した日が自動で入ります。',
            ),
            'ステータス': st.column_config.TextColumn('ステータス', disabled=True, width='small'),
            '備考': st.column_config.TextColumn(
                '備考', width='medium',
                help='変更理由など。記入すると保存時にメール通知の本文に含まれます。',
            ),
        }

        st.markdown(
            "<div style='background:#dbeafe; padding:10px 16px; border-radius:6px; "
            "border-left:5px solid #2563eb; margin-top:8px; margin-bottom:8px;'>"
            "<span style='color:#1e3a8a; font-weight:700; font-size:18px;'>"
            "自己負担</span>"
            "<span style='color:#475569; font-size:13px; margin-left:12px;'>"
            f"{len(self_display_df)}行 / 合計請求 {self_display_df['合計請求額'].sum():,}円 / 回収 {self_display_df['回収額'].sum():,}円"
            "</span></div>",
            unsafe_allow_html=True,
        )
        self_columns = {
            **base_columns,
            '自己負担額': st.column_config.NumberColumn(
                '自己負担額', format='localized', step=1, min_value=0,
                help='CSVから取り込んだ自己負担額。修正する場合は保存前の許可確認が出ます。',
            ),
            'おやつ代': st.column_config.NumberColumn(
                'おやつ代', format='localized', step=1, min_value=0,
                help='手入力の追加請求額',
            ),
            '検査代': st.column_config.NumberColumn(
                '検査代', format='localized', step=1, min_value=0,
                help='手入力の追加請求額',
            ),
            'その他': st.column_config.NumberColumn(
                'その他', format='localized', step=1, min_value=0,
                help='手入力の追加請求額',
            ),
            '合計請求額': st.column_config.NumberColumn(
                '合計請求額', format='localized', disabled=True,
                help='自己負担額 + おやつ代 + 検査代 + その他',
            ),
            '回収方法': st.column_config.SelectboxColumn(
                '回収方法',
                options=[''] + db.PAYMENT_METHODS_SELF,
                required=False, width='small',
                help='自己負担: SMBC / 振込 / 現金 / その他',
            ),
        }
        edited_self = st.data_editor(
            styling.style_editor_df(self_display_df),
            column_config=self_columns,
            hide_index=True,
            width='stretch',
            height=320,
            key=self_editor_key,
        )
        edited_self = _recompute_self_invoice_totals(edited_self)

        with st.expander("自己負担の一括入金登録（この一覧から）", expanded=False):
            selected_bulk = edited_self[edited_self['一括対象'] == True].copy()
            b1, b2, b3 = st.columns([2, 2, 3])
            with b1:
                inline_method = st.selectbox(
                    "回収方法",
                    db.PAYMENT_METHODS_SELF,
                    key=f"inline_self_method_{selected_ym}_{selected_facility_id}",
                )
            with b2:
                st.metric("チェック中", f"{len(selected_bulk)} 行")
            with b3:
                st.caption(
                    "チェックした行を、合計請求額どおりの回収額として保存します。"
                    "入金日は今日の日付になります。"
                )
            if st.button(
                "チェックした自己負担を一括で回収済みにする",
                type='primary',
                disabled=selected_bulk.empty,
                width='stretch',
                key=f"inline_self_bulk_btn_{selected_ym}_{selected_facility_id}",
            ):
                success = 0
                errors = []
                for _, row in selected_bulk.iterrows():
                    try:
                        _update_sales_record(
                            record_id=int(row['id']),
                            kbn='self',
                            charge=_as_int(row['自己負担額']),
                            snack_charge=_as_int(row['おやつ代']),
                            exam_charge=_as_int(row['検査代']),
                            other_charge=_as_int(row['その他']),
                            paid_amount=_as_int(row['合計請求額']),
                            paid_date=_today_jst().isoformat(),
                            method=inline_method,
                            memo=row.get('備考') or None,
                        )
                        success += 1
                    except Exception as e:
                        errors.append(f"id={row.get('id')}: {e}")
                if success:
                    st.success(f"自己負担の一括入金を保存しました: {success}行")
                    st.balloons()
                if errors:
                    st.error("エラー:\n" + "\n".join(errors))
                st.session_state.pop(state_key_self, None)
                st.rerun()

        with st.expander("自己負担のみの行を追加", expanded=False):
            st.caption("CSVに出てこない自己負担のみの利用者を、選択中のサービス年月に1行追加します。")
            manual_facility_options = {
                f"{f['short_code']}: {f['facility_name']}": f['id']
                for f in facilities
            }
            with st.form(f"manual_self_add_{selected_ym}_{selected_facility_id}"):
                m_cols1 = st.columns(2)
                with m_cols1[0]:
                    if selected_facility_id is None:
                        manual_facility_label = st.selectbox(
                            "施設",
                            list(manual_facility_options.keys()),
                            key=f"manual_self_fac_{selected_ym}",
                        )
                        manual_facility_id = manual_facility_options.get(manual_facility_label)
                    else:
                        st.text_input("施設", value=selected_facility_label, disabled=True)
                        manual_facility_id = selected_facility_id
                with m_cols1[1]:
                    manual_cert = st.text_input("受給者証番号")

                m_cols2 = st.columns(2)
                with m_cols2[0]:
                    manual_name = st.text_input("利用者氏名")
                with m_cols2[1]:
                    manual_method = st.selectbox("回収方法", [''] + db.PAYMENT_METHODS_SELF)

                m_cols3 = st.columns(5)
                with m_cols3[0]:
                    manual_self_charge = st.number_input("自己負担額", min_value=0, step=1)
                with m_cols3[1]:
                    manual_snack = st.number_input("おやつ代", min_value=0, step=1)
                with m_cols3[2]:
                    manual_exam = st.number_input("検査代", min_value=0, step=1)
                with m_cols3[3]:
                    manual_other = st.number_input("その他", min_value=0, step=1)
                with m_cols3[4]:
                    manual_paid = st.number_input("回収額", min_value=0, step=1)

                manual_memo = st.text_input("備考")
                submitted = st.form_submit_button("自己負担行を追加", type='primary')
                if submitted:
                    try:
                        _add_manual_self_record(
                            service_ym=selected_ym,
                            facility_id=manual_facility_id,
                            cert_number=manual_cert,
                            child_name=manual_name,
                            self_charge=manual_self_charge,
                            snack_charge=manual_snack,
                            exam_charge=manual_exam,
                            other_charge=manual_other,
                            paid_amount=manual_paid,
                            method=manual_method or None,
                            memo=manual_memo or None,
                        )
                        st.success("自己負担のみの行を追加しました。")
                        st.session_state.pop(state_key_self, None)
                        st.rerun()
                    except Exception as e:
                        st.error(f"追加できませんでした: {e}")

        st.markdown(
            "<div style='background:#fef3c7; padding:10px 16px; border-radius:6px; "
            "border-left:5px solid #d97706; margin-top:20px; margin-bottom:8px;'>"
            "<span style='color:#78350f; font-weight:700; font-size:18px;'>"
            "国保請求</span>"
            "<span style='color:#475569; font-size:13px; margin-left:12px;'>"
            f"{len(kokuho_df)}行 / 請求 {kokuho_df['請求額'].sum():,}円 / 回収 {kokuho_df['回収額'].sum():,}円"
            "</span></div>",
            unsafe_allow_html=True,
        )
        kokuho_columns = {
            **{k: v for k, v in base_columns.items() if k != '一括対象'},
            '請求額': st.column_config.NumberColumn(
                '請求額', format='localized', step=1, min_value=0,
                help='CSV取込後でも上書き編集可能',
            ),
            '回収方法': st.column_config.SelectboxColumn(
                '回収方法',
                options=[''] + db.PAYMENT_METHODS_KOKUHO,
                required=False, width='small',
                help='国保請求: 国保 / 自己',
            ),
        }
        edited_kokuho = st.data_editor(
            styling.style_editor_df(kokuho_df),
            column_config=kokuho_columns,
            hide_index=True,
            width='stretch',
            height=320,
            key=f"editor_kokuho_{selected_ym}_{selected_facility_id}_{show_diff_only}",
        )
        edited_kokuho['差'] = (edited_kokuho['請求額'].fillna(0).astype(int)
                               - edited_kokuho['回収額'].fillna(0).astype(int))

        changes = (
            _detect_changes(edited_self, original_self, 'self')
            + _detect_changes(edited_kokuho, original_kokuho, 'kokuho')
        )
        risky_changes = [c for c in changes if c['risky']]

        st.markdown("---")

        col_save, col_info = st.columns([1, 5])
        if changes:
            with col_info:
                st.info(f"未保存の編集: **{len(changes)}行**")
        else:
            with col_info:
                st.caption("（編集なし）")

        supervisor_ok = True
        if risky_changes:
            st.error(
                "⚠ **入金登録後の請求額変更が含まれています** — "
                "保存前に上司の許可が必要です。"
            )
            with st.expander(f"対象 {len(risky_changes)}件を確認", expanded=True):
                for c in risky_changes:
                    st.markdown(
                        f"- id={c['id']}　**{c['name']}**　{c['kbn_label']}　"
                        f"請求 **{c['old_total_charge']:,}円 → {c['new_total_charge']:,}円**"
                        f"（既に {c['old_paid']:,}円 入金済）"
                    )
            supervisor_ok = st.checkbox(
                "上司の許可を得たので、保存する",
                value=False,
                key='supervisor_approval',
            )

        save_disabled = (not changes) or (bool(risky_changes) and not supervisor_ok)

        with col_save:
            save_clicked = st.button(
                "保存", type='primary',
                width='stretch',
                disabled=save_disabled,
                help=("変更がありません" if not changes
                      else "上司許可チェックが必要です" if save_disabled
                      else None),
            )

        if save_clicked:
            success = 0
            errors = []
            for c in changes:
                try:
                    paid_date_str = c['paid_date'].isoformat() if c['paid_date'] else None
                    method = c['method'] if c['method'] else None
                    memo_to_save = c['new_memo'] if c['memo_changed'] else None
                    _update_sales_record(
                        record_id=c['id'],
                        kbn=c['kbn'],
                        charge=c['new_charge'],
                        paid_amount=c['new_paid'],
                        paid_date=paid_date_str,
                        method=method,
                        memo=memo_to_save,
                        snack_charge=c.get('snack_charge'),
                        exam_charge=c.get('exam_charge'),
                        other_charge=c.get('other_charge'),
                    )
                    success += 1
                except Exception as e:
                    errors.append(f"id={c['id']} {c['kbn_label']}: {e}")
            if success:
                st.success(f"保存しました: {success}行")
                st.balloons()
            if errors:
                st.error("エラー:\n" + "\n".join(errors))

            notify_targets = [c for c in changes
                              if c['amount_changed'] or (c['memo_changed'] and c['new_memo'])]
            if notify_targets:
                st.session_state['_pending_notification'] = notify_targets

            st.session_state.pop(state_key_self, None)
            st.session_state.pop(state_key_kokuho, None)
            st.session_state.pop('supervisor_approval', None)
            st.rerun()

        pending = st.session_state.get('_pending_notification')
        if pending:
            st.markdown("---")
            current = auth.current_user()
            recipient = current['email']
            email_data = notification.build_email(recipient, pending, current.get('name'))

            st.markdown(
                "<div style='background:#eff6ff; border:2px solid #3b82f6; border-radius:10px; "
                "padding:16px; margin-bottom:12px;'>"
                "<div style='font-size:18px; color:#1e40af; font-weight:700; margin-bottom:8px;'>"
                "📧 メール通知を送信しますか？</div>"
                f"<div style='color:#475569; font-size:14px;'>"
                f"宛先: <b>{recipient}</b>　|　件名: <b>{email_data['subject']}</b>"
                "</div></div>",
                unsafe_allow_html=True,
            )

            with st.expander("メール本文プレビュー", expanded=True):
                st.text(email_data['body'])

            n1, n2, n3 = st.columns([1, 1, 1])
            with n1:
                st.link_button(
                    "📩 メールアプリで開く",
                    email_data['mailto'],
                    width='stretch',
                )
            with n2:
                if notification.smtp_available():
                    if st.button(
                        "🚀 SMTPで自動送信",
                        type='primary', width='stretch',
                        key='smtp_send_btn',
                    ):
                        ok, msg = notification.smtp_send(
                            recipient, email_data['subject'], email_data['body']
                        )
                        if ok:
                            st.success(f"送信成功: {msg}")
                            st.session_state.pop('_pending_notification', None)
                            st.rerun()
                        else:
                            st.error(msg)
                else:
                    st.button("🚀 SMTP送信（未設定）", disabled=True,
                              width='stretch',
                              help='.streamlit/secrets.toml に [smtp] を追加すると自動送信できます')
            with n3:
                if st.button("通知をスキップ（クリア）",
                             type='secondary', width='stretch',
                             key='clear_notif_btn'):
                    st.session_state.pop('_pending_notification', None)
                    st.rerun()

        self_part = edited_self.reset_index(drop=True).assign(区分='自己負担')
        kokuho_part = edited_kokuho.reset_index(drop=True).assign(区分='国保請求')
        all_diff = pd.concat([self_part, kokuho_part], ignore_index=True)
        if '合計請求額' in all_diff.columns:
            all_diff['請求額'] = all_diff['請求額'].fillna(all_diff['合計請求額'])
        diff_only = all_diff[all_diff['差'] != 0].reset_index(drop=True)
        if not diff_only.empty:
            st.markdown("---")
            st.markdown(f"#### ⚠ 差額あり（{len(diff_only)}行 / 合計 {diff_only['差'].sum():,} 円）")
            diff_only = diff_only[['id', '利用者氏名', '受給者証番号', '施設', '区分',
                                    '請求額', '回収額', '差', '回収方法', '入金日', 'ステータス']]
            diff_only = diff_only.loc[:, ~diff_only.columns.duplicated()].reset_index(drop=True)
            st.dataframe(
                styling.style_records_df(diff_only),
                width='stretch', hide_index=True, height=240,
            )

    # ---- 一括入金登録 ----
    with tab_bulk:
        st.markdown(
            "**複数行をまとめて入金記録**できます。"
            "下の **🔵 自己負担 / 🟡 国保請求** タブで切り替え、"
            "氏名で絞り込み、共通の入金日・回収方法を設定して入金額をまとめて確定します。"
        )

        st.markdown("#### 共通絞り込み")
        cf1, cf2 = st.columns([3, 1])
        with cf1:
            search_name = st.text_input(
                "利用者氏名で絞り込み（部分一致）", value="",
                placeholder="例: カワシマ", key='bulk_search'
            )
        with cf2:
            only_unpaid = st.checkbox(
                "未入金・一部入金のみ", value=True, key='bulk_unpaid_only',
            )

        sub_self, sub_kokuho = st.tabs(["🔵 自己負担", "🟡 国保請求"])

        def _render_bulk_section(kbn_key, kbn_label, method_choices, color_bg, color_fg):
            target_records = []
            for r in records:
                name = r['child_name'] or ''
                if search_name and search_name not in name:
                    continue
                if kbn_key == 'self':
                    charge = _self_total_from_record(r)
                    current_paid = r['self_paid_amount'] or 0
                else:
                    charge = r['kokuho_charge'] or 0
                    current_paid = r['kokuho_paid_amount'] or 0
                status = _status_from_amounts(charge, current_paid)
                if charge <= 0:
                    continue
                if only_unpaid and status == '入金済':
                    continue
                target_records.append((r, charge, current_paid, status))

            st.markdown(
                f"<div style='background:{color_bg}; padding:8px 14px; border-radius:6px; "
                f"border-left:5px solid {color_fg}; margin:8px 0;'>"
                f"<span style='color:{color_fg}; font-weight:700; font-size:15px;'>"
                f"{kbn_label}</span>"
                f"<span style='color:#475569; font-size:12px; margin-left:10px;'>"
                f"対象 {len(target_records)}行</span></div>",
                unsafe_allow_html=True,
            )

            if not target_records:
                st.info(
                    f"{kbn_label}：対象がありません。"
                    "（既に全件 入金済 / 請求0円 / 検索条件不一致 のいずれか）"
                )
                return

            st.markdown("##### 共通設定")
            cs1, cs2, cs3 = st.columns(3)
            with cs1:
                common_date = st.date_input(
                    "入金日（共通）", value=date.today(),
                    key=f'bulk_date_{kbn_key}',
                )
            with cs2:
                common_method = st.selectbox(
                    "回収方法（共通）", method_choices,
                    key=f'bulk_method_{kbn_key}',
                    help=f"{kbn_label}用の回収方法: " + " / ".join(method_choices),
                )
            with cs3:
                slide_charge = st.checkbox(
                    "請求額をそのまま入金額に反映", value=True,
                    help="ONにすると入金額の初期値を請求額にコピーします（全額入金時の入力省略）。",
                    key=f'bulk_slide_{kbn_key}',
                )

            bulk_data = []
            for r, charge, current_paid, status in target_records:
                default_paid = charge if slide_charge else current_paid
                bulk_data.append({
                    '記録': True,
                    'id': r['id'],
                    '利用者氏名': r['child_name'] or '',
                    '請求額': charge,
                    '入金額': default_paid,
                    '現状': status,
                })
            bulk_df = pd.DataFrame(bulk_data)

            st.markdown("##### 編集テーブル")
            editor_key = (
                f"bulk_editor_{kbn_key}_{selected_ym}_{selected_facility_id}"
                f"_{search_name}_{only_unpaid}_{slide_charge}"
            )
            edited_bulk = st.data_editor(
                bulk_df,
                column_config={
                    '記録': st.column_config.CheckboxColumn(
                        '記録', help='チェックを外すとこの行はスキップ', width='small',
                    ),
                    'id': st.column_config.NumberColumn('id', disabled=True, width='small'),
                    '利用者氏名': st.column_config.TextColumn('利用者氏名', disabled=True),
                    '請求額': st.column_config.NumberColumn('請求額', disabled=True, format='localized'),
                    '入金額': st.column_config.NumberColumn('入金額', format='localized', step=1, min_value=0),
                    '現状': st.column_config.TextColumn('現状', disabled=True, width='small'),
                },
                hide_index=True,
                width='stretch',
                height=480,
                key=editor_key,
            )

            selected_count = int(edited_bulk['記録'].sum())
            target_subset = edited_bulk[edited_bulk['記録']]
            total_to_record = int(target_subset['入金額'].sum()) if len(target_subset) else 0

            m1, m2, _ = st.columns([1, 1, 3])
            m1.metric("記録対象", f"{selected_count} 行")
            m2.metric("入金合計", f"{total_to_record:,} 円")

            if st.button(
                f"📝 {kbn_label} まとめて記録（{selected_count}行）",
                type='primary',
                disabled=(selected_count == 0),
                width='stretch',
                key=f'bulk_record_btn_{kbn_key}',
            ):
                success = 0
                errors = []
                for idx in range(len(edited_bulk)):
                    row = edited_bulk.iloc[idx]
                    if not row['記録']:
                        continue
                    try:
                        _update_sales_record(
                            record_id=int(row['id']),
                            kbn=kbn_key,
                            paid_amount=int(row['入金額']),
                            paid_date=common_date.isoformat(),
                            method=common_method,
                        )
                        success += 1
                    except Exception as e:
                        errors.append(f"id={row['id']}: {e}")
                if success:
                    st.success(f"{kbn_label}: {success}行を記録しました")
                    st.balloons()
                if errors:
                    st.error("エラー:\n" + "\n".join(errors))
                st.rerun()

        with sub_self:
            _render_bulk_section(
                'self', '自己負担', db.PAYMENT_METHODS_SELF,
                color_bg='#dbeafe', color_fg='#1e3a8a',
            )
        with sub_kokuho:
            _render_bulk_section(
                'kokuho', '国保請求', db.PAYMENT_METHODS_KOKUHO,
                color_bg='#fef3c7', color_fg='#78350f',
            )

    # ---- 一括削除（管理者専用） ----
    if tab_delete is not None:
        with tab_delete:
            st.error(
                "⚠ **一括削除は取り消せません。** 削除前に必ずバックアップ "
                "（`data/uriage.db` のコピー）を取ってください。"
            )

            st.markdown("### 🎯 条件指定で削除")
            st.markdown(
                "現在の絞り込み条件にマッチするデータを削除します。"
                "**施設マスタ・ユーザー情報は削除されません**（取込履歴と売上明細のみ）。"
            )

            del_c1, del_c2 = st.columns(2)
            with del_c1:
                del_ym = st.selectbox(
                    "対象 サービス提供年月",
                    ['（すべての月）'] + year_months,
                    index=year_months.index(selected_ym) + 1 if selected_ym in year_months else 0,
                    key='del_ym_select',
                )
            with del_c2:
                del_facility_options = {"（すべての施設）": None}
                for f in facilities:
                    if f['csv_facility_code']:
                        del_facility_options[
                            f"{f['short_code']}: {f['facility_name']}"
                        ] = f['id']
                del_facility_label = st.selectbox(
                    "対象 施設",
                    list(del_facility_options.keys()),
                    key='del_facility_select',
                )
                del_facility_id = del_facility_options[del_facility_label]

            del_ym_param = None if del_ym == '（すべての月）' else del_ym
            target_count = db.count_records(
                service_ym=del_ym_param, facility_id=del_facility_id,
            )

            st.markdown(
                f"<div style='background:#fef2f2; border:2px solid #fecaca; "
                f"padding:14px 18px; border-radius:8px; margin:12px 0;'>"
                f"<div style='font-size:13px; color:#991b1b; font-weight:700;'>削除対象</div>"
                f"<div style='font-size:24px; color:#7f1d1d; font-weight:700; margin-top:4px;'>"
                f"{target_count:,} 件</div>"
                f"<div style='font-size:12px; color:#475569; margin-top:6px;'>"
                f"年月: {del_ym}　／　施設: {del_facility_label}</div></div>",
                unsafe_allow_html=True,
            )

            if target_count == 0:
                st.info("削除対象がありません")
            else:
                approval_check = st.checkbox(
                    "管理者として削除許可を得ています",
                    key='del_filter_approval_check',
                )
                confirm_check = st.checkbox(
                    "上記の件数を削除することに同意します（取り消せません）",
                    key='del_filter_confirm_check',
                )
                confirm_text = st.text_input(
                    f"確認のため `削除` と入力してください（{target_count:,}件削除）",
                    key='del_filter_confirm_text',
                    placeholder="削除",
                )
                del_disabled = not (
                    approval_check and confirm_check and confirm_text.strip() == '削除'
                )

                if st.button(
                    f"🗑 {target_count:,} 件を削除する",
                    disabled=del_disabled,
                    key='del_filter_btn',
                    width='content',
                ):
                    rec_n, imp_n = db.delete_records(
                        service_ym=del_ym_param, facility_id=del_facility_id,
                    )
                    st.success(
                        f"削除しました: 売上明細 {rec_n:,}件 / 取込履歴 {imp_n:,}件"
                    )
                    st.rerun()

            st.markdown("---")

            st.markdown("### 💣 全データ削除")
            all_count = db.count_records()
            st.markdown(
                f"<div style='background:#7f1d1d; color:white; padding:14px 18px; "
                f"border-radius:8px; margin:12px 0;'>"
                f"<div style='font-size:13px; font-weight:700;'>全データ</div>"
                f"<div style='font-size:24px; font-weight:700; margin-top:4px;'>"
                f"{all_count:,} 件のレコード</div>"
                f"<div style='font-size:12px; margin-top:6px; opacity:0.9;'>"
                f"全月・全施設の売上明細＋取込履歴をまるごと削除します"
                f"（施設マスタ・ユーザー情報は残ります）</div></div>",
                unsafe_allow_html=True,
            )

            if all_count == 0:
                st.info("削除対象がありません")
            else:
                confirm_all_approval = st.checkbox(
                    "管理者として全データ削除の許可を得ています",
                    key='del_all_approval_check',
                )
                confirm_all_check = st.checkbox(
                    "全データの削除に同意します（取り消せません）",
                    key='del_all_confirm_check',
                )
                confirm_all_text = st.text_input(
                    "確認のため `全データを削除します` と入力してください",
                    key='del_all_confirm_text',
                    placeholder="全データを削除します",
                )
                del_all_disabled = not (
                    confirm_all_approval
                    and confirm_all_check
                    and confirm_all_text.strip() == '全データを削除します'
                )

                if st.button(
                    f"💣 全データ（{all_count:,}件）を削除する",
                    disabled=del_all_disabled,
                    key='del_all_btn',
                    type='secondary',
                ):
                    rec_n, imp_n = db.delete_all_records()
                    st.success(
                        f"全データを削除しました: 売上明細 {rec_n:,}件 / 取込履歴 {imp_n:,}件"
                    )
                    st.rerun()

    # ---- Excelエクスポート ----
    st.markdown("---")
    buf = io.BytesIO()
    all_df_self, all_df_kokuho = _build_split_df(records)
    combined = pd.concat([
        all_df_self.assign(区分='自己負担'),
        all_df_kokuho.assign(区分='国保請求'),
    ], ignore_index=True)
    combined = combined.drop(columns=['一括対象'], errors='ignore')
    if '合計請求額' in combined.columns:
        combined['請求額'] = combined['請求額'].fillna(combined['合計請求額'])
    combined.to_excel(buf, index=False, engine='openpyxl')
    st.download_button(
        label="📥 表示中の一覧をExcelでダウンロード",
        data=buf.getvalue(),
        file_name=f"売上一覧_{selected_ym}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


def _payment_method_choices(kbn):
    if kbn == 'self':
        return db.PAYMENT_METHODS_SELF
    choices = ['国保', 'SMBC', '振込', '現金', 'その他', '自己']
    return list(dict.fromkeys(choices))


def _self_kwargs_from_row(row, is_share_layout=False):
    return {
        SELF_FIELD_PARAMS[field]: _as_int(row.get(label, 0))
        for label, field, _sign in _self_items(is_share_layout)
    }


def _copy_record_values_to_labels(record, is_share_layout=False):
    values = {}
    for label, field, _sign in _self_items(is_share_layout):
        values[label] = record.get(field) or 0
    return values


def _set_bulk_selection(selection_key, editor_version_key, record_ids):
    st.session_state[selection_key] = [int(rid) for rid in record_ids]
    st.session_state[editor_version_key] = st.session_state.get(editor_version_key, 0) + 1


def _clear_bulk_selection(selection_key, editor_version_key):
    st.session_state[selection_key] = []
    st.session_state[editor_version_key] = st.session_state.get(editor_version_key, 0) + 1


def _render_bulk_selection_buttons(kbn, area_key, selection_key, editor_version_key,
                                   record_ids, selected_ym, selected_facility_id):
    row_count = len(record_ids)
    select_cols = st.columns(2)
    with select_cols[0]:
        st.button(
            "全てチェック",
            type='secondary',
            disabled=row_count == 0,
            width='stretch',
            key=f"bulk_select_all_{area_key}_{kbn}_{selected_ym}_{selected_facility_id}",
            on_click=_set_bulk_selection,
            args=(selection_key, editor_version_key, record_ids),
        )
    with select_cols[1]:
        st.button(
            "チェックを外す",
            type='secondary',
            disabled=row_count == 0,
            width='stretch',
            key=f"bulk_clear_all_{area_key}_{kbn}_{selected_ym}_{selected_facility_id}",
            on_click=_clear_bulk_selection,
            args=(selection_key, editor_version_key),
        )


def _render_payment_tools(kbn, label, edited_df, selected_ym, selected_facility_id,
                          state_key, selection_key, editor_version_key,
                          is_share_layout=False):
    selected_rows = edited_df[edited_df['一括対象'] == True].copy()
    method_choices = _payment_method_choices(kbn)
    record_ids = [int(rid) for rid in edited_df['id'].tolist()] if 'id' in edited_df.columns else []

    tool_cols = st.columns(2)
    with tool_cols[0]:
        with st.expander(f"{label}の一括入金登録", expanded=False):
            st.caption("一覧で「一括対象」にチェックした行を、まとめて入金済みにします。")
            _render_bulk_selection_buttons(
                kbn, "paid", selection_key, editor_version_key,
                record_ids, selected_ym, selected_facility_id
            )
            if kbn == 'self':
                c1, c2 = st.columns(2)
                with c1:
                    bulk_method = st.selectbox(
                        "回収方法",
                        method_choices,
                        key=f"top_bulk_method_{kbn}_{selected_ym}_{selected_facility_id}",
                    )
                with c2:
                    st.metric("チェック中", f"{len(selected_rows)} 行")
                st.caption("回収額は合計請求額と同じ金額で登録し、入金日は回収額を登録した日付になります。")
            else:
                bulk_method = None
                st.metric("チェック中", f"{len(selected_rows)} 行")
                st.caption("回収額は国保請求額と同じ金額で登録し、記載日は回収額を登録した日付になります。")
            if st.button(
                f"{label}をまとめて入金登録",
                type='primary',
                disabled=selected_rows.empty,
                width='stretch',
                key=f"top_bulk_paid_{kbn}_{selected_ym}_{selected_facility_id}",
            ):
                success = 0
                errors = []
                for _, row in selected_rows.iterrows():
                    try:
                        kwargs = {
                            'record_id': int(row['id']),
                            'kbn': kbn,
                            'charge': _as_int(
                                row['サービス料'] if kbn == 'self' and is_share_layout
                                else row['自己負担額'] if kbn == 'self'
                                else row['国保請求額']
                            ),
                            'paid_amount': _as_int(row['合計請求額'] if kbn == 'self' else row['国保請求額']),
                            'paid_date': _today_jst().isoformat(),
                            'method': bulk_method,
                            'memo': row.get('備考') or None,
                        }
                        if kbn == 'self':
                            kwargs.update(_self_kwargs_from_row(row, is_share_layout))
                        else:
                            kwargs.update({
                                'addition_charge': None,
                                'adjustment_charge': None,
                            })
                        _update_sales_record(**kwargs)
                        success += 1
                    except Exception as e:
                        errors.append(f"id={row.get('id')}: {e}")
                if success:
                    st.success(f"{label}の一括入金を保存しました: {success}行")
                    st.balloons()
                if errors:
                    st.error("エラー:\n" + "\n".join(errors))
                st.session_state.pop(state_key, None)
                _clear_bulk_selection(selection_key, editor_version_key)
                st.rerun()

    with tool_cols[1]:
        with st.expander(f"{label}の一括削除（管理者・許可制）", expanded=False):
            st.caption(
                "安全のため、行そのものは消さず、この区分の請求額・回収額を0円にして一覧対象から外します。"
            )
            _render_bulk_selection_buttons(
                kbn, "delete", selection_key, editor_version_key,
                record_ids, selected_ym, selected_facility_id
            )
            st.metric("チェック中", f"{len(selected_rows)} 行")
            approval = st.checkbox(
                "管理者として削除許可を得ています",
                key=f"top_delete_approval_{kbn}_{selected_ym}_{selected_facility_id}",
            )
            confirm = st.text_input(
                "確認のため `削除` と入力してください",
                key=f"top_delete_text_{kbn}_{selected_ym}_{selected_facility_id}",
                placeholder="削除",
            )
            disabled = selected_rows.empty or not (approval and confirm.strip() == '削除')
            if st.button(
                f"{label}を一覧から外す",
                type='secondary',
                disabled=disabled,
                width='stretch',
                key=f"top_delete_btn_{kbn}_{selected_ym}_{selected_facility_id}",
            ):
                success = 0
                errors = []
                for _, row in selected_rows.iterrows():
                    try:
                        kwargs = {
                            'record_id': int(row['id']),
                            'kbn': kbn,
                            'charge': 0,
                            'paid_amount': 0,
                            'paid_date': None,
                            'method': None,
                            'memo': row.get('備考') or None,
                        }
                        if kbn == 'self':
                            kwargs.update({
                                param: 0
                                for param in SELF_FIELD_PARAMS.values()
                            })
                        else:
                            kwargs.update({'addition_charge': 0, 'adjustment_charge': 0})
                        _update_sales_record(**kwargs)
                        success += 1
                    except Exception as e:
                        errors.append(f"id={row.get('id')}: {e}")
                if success:
                    st.success(f"{label}を一覧から外しました: {success}行")
                if errors:
                    st.error("エラー:\n" + "\n".join(errors))
                st.session_state.pop(state_key, None)
                _clear_bulk_selection(selection_key, editor_version_key)
                st.rerun()


def _render_money_inputs(item_labels, key_prefix, columns=4):
    values = {}
    for start in range(0, len(item_labels), columns):
        cols = st.columns(min(columns, len(item_labels) - start))
        for idx, item_label in enumerate(item_labels[start:start + columns]):
            with cols[idx]:
                values[item_label] = st.number_input(
                    item_label, min_value=0, step=1,
                    key=f"{key_prefix}_{start + idx}",
                )
    return values


def _render_manual_add(kbn, label, selected_ym, selected_facility_id,
                       selected_facility_label, state_key, is_share_layout=False,
                       expanded=False):
    with st.expander(f"{label}の行を追加", expanded=expanded):
        st.caption("CSVに出てこない利用者や追加請求を、選択中のサービス年月に1行追加します。")
        manual_facility_options = {
            f"{f['short_code']}: {f['facility_name']}": f['id']
            for f in facilities
        }
        with st.form(f"manual_add_{kbn}_{selected_ym}_{selected_facility_id}"):
            m_cols1 = st.columns(2)
            with m_cols1[0]:
                if selected_facility_id is None:
                    manual_facility_label = st.selectbox(
                        "施設",
                        list(manual_facility_options.keys()),
                        key=f"manual_fac_{kbn}_{selected_ym}",
                    )
                    manual_facility_id = manual_facility_options.get(manual_facility_label)
                else:
                    st.text_input("施設", value=selected_facility_label, disabled=True)
                    manual_facility_id = selected_facility_id
            with m_cols1[1]:
                manual_cert = st.text_input("受給者証番号")

            m_cols2 = st.columns(2)
            with m_cols2[0]:
                manual_name = st.text_input("利用者氏名")
            with m_cols2[1]:
                if kbn == 'self':
                    manual_method = st.selectbox("回収方法", [''] + _payment_method_choices(kbn))
                else:
                    manual_method = None
                    st.caption("国保請求は回収方法を使いません。")

            if kbn == 'self':
                item_labels = [item[0] for item in _self_items(is_share_layout)]
                values = _render_money_inputs(
                    item_labels,
                    f"manual_{kbn}_{selected_ym}_{selected_facility_id}_{'share' if is_share_layout else 'normal'}",
                )
                manual_paid = st.number_input("回収額", min_value=0, step=1, key=f"manual_paid_{kbn}")
            else:
                m_cols3 = st.columns(2)
                values = [0, 0, 0, 0]
                with m_cols3[0]:
                    values[0] = st.number_input("国保請求額", min_value=0, step=1, key=f"manual_{kbn}_charge")
                with m_cols3[1]:
                    manual_paid = st.number_input("回収額", min_value=0, step=1, key=f"manual_paid_{kbn}")

            manual_memo = st.text_input("備考")
            manual_report = st.checkbox(
                "この備考を上司に報告する",
                value=False,
                key=f"manual_report_{kbn}_{selected_ym}_{selected_facility_id}",
            )
            submitted = st.form_submit_button(f"{label}行を追加", type='primary')
            if submitted:
                try:
                    if kbn == 'self':
                        _add_manual_self_record(
                            service_ym=selected_ym,
                            facility_id=manual_facility_id,
                            cert_number=manual_cert,
                            child_name=manual_name,
                            **{
                                SELF_FIELD_PARAMS[field]: _as_int(values.get(item_label, 0))
                                for item_label, field, _sign in _self_items(is_share_layout)
                            },
                            paid_amount=manual_paid,
                            method=manual_method or None,
                            memo=manual_memo or None,
                            report_to_supervisor=manual_report,
                        )
                    else:
                        _add_manual_kokuho_record(
                            service_ym=selected_ym,
                            facility_id=manual_facility_id,
                            cert_number=manual_cert,
                            child_name=manual_name,
                            kokuho_charge=values[0],
                            addition_charge=0,
                            adjustment_charge=0,
                            other_charge=0,
                            paid_amount=manual_paid,
                            method=None,
                            memo=manual_memo or None,
                            report_to_supervisor=manual_report,
                        )
                    st.success(f"{label}行を追加しました。")
                    st.session_state.pop(state_key, None)
                    st.rerun()
                except Exception as e:
                    st.error(f"追加できませんでした: {e}")


def _previous_year_month(ym):
    try:
        year, month = [int(part) for part in ym.split('-')]
    except Exception:
        return None
    month -= 1
    if month == 0:
        year -= 1
        month = 12
    return f"{year:04d}-{month:02d}"


def _render_previous_month_copy(selected_ym, selected_facility_id, selected_facility_label,
                                state_key, is_share_layout=False):
    with st.expander("前月項目のコピー", expanded=False):
        st.caption("前月の固定費などを、同じ利用者の今月行へコピーします。入金額・入金日はコピーしません。")
        if selected_facility_id is None:
            st.info("前月コピーを使う場合は、先に施設を1つ選んでください。")
            return

        prev_ym = _previous_year_month(selected_ym)
        if not prev_ym:
            st.warning("サービス提供年月を確認してください。")
            return

        current_records = db.list_records(service_ym=selected_ym, facility_id=selected_facility_id)
        prev_records = db.list_records(service_ym=prev_ym, facility_id=selected_facility_id)
        if not current_records:
            st.info("今月の行がありません。先にCSV取込または行追加をしてください。")
            return
        if not prev_records:
            st.info(f"{prev_ym} の前月データがありません。")
            return

        prev_by_cert = {str(r.get('cert_number')): r for r in prev_records}
        current_by_id = {int(r['id']): r for r in current_records}
        item_labels = [label for label, _field, _sign in _self_items(is_share_layout)]
        total_label = "利用者請求額合計" if is_share_layout else "合計請求額"

        rows = []
        for current in current_records:
            previous = prev_by_cert.get(str(current.get('cert_number')))
            if not previous:
                continue
            row = {
                'コピー対象': False,
                'id': int(current['id']),
                '利用者氏名': current.get('child_name') or '',
                '受給者証番号': current.get('cert_number') or '',
            }
            row.update(_copy_record_values_to_labels(previous, is_share_layout))
            row[total_label] = _self_total_from_labels(row, _self_items(is_share_layout))
            rows.append(row)

        if not rows:
            st.info("前月と今月で一致する利用者がありません。")
            return

        copy_df = pd.DataFrame(rows)
        copy_selection_key = f"prev_copy_select_{selected_ym}_{selected_facility_id}_{'share' if is_share_layout else 'normal'}"
        copy_version_key = f"{copy_selection_key}_version"
        copy_version = st.session_state.get(copy_version_key, 0)
        if copy_selection_key in st.session_state:
            selected_ids = {int(rid) for rid in st.session_state.get(copy_selection_key, [])}
            copy_df['コピー対象'] = copy_df['id'].map(lambda rid: int(rid) in selected_ids)

        record_ids = [int(rid) for rid in copy_df['id'].tolist()]
        _render_bulk_selection_buttons(
            'self_copy', 'prev', copy_selection_key, copy_version_key,
            record_ids, selected_ym, selected_facility_id,
        )

        column_config = {
            'コピー対象': st.column_config.CheckboxColumn('コピー対象', width='small'),
            'id': st.column_config.NumberColumn('id', disabled=True, width='small'),
            '利用者氏名': st.column_config.TextColumn('利用者氏名', disabled=True),
            '受給者証番号': st.column_config.TextColumn('受給者証番号', disabled=True),
            total_label: st.column_config.NumberColumn(total_label, format='localized', disabled=True),
        }
        for item_label in item_labels:
            column_config[item_label] = st.column_config.NumberColumn(
                item_label, format='localized', disabled=True,
            )

        edited_copy = st.data_editor(
            copy_df,
            column_config=column_config,
            column_order=['コピー対象', 'id', '利用者氏名', '受給者証番号', *item_labels, total_label],
            hide_index=True,
            width='stretch',
            height=320,
            key=f"prev_copy_editor_{selected_ym}_{selected_facility_id}_{copy_version}",
        )
        selected_copy = edited_copy[edited_copy['コピー対象'] == True].copy()
        st.session_state[copy_selection_key] = [
            int(row['id']) for _, row in selected_copy.iterrows()
        ]
        st.metric("コピー対象", f"{len(selected_copy)} 行")

        if st.button(
            "前月項目をコピー",
            type='primary',
            disabled=selected_copy.empty,
            width='stretch',
            key=f"prev_copy_btn_{selected_ym}_{selected_facility_id}_{'share' if is_share_layout else 'normal'}",
        ):
            success = 0
            errors = []
            for _, row in selected_copy.iterrows():
                try:
                    current = current_by_id[int(row['id'])]
                    kwargs = {
                        'record_id': int(row['id']),
                        'kbn': 'self',
                        'paid_amount': current.get('self_paid_amount') or 0,
                        'paid_date': current.get('self_paid_date'),
                        'method': current.get('self_payment_method'),
                        'memo': None,
                    }
                    kwargs.update(_self_kwargs_from_row(row, is_share_layout))
                    _update_sales_record(**kwargs)
                    success += 1
                except Exception as e:
                    errors.append(f"id={row.get('id')}: {e}")
            if success:
                st.success(f"前月項目をコピーしました: {success}行")
                st.session_state.pop(state_key, None)
                _clear_bulk_selection(copy_selection_key, copy_version_key)
                st.rerun()
            if errors:
                st.error("エラー:\n" + "\n".join(errors))


def render_payment_page(kbn):
    label = '自己負担' if kbn == 'self' else '国保請求'
    ym_options = year_months or [_today_jst().strftime('%Y-%m')]
    if not year_months:
        st.info("まだCSVデータはありません。手入力で始める場合は、この画面下の「行を追加」から登録できます。")

    page_help = (
        "一覧の金額を編集すると、合計請求額と差額がその場で再計算されます。"
        if kbn == 'self'
        else "国保請求額はCSVから取り込みます。回収額を入力すると、差額とステータスがその場で再計算されます。"
    )
    st.markdown(
        f"<div style='background:#fef9c3; border-left:5px solid #facc15; "
        f"padding:10px 16px; border-radius:6px; margin-bottom:12px; font-size:13px;'>"
        f"<b>{label}ページです。</b> {page_help}"
        f"「一括対象」にチェックした行は、下の一括入金・一括削除でまとめて処理できます。"
        f"</div>",
        unsafe_allow_html=True,
    )

    filter_cols = st.columns(2)
    with filter_cols[0]:
        selected_ym = st.selectbox(
            "サービス提供年月",
            ym_options,
            index=0,
            key=f'{kbn}_ym',
        )
    with filter_cols[1]:
        facility_options = {"（すべて）": None}
        for f in facilities:
            facility_options[f"{f['short_code']}: {f['facility_name']}"] = f['id']
        selected_facility_label = st.selectbox(
            "施設",
            list(facility_options.keys()),
            key=f'{kbn}_fac',
        )
        selected_facility_id = facility_options[selected_facility_label]
    is_share_layout = kbn == 'self' and selected_facility_id is not None and _is_share_facility_label(selected_facility_label)

    records = db.list_records(service_ym=selected_ym, facility_id=selected_facility_id)
    has_records = bool(records)
    if has_records:
        self_df, kokuho_df = _build_split_df(records)
    else:
        st.info("この条件の行はまだありません。下に未入力の一覧枠と行追加フォームを表示しています。")
        self_df = _empty_payment_df('self')
        kokuho_df = _empty_payment_df('kokuho')

    if kbn == 'self':
        df = self_df
        editor_base_key = f"top_editor_self_{selected_ym}_{selected_facility_id}"
        column_labels = {label: label for label, _field, _sign in _self_items(is_share_layout)}
        total_label = "利用者請求額合計" if is_share_layout else "合計請求額"
    else:
        df = kokuho_df
        editor_base_key = f"top_editor_kokuho_{selected_ym}_{selected_facility_id}"
        column_labels = {
            '国保請求額': '国保請求額',
        }
        total_label = "国保請求額"
    selection_key = f"top_bulk_select_{kbn}_{selected_ym}_{selected_facility_id}"
    editor_version_key = f"{selection_key}_editor_version"
    editor_version = st.session_state.get(editor_version_key, 0)
    editor_key = f"{editor_base_key}_{editor_version}"

    charge_filter_col = '合計請求額' if kbn == 'self' else '国保請求額'
    df = df[
        (df[charge_filter_col].fillna(0).map(_as_int) != 0)
        | (df['回収額'].fillna(0).map(_as_int) != 0)
    ].reset_index(drop=True)
    if df.empty:
        st.info(f"{label}の対象行はありません。必要な場合は下の「{label}の行を追加」から登録できます。")
    manual_add_expanded = df.empty

    if selection_key in st.session_state and '一括対象' in df.columns and 'id' in df.columns:
        selected_ids = {int(rid) for rid in st.session_state.get(selection_key, [])}
        df = df.copy()
        df['一括対象'] = df['id'].map(lambda rid: int(rid) in selected_ids)

    display_df = (
        _apply_editor_state_to_self_df(df, editor_key, is_share_layout)
        if kbn == 'self'
        else _apply_editor_state_to_kokuho_df(df, editor_key)
    )

    state_key = f"top_orig_{kbn}_{selected_ym}_{selected_facility_id}"
    stored_original = st.session_state.get(state_key)
    if (
        not isinstance(stored_original, pd.DataFrame)
        or list(stored_original.columns) != list(df.columns)
        or len(stored_original) != len(df)
    ):
        st.session_state[state_key] = df.copy()
    original_df = st.session_state[state_key]

    charge_sum_col = '合計請求額' if kbn == 'self' else '国保請求額'
    sum_charge = int(display_df[charge_sum_col].sum()) if not display_df.empty else 0
    sum_paid = int(display_df['回収額'].sum()) if not display_df.empty else 0
    metric_cols = st.columns(4)
    metric_cols[0].metric("対象行", f"{len(display_df)} 行")
    metric_cols[1].metric(total_label, f"{sum_charge:,} 円")
    metric_cols[2].metric(f"{label} 入金", f"{sum_paid:,} 円")
    metric_cols[3].metric("差額", f"{sum_charge - sum_paid:,} 円", delta_color="inverse")

    base_columns = {
        '一括対象': st.column_config.CheckboxColumn(
            '一括対象',
            help='一括入金・一括削除の対象にする場合はチェック',
            width='small',
        ),
        'id': st.column_config.NumberColumn('id', disabled=True, width='small'),
        '利用者氏名': st.column_config.TextColumn('利用者氏名', disabled=True),
        '受給者証番号': st.column_config.TextColumn('受給者証番号', disabled=True),
        '施設': st.column_config.TextColumn('施設', disabled=True),
    }
    if kbn == 'self':
        base_columns.update({
            '合計請求額': st.column_config.NumberColumn(total_label, format='localized', disabled=True),
            '回収額': st.column_config.NumberColumn('回収額', format='localized', step=1, min_value=0),
            '差': st.column_config.NumberColumn('差', format='localized', disabled=True),
            '入金日': st.column_config.DateColumn('入金日', format='YYYY-MM-DD', disabled=True),
            'ステータス': st.column_config.TextColumn('ステータス', disabled=True, width='small'),
            '回収方法': st.column_config.SelectboxColumn(
                '回収方法',
                options=[''] + _payment_method_choices(kbn),
                required=False,
                width='small',
            ),
            '備考': st.column_config.TextColumn('備考', width='medium'),
        })
        for col, title in column_labels.items():
            base_columns[col] = st.column_config.NumberColumn(
                title, format='localized', step=1, min_value=0,
            )
    else:
        base_columns.update({
            '国保請求額': st.column_config.NumberColumn('国保請求額', format='localized', disabled=True),
            '回収額': st.column_config.NumberColumn('回収額', format='localized', step=1, min_value=0),
            '差': st.column_config.NumberColumn('差', format='localized', disabled=True),
            '記載日': st.column_config.DateColumn('記載日', format='YYYY-MM-DD', disabled=True),
            'ステータス': st.column_config.TextColumn('ステータス', disabled=True, width='small'),
            '備考': st.column_config.TextColumn('備考', width='medium'),
        })

    if kbn == 'self':
        item_order = [label for label, _field, _sign in _self_items(is_share_layout)]
        column_order = [
            'id', '一括対象', '利用者氏名', '受給者証番号', '施設',
            *item_order, '合計請求額', '回収額', '差', '回収方法',
            '入金日', 'ステータス', '備考',
        ]
    else:
        column_order = [
            'id', '一括対象', '利用者氏名', '受給者証番号', '施設',
            '国保請求額', '回収額', '差', '記載日', 'ステータス', '備考',
        ]

    edited_df = st.data_editor(
        styling.style_editor_df(display_df),
        column_config=base_columns,
        column_order=[col for col in column_order if col in display_df.columns],
        hide_index=True,
        width='stretch',
        height=430,
        key=editor_key,
    )
    edited_df = (
        _recompute_self_invoice_totals(edited_df, is_share_layout)
        if kbn == 'self'
        else _recompute_kokuho_invoice_totals(edited_df)
    )
    if '一括対象' in edited_df.columns and 'id' in edited_df.columns:
        st.session_state[selection_key] = [
            int(row['id'])
            for _, row in edited_df[edited_df['一括対象'] == True].iterrows()
        ]

    _render_payment_tools(
        kbn, label, edited_df, selected_ym, selected_facility_id,
        state_key, selection_key, editor_version_key, is_share_layout
    )
    if kbn == 'self':
        add_col, copy_col = st.columns(2)
        with add_col:
            _render_manual_add(
                kbn, label, selected_ym, selected_facility_id,
                selected_facility_label, state_key, is_share_layout,
                expanded=manual_add_expanded,
            )
        with copy_col:
            _render_previous_month_copy(
                selected_ym, selected_facility_id, selected_facility_label,
                state_key, is_share_layout
            )
    else:
        _render_manual_add(
            kbn, label, selected_ym, selected_facility_id,
            selected_facility_label, state_key, is_share_layout,
            expanded=manual_add_expanded,
        )

    changes = _detect_changes(edited_df, original_df, kbn, is_share_layout)
    risky_changes = [c for c in changes if c['risky']]
    st.markdown("---")
    _render_report_prompt(changes, kbn, selected_ym, selected_facility_id)
    save_cols = st.columns([1, 4])
    with save_cols[1]:
        if changes:
            st.info(f"未保存の編集: **{len(changes)}行**")
        else:
            st.caption("編集はまだありません。")

    supervisor_ok = True
    if risky_changes:
        st.warning("入金済み行の請求額変更が含まれます。保存前に許可確認をしてください。")
        supervisor_ok = st.checkbox(
            "許可を得たので保存する",
            value=False,
            key=f"approval_{kbn}_{selected_ym}_{selected_facility_id}",
        )

    with save_cols[0]:
        save_clicked = st.button(
            "保存",
            type='primary',
            disabled=(not changes) or (bool(risky_changes) and not supervisor_ok),
            width='stretch',
            key=f"save_{kbn}_{selected_ym}_{selected_facility_id}",
        )

    if save_clicked:
        success = 0
        errors = []
        for c in changes:
            try:
                kwargs = {
                    'record_id': c['id'],
                    'kbn': kbn,
                    'charge': c['new_charge'],
                    'paid_amount': c['new_paid'],
                    'paid_date': c['paid_date'].isoformat() if c['paid_date'] else None,
                    'method': c['method'] if c['method'] else None,
                    'memo': c['new_memo'] if c['memo_changed'] else None,
                    'other_charge': c.get('other_charge'),
                    'report_to_supervisor': c.get('report_to_supervisor'),
                }
                if kbn == 'self':
                    kwargs.update(c.get('self_values') or {})
                else:
                    kwargs.update({
                        'addition_charge': c.get('addition_charge'),
                        'adjustment_charge': c.get('adjustment_charge'),
                    })
                _update_sales_record(**kwargs)
                success += 1
            except Exception as e:
                errors.append(f"id={c['id']}: {e}")
        if success:
            st.success(f"{label}を保存しました: {success}行")
            st.session_state.pop(state_key, None)
            st.rerun()
        if errors:
            st.error("エラー:\n" + "\n".join(errors))


# ============================================================
# ③ 未入金確認タブ
# ============================================================
def render_unpaid():
    if not year_months:
        st.info("まだデータがありません。「**📥 CSV取込**」タブから取込んでください。")
        return

    st.markdown(
        "未入金・一部入金のレコードを **経過月数** で集計し、督促リスト出力できます。"
    )

    st.markdown("#### 絞り込み")
    c1, c2, c3 = st.columns(3)
    with c1:
        facility_options = {"全施設": None}
        for f in facilities:
            if f['csv_facility_code']:
                facility_options[f"{f['short_code']}: {f['facility_name']}"] = f['id']
        selected_facility_label = st.selectbox(
            "施設", list(facility_options.keys()), key='unpaid_fac',
        )
        selected_facility_id = facility_options[selected_facility_label]
    with c2:
        target_kbn = st.selectbox("区分", ["両方", "自己負担のみ", "国保請求のみ"],
                                    key='unpaid_kbn')
    with c3:
        aging_filter = st.selectbox(
            "経過月数",
            ["すべて", "1ヶ月未満", "1〜3ヶ月", "3〜6ヶ月", "6ヶ月以上"],
            key='unpaid_aging',
        )

    all_records = db.list_records(facility_id=selected_facility_id)

    today = date.today()
    rows = []
    for r in all_records:
        for kbn in ('self', 'kokuho'):
            kbn_label = '自己負担' if kbn == 'self' else '国保請求'
            if target_kbn == '自己負担のみ' and kbn != 'self':
                continue
            if target_kbn == '国保請求のみ' and kbn != 'kokuho':
                continue

            charge = _self_total_from_record(r) if kbn == 'self' else _kokuho_total_from_record(r)
            paid = r[f'{kbn}_paid_amount'] or 0
            status = _status_from_amounts(charge, paid)

            if status in ('入金済', '対象外') or charge <= 0:
                continue
            diff = charge - paid

            try:
                ym = r['service_year_month']
                ym_date = datetime.strptime(f"{ym}-01", "%Y-%m-%d").date()
                months = (today.year - ym_date.year) * 12 + (today.month - ym_date.month)
            except Exception:
                months = 0

            rows.append({
                'id': r['id'],
                'サービス年月': r['service_year_month'],
                '経過月数': months,
                '施設': r['facility_name'],
                '受給者証番号': r['cert_number'],
                '利用者氏名': r['child_name'] or '',
                '区分': kbn_label,
                '請求額': charge,
                '回収額': paid,
                '差額（未収）': diff,
                'ステータス': status,
                '回収方法': r[f'{kbn}_payment_method'] or '',
            })

    def aging_bucket(m):
        if m < 1:
            return "1ヶ月未満"
        if m < 3:
            return "1〜3ヶ月"
        if m < 6:
            return "3〜6ヶ月"
        return "6ヶ月以上"

    if aging_filter != 'すべて':
        rows = [r for r in rows if aging_bucket(r['経過月数']) == aging_filter]

    if not rows:
        st.success("該当する未入金レコードはありません")
        return

    st.markdown("---")
    st.markdown("#### 経過月数別サマリ")

    agg = {'1ヶ月未満': {'count': 0, 'amount': 0},
           '1〜3ヶ月': {'count': 0, 'amount': 0},
           '3〜6ヶ月': {'count': 0, 'amount': 0},
           '6ヶ月以上': {'count': 0, 'amount': 0}}
    for r in rows:
        bucket = aging_bucket(r['経過月数'])
        agg[bucket]['count'] += 1
        agg[bucket]['amount'] += r['差額（未収）']

    sc1, sc2, sc3, sc4 = st.columns(4)
    for col, (bucket, color) in zip(
        [sc1, sc2, sc3, sc4],
        [('1ヶ月未満', '#10b981'),
         ('1〜3ヶ月', '#f59e0b'),
         ('3〜6ヶ月', '#f97316'),
         ('6ヶ月以上', '#dc2626')],
    ):
        with col:
            v = agg[bucket]
            st.markdown(
                f"<div style='background:white; border:2px solid {color}; border-radius:10px; "
                f"padding:14px; text-align:center;'>"
                f"<div style='color:{color}; font-size:13px; font-weight:700;'>{bucket}</div>"
                f"<div style='font-size:22px; font-weight:700; color:#0f172a; margin-top:4px;'>"
                f"{v['count']} 件</div>"
                f"<div style='font-size:14px; color:#475569; margin-top:2px;'>"
                f"{v['amount']:,} 円</div></div>",
                unsafe_allow_html=True,
            )

    total_count = sum(v['count'] for v in agg.values())
    total_amount = sum(v['amount'] for v in agg.values())
    st.markdown(
        f"<div style='background:#fef2f2; border-left:6px solid #dc2626; "
        f"padding:12px 16px; border-radius:8px; margin-top:16px;'>"
        f"<span style='font-size:14px; color:#7f1d1d; font-weight:600;'>未入金合計：</span> "
        f"<span style='font-size:18px; color:#7f1d1d; font-weight:800;'>"
        f"{total_count} 件 / {total_amount:,} 円</span></div>",
        unsafe_allow_html=True,
    )

    st.markdown("---")
    st.markdown(f"#### 一覧（{len(rows)} 件、古い順）")

    rows.sort(key=lambda r: (-r['経過月数'], r['施設'], r['利用者氏名']))

    df = pd.DataFrame(rows)

    def color_aging(val):
        if val >= 6:
            return 'background-color:#fee2e2; color:#991b1b; font-weight:700'
        if val >= 3:
            return 'background-color:#ffedd5; color:#9a3412; font-weight:700'
        if val >= 1:
            return 'background-color:#fef3c7; color:#92400e; font-weight:700'
        return 'background-color:#d1fae5; color:#065f46; font-weight:700'

    def color_kbn(val):
        if val == '自己負担':
            return 'background-color:#dbeafe; color:#1e3a8a; font-weight:600'
        if val == '国保請求':
            return 'background-color:#fef3c7; color:#78350f; font-weight:600'
        return ''

    def color_diff(val):
        return 'color:#dc2626; font-weight:700' if val and val != 0 else ''

    styled = (
        df.style
        .map(color_aging, subset=['経過月数'])
        .map(color_kbn, subset=['区分'])
        .map(color_diff, subset=['差額（未収）'])
        .format({'請求額': '{:,}', '回収額': '{:,}', '差額（未収）': '{:,}'})
    )
    st.dataframe(styled, width='stretch', hide_index=True, height=520)

    st.markdown("---")
    st.markdown("#### 督促リスト出力")
    st.caption("表示中の未入金一覧をExcelファイルでダウンロードできます。")

    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine='openpyxl')
    st.download_button(
        label="📥 督促リストをExcelでダウンロード",
        data=buf.getvalue(),
        file_name=f"督促リスト_{date.today().isoformat()}.xlsx",
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type='primary',
    )

    st.info(
        "💡 入金確認後の入金登録は **「自己負担」または「国保請求」タブ** の"
        "一括入金登録からまとめて記録できます。"
    )


# ============================================================
# ④ 売上確認タブ（管理者専用）
# ============================================================
def render_dashboard():
    if not year_months:
        st.info("まだデータがありません。「**📥 CSV取込**」タブから取込んでください。")
        return

    st.markdown("#### 表示条件")
    c1, c2, c3 = st.columns([1, 2, 2])

    with c1:
        period_mode = st.radio(
            "期間モード",
            ["単月", "年度（4月-3月）"],
            key='dash_period_mode',
        )

    with c2:
        if period_mode == "単月":
            selected_ym = st.selectbox("対象月", year_months, index=0, key='dash_month')
            start_ym = end_ym = selected_ym
            period_label = selected_ym
            is_fy_mode = False
        else:
            fiscal_years = db.list_fiscal_years()
            selected_fy = st.selectbox(
                "対象年度",
                fiscal_years,
                format_func=lambda fy: f"{fy}年度（{fy}/4 〜 {fy+1}/3）",
                key='dash_fy',
            )
            start_ym, end_ym = db.fiscal_year_range(selected_fy)
            period_label = f"{selected_fy}年度"
            is_fy_mode = True

    with c3:
        facility_options = {"全施設（合計）": None}
        for f in facilities:
            if f['csv_facility_code']:
                facility_options[f"{f['short_code']}: {f['facility_name']}"] = f['id']
        selected_facility_label = st.selectbox(
            "対象施設", list(facility_options.keys()), key='dash_fac',
        )
        selected_facility_id = facility_options[selected_facility_label]

    records = db.list_records_in_range(start_ym, end_ym, selected_facility_id)

    if not records:
        st.warning("対象期間にデータがありません")
        return

    fac_label = (
        "全施設" if selected_facility_id is None
        else selected_facility_label
    )
    st.markdown(
        f"<div style='background:linear-gradient(90deg,#dbeafe 0%,#fef3c7 100%); "
        f"padding:14px 20px; border-radius:10px; border-left:6px solid #2563eb; "
        f"margin:16px 0;'>"
        f"<div style='font-size:13px; color:#475569; font-weight:600;'>表示中</div>"
        f"<div style='font-size:20px; color:#0f172a; font-weight:700; margin-top:4px;'>"
        f"{period_label}　／　{fac_label}</div></div>",
        unsafe_allow_html=True,
    )

    self_charge = sum(_self_total_from_record(r) for r in records)
    self_paid = sum(r['self_paid_amount'] or 0 for r in records)
    kokuho_charge = sum(_kokuho_total_from_record(r) for r in records)
    kokuho_paid = sum(r['kokuho_paid_amount'] or 0 for r in records)
    total_charge = self_charge + kokuho_charge
    total_paid = self_paid + kokuho_paid

    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("対象件数", f"{len(records)} 件")
    sc2.metric("請求総額", f"{total_charge:,} 円")
    sc3.metric("入金額", f"{total_paid:,} 円")
    sc4.metric("未入金額", f"{total_charge - total_paid:,} 円", delta_color="inverse")

    if total_charge > 0:
        rate = total_paid / total_charge
        st.progress(min(rate, 1.0), text=f"入金率 {rate*100:.1f}%")

    st.markdown("---")
    st.markdown("#### 区分別")
    df_kbn = pd.DataFrame([
        {
            '区分': '自己負担', '請求額': self_charge, '回収額': self_paid,
            '差': self_charge - self_paid,
            '入金率': f"{(self_paid / self_charge * 100) if self_charge else 0:.1f}%",
        },
        {
            '区分': '国保請求', '請求額': kokuho_charge, '回収額': kokuho_paid,
            '差': kokuho_charge - kokuho_paid,
            '入金率': f"{(kokuho_paid / kokuho_charge * 100) if kokuho_charge else 0:.1f}%",
        },
    ])
    st.dataframe(
        styling.style_records_df(df_kbn),
        width='stretch', hide_index=True,
    )

    if is_fy_mode:
        st.markdown("---")
        st.markdown("#### 月別推移")
        monthly_data = []
        for ym in db.fiscal_year_months(selected_fy):
            ym_records = [r for r in records if r['service_year_month'] == ym]
            s_chg = sum(_self_total_from_record(r) for r in ym_records)
            s_paid_m = sum(r['self_paid_amount'] or 0 for r in ym_records)
            k_chg = sum(_kokuho_total_from_record(r) for r in ym_records)
            k_paid_m = sum(r['kokuho_paid_amount'] or 0 for r in ym_records)
            monthly_data.append({
                '月': ym,
                '件数': len(ym_records),
                '自己負担請求': s_chg,
                '自己負担回収': s_paid_m,
                '国保請求': k_chg,
                '国保回収': k_paid_m,
                '請求合計': s_chg + k_chg,
                '入金合計': s_paid_m + k_paid_m,
                '差': (s_chg + k_chg) - (s_paid_m + k_paid_m),
            })
        monthly_df = pd.DataFrame(monthly_data)

        def red_if_diff(val):
            return 'color:#dc2626; font-weight:700' if val and val != 0 else ''
        styled_monthly = (
            monthly_df.style
            .map(red_if_diff, subset=['差'])
            .format({
                '自己負担請求': '{:,}', '自己負担回収': '{:,}',
                '国保請求': '{:,}', '国保回収': '{:,}',
                '請求合計': '{:,}', '入金合計': '{:,}', '差': '{:,}',
            })
        )
        st.dataframe(styled_monthly, width='stretch', hide_index=True)

    st.markdown("---")
    st.markdown("#### 回収方法別")
    method_agg = defaultdict(lambda: {'count': 0, 'charge': 0, 'paid': 0})
    for r in records:
        m = r['self_payment_method'] or '(未設定)'
        method_agg[('自己負担', m)]['count'] += 1
        method_agg[('自己負担', m)]['charge'] += _self_total_from_record(r)
        method_agg[('自己負担', m)]['paid'] += r['self_paid_amount'] or 0
        m = r['kokuho_payment_method'] or '(未設定)'
        method_agg[('国保請求', m)]['count'] += 1
        method_agg[('国保請求', m)]['charge'] += _kokuho_total_from_record(r)
        method_agg[('国保請求', m)]['paid'] += r['kokuho_paid_amount'] or 0

    df_method = pd.DataFrame([
        {
            '区分': k[0],
            '回収方法': k[1],
            '件数': v['count'],
            '請求額': v['charge'],
            '回収額': v['paid'],
            '差': v['charge'] - v['paid'],
        }
        for k, v in sorted(method_agg.items())
    ])
    st.dataframe(
        styling.style_records_df(df_method),
        width='stretch', hide_index=True,
    )

    if selected_facility_id is None:
        st.markdown("---")
        st.markdown("#### 事業所別")
        fac_agg = defaultdict(lambda: {
            'count': 0,
            'self_chg': 0, 'self_paid': 0,
            'kokuho_chg': 0, 'kokuho_paid': 0,
        })
        for r in records:
            key = (r['facility_short_code'], r['facility_name'])
            fac_agg[key]['count'] += 1
            fac_agg[key]['self_chg'] += _self_total_from_record(r)
            fac_agg[key]['self_paid'] += r['self_paid_amount'] or 0
            fac_agg[key]['kokuho_chg'] += _kokuho_total_from_record(r)
            fac_agg[key]['kokuho_paid'] += r['kokuho_paid_amount'] or 0

        rows_fac = []
        for (code, name), v in sorted(fac_agg.items()):
            total_chg = v['self_chg'] + v['kokuho_chg']
            total_paid_f = v['self_paid'] + v['kokuho_paid']
            rows_fac.append({
                'コード': code,
                '事業所名': name,
                '件数': v['count'],
                '自己負担請求': v['self_chg'],
                '自己負担回収': v['self_paid'],
                '国保請求': v['kokuho_chg'],
                '国保回収': v['kokuho_paid'],
                '請求合計': total_chg,
                '入金合計': total_paid_f,
                '未入金額': total_chg - total_paid_f,
            })
        df_fac = pd.DataFrame(rows_fac)

        def red_if_unpaid(val):
            return 'color:#dc2626; font-weight:700' if val and val != 0 else ''
        styled_fac = (
            df_fac.style
            .map(red_if_unpaid, subset=['未入金額'])
            .format({
                '自己負担請求': '{:,}', '自己負担回収': '{:,}',
                '国保請求': '{:,}', '国保回収': '{:,}',
                '請求合計': '{:,}', '入金合計': '{:,}', '未入金額': '{:,}',
            })
        )
        st.dataframe(styled_fac, width='stretch', hide_index=True)


# ============================================================
# ⑤ 報告確認タブ（管理者専用）
# ============================================================
def _report_rows_from_records(records, report_kbn):
    rows = []
    for r in records:
        if report_kbn in ('すべて', '自己負担') and _as_int(r.get('self_report_to_supervisor')) == 1:
            memo = str(r.get('self_memo') or '').strip()
            if memo:
                charge = _self_total_from_record(r)
                paid = _as_int(r.get('self_paid_amount'))
                rows.append({
                    'サービス提供年月': r.get('service_year_month'),
                    '区分': '自己負担',
                    '施設': f"{r.get('facility_short_code')}: {r.get('facility_name')}",
                    '利用者氏名': r.get('child_name') or '',
                    '受給者証番号': r.get('cert_number') or '',
                    '報告内容': memo,
                    '請求額': charge,
                    '回収額': paid,
                    '差額': charge - paid if (charge > 0 or paid > 0) else 0,
                    'ステータス': _status_from_amounts(charge, paid),
                    '報告更新日': r.get('self_reported_at') or r.get('updated_at') or '',
                })
        if report_kbn in ('すべて', '国保請求') and _as_int(r.get('kokuho_report_to_supervisor')) == 1:
            memo = str(r.get('kokuho_memo') or '').strip()
            if memo:
                charge = _kokuho_total_from_record(r)
                paid = _as_int(r.get('kokuho_paid_amount'))
                rows.append({
                    'サービス提供年月': r.get('service_year_month'),
                    '区分': '国保請求',
                    '施設': f"{r.get('facility_short_code')}: {r.get('facility_name')}",
                    '利用者氏名': r.get('child_name') or '',
                    '受給者証番号': r.get('cert_number') or '',
                    '報告内容': memo,
                    '請求額': charge,
                    '回収額': paid,
                    '差額': charge - paid if (charge > 0 or paid > 0) else 0,
                    'ステータス': _status_from_amounts(charge, paid),
                    '報告更新日': r.get('kokuho_reported_at') or r.get('updated_at') or '',
                })
    rows.sort(key=lambda row: (row['報告更新日'] or '', row['サービス提供年月'] or ''), reverse=True)
    return rows


def render_report_confirmation():
    if not year_months:
        st.info("まだデータがありません。備考を登録して「上司に報告します」にチェックすると、ここに表示されます。")
        return

    st.markdown(
        "自己負担・国保請求の備考欄で「上司に報告します」を選んだ内容だけを確認できます。"
        "備考を修正して保存すると、この画面の内容も最新の備考で上書き表示されます。"
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        ym_options = ['（すべて）'] + year_months
        selected_ym = st.selectbox("サービス提供年月", ym_options, key='report_ym')
        ym_filter = None if selected_ym == '（すべて）' else selected_ym
    with c2:
        facility_options = {"（すべて）": None}
        for f in facilities:
            facility_options[f"{f['short_code']}: {f['facility_name']}"] = f['id']
        selected_facility_label = st.selectbox("施設", list(facility_options.keys()), key='report_fac')
        selected_facility_id = facility_options[selected_facility_label]
    with c3:
        report_kbn = st.selectbox("区分", ['すべて', '自己負担', '国保請求'], key='report_kbn')

    records = db.list_records(service_ym=ym_filter, facility_id=selected_facility_id)
    rows = _report_rows_from_records(records, report_kbn)

    metric_cols = st.columns(3)
    metric_cols[0].metric("報告件数", f"{len(rows)} 件")
    metric_cols[1].metric("自己負担", f"{sum(1 for row in rows if row['区分'] == '自己負担')} 件")
    metric_cols[2].metric("国保請求", f"{sum(1 for row in rows if row['区分'] == '国保請求')} 件")

    if not rows:
        st.info("報告対象の備考はありません。備考を保存するときに「はい、上司に報告します」を選ぶとここに表示されます。")
        return

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        width='stretch',
        hide_index=True,
        height=560,
        column_config={
            '報告内容': st.column_config.TextColumn('報告内容', width='large'),
            '請求額': st.column_config.NumberColumn('請求額', format='localized'),
            '回収額': st.column_config.NumberColumn('回収額', format='localized'),
            '差額': st.column_config.NumberColumn('差額', format='localized'),
        },
    )

    csv = df.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        "報告確認をCSVで出力",
        data=csv,
        file_name=f"報告確認_{date.today().isoformat()}.csv",
        mime='text/csv',
    )


# ============================================================
# タブ呼び出し
# ============================================================
with _top_tabs[0]:
    render_csv_import()
with _top_tabs[1]:
    render_payment_page('self')
with _top_tabs[2]:
    render_payment_page('kokuho')
with _top_tabs[3]:
    render_unpaid()
if _is_admin:
    with _top_tabs[4]:
        render_dashboard()
    with _top_tabs[5]:
        render_report_confirmation()
