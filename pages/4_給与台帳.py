"""給与台帳 / Payroll Ledger
- 医療法人社団EMIFULL / NPO法人EMIFULL の給与CSVを取り込み
- 月次・年度（4〜翌3月）・職員別の集計表示
- 法人別/合算 の年収一覧・基本給の月別推移
- 正社員/パート の判定はマスタで編集可能（自動推定の初期値あり）

【重要】対象月（労働月）と支給月（実際に支払った月）の違い:
- CSVファイル名・集計対象テキストは『支給月』で書かれている
  (例: 2604給与台帳 = 2026年4月支給 = 2026年3月分の労働対価)
- DBには支給月のまま保存し、画面表示は『対象月』を主軸にする
- 賞与は対象月の概念がないため支給月で扱う
"""
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import streamlit as st
import pandas as pd

from lib import db, payroll_parser as pp, styling, auth

styling.inject_global_css()
auth.require_login()
auth.render_sidebar_navigation()

IS_ADMIN = auth.is_admin()
CURRENT_USER = auth.current_user() or {}
CURRENT_EMAIL = (CURRENT_USER.get('email') or '').strip().lower()

st.title("給与台帳")
st.markdown(
    "<p style='color:#64748b; font-size:14px;'>"
    "医療法人社団EMIFULL / NPO法人EMIFULL の給与・賞与を法人別/年度別に管理。"
    "年月は <strong>対象月（労働した月）</strong>で表示します（支給月はその翌月）。"
    "</p>",
    unsafe_allow_html=True,
)


# ============================================================
# 共通ヘルパー
# ============================================================

UPLOAD_TMP_DIR = Path('debug') / 'payroll_uploads'


def _format_yen(v):
    if v is None or pd.isna(v):
        return ''
    try:
        return f"¥{int(v):,}"
    except (ValueError, TypeError):
        return ''


def _ym_label(target_ym: str, pay_ym: str | None = None) -> str:
    """対象月ラベル: '2026-03（4月支給）'"""
    if not target_ym:
        return ''
    if pay_ym is None:
        pay_ym = db.payroll_pay_ym(target_ym)
    py = int(pay_ym[:4])
    pm = int(pay_ym[5:7])
    return f"{target_ym}（{pm}月支給）"


def _bonus_label(pay_ym: str, bonus_round: str | None) -> str:
    """賞与ラベル: '2025-12 冬季賞与'"""
    br = bonus_round or '賞与'
    return f"{pay_ym}（{br}賞与）" if br != '賞与' else f"{pay_ym}（賞与）"


def _color_corp(name):
    if name and 'NPO' in name:
        return 'background-color:#dcfce7; color:#14532d; font-weight:600'
    if name and '奉志会' in name:
        return 'background-color:#f3e8ff; color:#581c87; font-weight:600'
    return 'background-color:#dbeafe; color:#1e3a8a; font-weight:600'


def _color_emp_type(v):
    if v == '正社員':
        return 'background-color:#dbeafe; color:#1e3a8a; font-weight:700'
    if v == 'パート':
        return 'background-color:#fef3c7; color:#92400e; font-weight:600'
    if v == '不明':
        return 'background-color:#fee2e2; color:#991b1b; font-weight:600'
    return 'background-color:#f1f5f9; color:#64748b'


def _color_total_amount(val):
    """計上額の値に応じて色を変える。
    プラス: 青ハイライト（処遇改善対象として正常）
    マイナス: 赤ハイライト（要確認）
    ゼロ: 灰色"""
    try:
        v = int(val)
    except (TypeError, ValueError):
        return ''
    if v < 0:
        return 'background-color:#fee2e2; color:#991b1b; font-weight:700'
    if v > 0:
        return 'background-color:#dbeafe; color:#1e3a8a; font-weight:700'
    return 'background-color:#f1f5f9; color:#64748b; font-weight:600'


def _target_ym_for_record(record: dict) -> str | None:
    if record.get('pay_type') == '給与':
        return db.payroll_target_ym(record.get('year_month'), '給与')
    return record.get('year_month')


def _filter_allowed_records(records: list[dict]) -> list[dict]:
    if IS_ADMIN:
        return records
    allowed_by_month = {}
    filtered = []
    for r in records:
        target_ym = _target_ym_for_record(r)
        if target_ym not in allowed_by_month:
            allowed_by_month[target_ym] = db.list_payroll_allowed_employee_ids(
                CURRENT_EMAIL, target_ym,
            )
        if r.get('employee_id') in allowed_by_month[target_ym]:
            filtered.append(r)
    return filtered


def _filter_allowed_employees(employees: list[dict]) -> list[dict]:
    if IS_ADMIN:
        return employees
    allowed = db.list_payroll_allowed_employee_ids(CURRENT_EMAIL)
    return [e for e in employees if e.get('id') in allowed]


def _import_file(path: Path, info: dict) -> dict:
    """1ファイルを取り込んでDBへ書き込む。結果サマリdictを返す"""
    parsed = pp.parse_csv(path)
    meta = parsed['meta']

    corp_code = pp.resolve_corp_code(info, meta)
    if not corp_code:
        return {'status': 'error', 'message': f"法人を判定できません: {path.name}"}
    # 奉志会はスキップ
    if corp_code == 'HOUSHIKAI':
        return {'status': 'skipped', 'message': f"奉志会(旧法人)は対象外: {path.name}"}

    corp = db.get_payroll_corp_by_code(corp_code)
    if not corp:
        return {'status': 'error', 'message': f"法人マスタにありません: {corp_code}"}

    year_month = meta['year_month'] or info['year_month']
    if not year_month:
        return {'status': 'error', 'message': f"年月を判定できません: {path.name}"}

    period_id = db.upsert_payroll_period(
        corp_id=corp['id'],
        year_month=year_month,
        pay_type=info['pay_type'],
        bonus_round=info['bonus_round'],
        source_filename=path.name,
        source_hash=parsed['source_hash'],
        row_count=len(parsed['employees']),
    )

    inserted = 0
    for emp in parsed['employees']:
        emp_type_guess = pp.guess_employment_type(
            emp['emp_code'], emp['items'], corp_code=corp_code,
        )
        emp_id = db.upsert_payroll_employee(
            corp_id=corp['id'],
            emp_code=emp['emp_code'],
            name=emp['name'],
            department=emp['department'] or None,
            employment_type=emp_type_guess,
        )
        db.insert_payroll_record(
            period_id=period_id,
            employee_id=emp_id,
            department=emp['department'] or None,
            items_dict=emp['items'],
        )
        inserted += 1

    return {
        'status': 'ok',
        'corp_name': corp['name'],
        'pay_year_month': year_month,
        'target_year_month': db.payroll_target_ym(year_month, info['pay_type']),
        'pay_type': info['pay_type'],
        'bonus_round': info['bonus_round'],
        'count': inserted,
    }


def _corp_summary_box(records: list[dict], pay_type_in_label: str = ''):
    """法人別の小サマリを横並びで表示"""
    if not records:
        return
    by_corp = defaultdict(list)
    for r in records:
        by_corp[(r['corp_code'], r['corp_name'])].append(r)

    # 各法人ごとのカード（NPO・医療法人を必ず2枚並べる）
    cols = st.columns(max(2, len(by_corp)))
    items = sorted(by_corp.items(), key=lambda kv: kv[0][0])
    for col, ((code, name), rs) in zip(cols, items):
        emp_ids = {r['employee_id'] for r in rs}
        full = sum(1 for r in rs if r.get('employment_type') == '正社員')
        part = sum(1 for r in rs if r.get('employment_type') == 'パート')
        total = sum((r.get('total_payment') or 0) for r in rs)
        base = sum((r.get('base_salary') or 0) for r in rs)
        with col:
            color = '#16a34a' if 'NPO' in name else '#2563eb'
            st.markdown(
                f"<div style='background:white; border:1px solid #e2e8f0; border-left:6px solid {color};"
                f" border-radius:8px; padding:14px 18px;'>"
                f"<div style='font-size:13px; color:#64748b; font-weight:600;'>{name} {pay_type_in_label}</div>"
                f"<div style='font-size:18px; color:#0f172a; font-weight:700; margin-top:4px;'>"
                f"{len(emp_ids)}名 <span style='font-size:12px; color:#475569; font-weight:600;'>"
                f"（正{full} / パ{part}）</span></div>"
                f"<div style='font-size:13px; color:#64748b; margin-top:6px;'>基本給合計 "
                f"<strong style='color:#0f172a;'>{_format_yen(base)}</strong></div>"
                f"<div style='font-size:13px; color:#64748b;'>総支給合計 "
                f"<strong style='color:#0f172a;'>{_format_yen(total)}</strong></div>"
                f"</div>",
                unsafe_allow_html=True,
            )


def _corp_selector(key, include_combined=True, only_with_data=True):
    """法人セレクタ。戻り値: (corp_id or None, label)
    None = 合算。データの無い法人は除外。"""
    corps = db.list_payroll_corps()
    options = {}
    if include_combined:
        options['🔀 合算（医療法人 + NPO法人）'] = None
    for c in corps:
        if c['code'] == 'HOUSHIKAI':
            continue
        if only_with_data:
            if not db.list_payroll_periods(corp_id=c['id']):
                continue
        icon = '🟢' if c['code'] == 'EMIFULL_NPO' else '🔵'
        options[f"{icon} {c['name']}"] = c['id']
    if not options:
        return None, ''
    label = st.selectbox("法人", list(options.keys()), key=key)
    return options[label], label


# ============================================================
# タブ構成
# ============================================================
tabs = st.tabs([
    "📥 取込",
    "🔐 管理マスタ",
    "📅 月次サマリ",
    "📊 年度サマリ",
    "👤 職員一覧（年収）",
    "📈 職員別 推移",
    "🎯 処遇改善",
    "⏰ 残業管理",
])


# ============================================================
# ① 取込
# ============================================================
with tabs[0]:
    if not IS_ADMIN:
        st.warning('取込は管理者のみ利用できます。')
    else:
        st.markdown(
            "給与台帳CSV（`YYMM給与台帳（XX）.csv`）をこの画面からアップロードして取込みます。\n"
            "- 法人は会社名・ファイル名から自動判定\n"
            "- 賞与（夏季/冬季）も自動判定\n"
            "- 同じ年月・法人・賞与種別で再取込すると上書き\n"
            "- 奉志会(旧法人)の過去データはスキップ\n\n"
            "Dropboxフォルダとの自動紐づけは使いません。過去に取込済みのデータはDBに残っているため、そのまま閲覧できます。"
        )
        st.markdown("##### アップロード取込")
        up = st.file_uploader(
            "給与台帳CSV（複数選択可）", type=['csv'], accept_multiple_files=True,
            key='payroll_uploader',
        )
        if up and st.button("アップロードしたファイルを取込", type='primary',
                              key='import_upload_btn'):
            ok = 0
            errs = []
            for f in up:
                tmp = UPLOAD_TMP_DIR / f.name
                tmp.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_bytes(f.getvalue())
                # ファイル名 → 中身 の順で判定
                info = pp.detect_file(tmp)
                if not info or not info.get('year_month') or not info.get('corp_code'):
                    errs.append(f"給与台帳CSVと判定できません: {f.name}")
                    continue
                try:
                    r = _import_file(tmp, info)
                    if r['status'] == 'ok':
                        ok += 1
                    else:
                        errs.append(r.get('message', '?'))
                except Exception as e:
                    errs.append(f"{f.name}: {e}")
            st.success(f"取込完了: 成功 {ok} / {len(up)}")
            for m in errs:
                st.error(m)
            st.rerun()

        with st.expander("⚙️ メンテナンス"):
            st.markdown("##### 雇用区分の一括再判定")
            st.caption(
                "過去の給与CSVから『基本給の最大値』を集計し、20万円以上を **正社員**、"
                "それ未満を **パート** に一括更新します。手動で雇用区分を編集していた内容は上書きされます。"
            )
            corps_for_reclass = db.list_payroll_corps()
            for corp in corps_for_reclass:
                if corp['code'] == 'HOUSHIKAI':
                    continue
                cnt = len(db.list_payroll_employees(corp_id=corp['id']))
                if cnt == 0:
                    continue
                cc1, cc2 = st.columns([3, 1])
                with cc1:
                    st.markdown(f"**{corp['name']}** （職員 {cnt}名）")
                with cc2:
                    if st.button(f"再判定", key=f'reclass_{corp["code"]}',
                                  type='primary', use_container_width=True):
                        res = db.reclassify_payroll_employees_by_base_salary(
                            corp['id'], threshold=200_000,
                        )
                        st.success(
                            f"{corp['name']}: 正社員 {res['正社員']}名 / "
                            f"パート {res['パート']}名 / 判定不可（基本給0） {res['据え置き']}名"
                        )
                        st.rerun()

            st.markdown("---")
            st.warning("給与データを全削除します（取込テスト用）。")
            confirm = st.checkbox("削除を確認しました", key='wipe_confirm')
            if st.button("🗑️ 給与データ全削除", disabled=not confirm,
                          type='secondary', key='wipe_btn'):
                db.delete_all_payroll()
                st.success("削除しました")
                st.rerun()

        # === 取込済み一覧（取込タブの下部に統合） ===
        st.markdown("---")
        st.markdown("### 🗂️ 取込済み一覧")
        periods = db.list_payroll_periods()
        if not periods:
            st.info("取込履歴はまだありません。")
        else:
            df_p = pd.DataFrame([
                {
                    'id': p['id'],
                    '法人': p['corp_name'],
                    '対象月': db.payroll_target_ym(p['year_month'], p['pay_type']) if p['pay_type'] == '給与' else '-',
                    '支給月': p['year_month'],
                    '区分': p['pay_type'] + (f"({p['bonus_round']})" if p['bonus_round'] else ''),
                    '件数': p['row_count'],
                    'ファイル': p['source_filename'],
                    '取込日時': p['imported_at'],
                }
                for p in periods
            ])
            st.dataframe(
                df_p.drop(columns=['id']).style.map(_color_corp, subset=['法人']),
                width='stretch', hide_index=True, height=400,
            )

            with st.expander("🗑️ 取込済みの個別削除"):
                opts = {
                    f"#{p['id']} [{p['corp_name']}] {p['year_month']} {p['pay_type']}"
                    f"{('('+p['bonus_round']+')') if p['bonus_round'] else ''} - {p['source_filename']}": p['id']
                    for p in periods
                }
                target = st.selectbox("削除対象", list(opts.keys()), key='del_period_pick')
                confirm_d = st.checkbox("削除を確認しました", key='del_period_confirm')
                if st.button("🗑️ 削除", disabled=not confirm_d, key='del_period_btn'):
                    db.delete_payroll_period(opts[target])
                    st.success("削除しました")
                    st.rerun()


# ============================================================
# ② 管理マスタ
# ============================================================
with tabs[1]:
    if not IS_ADMIN:
        st.warning("給与の閲覧権限設定は管理者のみ利用できます。")
    elif not db.list_payroll_target_year_months():
        st.info("まだ給与データがありません。「取込」タブから取込んでください。")
    else:
        st.markdown(
            "対象月ごとに、各管理者が閲覧できる職員を設定します。"
            "新しい対象月は前月の設定を引き継ぎます。前月にいない新職員は未設定として警告します。"
        )
        target_yms = db.list_payroll_target_year_months()
        ym_options = {_ym_label(t): t for t in target_yms}
        picked_label = st.selectbox(
            "対象月（労働月）", list(ym_options.keys()), key='perm_ym'
        )
        perm_ym = ym_options[picked_label]

        managers, employees, perm_map = db.list_payroll_permission_matrix(perm_ym)
        unconfigured = db.list_payroll_unconfigured_employees(perm_ym)

        if unconfigured:
            names = "、".join(
                f"{e['name']}（{e['corp_name']} / {e['emp_code']}）"
                for e in unconfigured[:8]
            )
            more = f" ほか{len(unconfigured) - 8}名" if len(unconfigured) > 8 else ""
            st.warning(f"権限未設定の職員が {len(unconfigured)}名います: {names}{more}")
        else:
            st.success("この対象月の職員は全員、権限設定済みです。")

        if not managers:
            st.warning("ユーザーが登録されていません。先に施設マスタ／設定でユーザーを登録してください。")
        elif not employees:
            st.warning("この対象月の給与職員データがありません。")
        else:
            rows = []
            emp_columns = []
            emp_id_by_col = {}
            for emp in employees:
                col = f"{emp['name']}\n{emp['corp_name']}\n{emp['emp_code']}"
                emp_columns.append(col)
                emp_id_by_col[col] = emp['id']

            for manager in managers:
                email = (manager.get('email') or '').strip().lower()
                display_name = manager.get('name') or manager.get('email')
                row = {
                    '氏名': display_name,
                    'メール': email,
                }
                for col in emp_columns:
                    row[col] = bool(perm_map.get((email, emp_id_by_col[col]), False))
                rows.append(row)

            matrix_df = pd.DataFrame(rows)
            edited = st.data_editor(
                matrix_df,
                width='stretch',
                hide_index=True,
                height=min(640, 120 + 36 * len(matrix_df)),
                column_config={
                    '氏名': st.column_config.TextColumn(disabled=True),
                    'メール': st.column_config.TextColumn(disabled=True),
                    **{
                        col: st.column_config.CheckboxColumn(
                            label=col,
                            help="チェックした管理者だけ、この職員の給与を閲覧できます。",
                        )
                        for col in emp_columns
                    },
                },
                key=f'perm_editor_{perm_ym}',
            )

            if st.button("💾 権限を保存", type='primary', key='perm_save_btn'):
                checked_pairs = []
                manager_emails = []
                employee_ids = list(emp_id_by_col.values())
                for _, row in edited.iterrows():
                    email = str(row['メール']).strip().lower()
                    manager_emails.append(email)
                    for col in emp_columns:
                        if bool(row[col]):
                            checked_pairs.append((email, emp_id_by_col[col]))
                db.save_payroll_permission_matrix(
                    perm_ym, manager_emails, employee_ids, checked_pairs,
                )
                st.success("閲覧権限を保存しました。")
                st.rerun()


# ============================================================
# ③ 月次サマリ
# ============================================================
with tabs[2]:
    if not db.list_payroll_target_year_months():
        st.info("まだ給与データがありません。「取込」タブから取込んでください。")
    else:
        c1, c2, c3 = st.columns([1.5, 1.7, 1])
        with c1:
            corp_id, corp_label = _corp_selector('m_corp')
        with c2:
            target_yms = db.list_payroll_target_year_months(corp_id=corp_id)
            if not target_yms:
                st.warning("該当法人にデータがありません")
                target_ym = None
                pay_ym = None
            else:
                ym_options = {_ym_label(t): t for t in target_yms}
                picked_label = st.selectbox(
                    "対象月（労働月）", list(ym_options.keys()), key='m_ym'
                )
                target_ym = ym_options[picked_label]
                pay_ym = db.payroll_pay_ym(target_ym)
        with c3:
            pay_type_pick = st.selectbox("区分", ['給与のみ', '給与＋賞与（同月支給）'],
                                            key='m_pt')

        if target_ym is None:
            records = []
            bonus_records = []
        else:
            st.caption(f"対象月 **{target_ym}** = 支給月 **{pay_ym}** の給与台帳を表示しています。")

            records = db.list_payroll_records_by_target_ym(
                corp_id=corp_id, target_ym=target_ym, pay_type='給与',
            )
            records = _filter_allowed_records(records)
            bonus_records = []
            if '賞与' in pay_type_pick:
                bonus_records = db.list_payroll_records(
                    corp_id=corp_id, year_month=pay_ym, pay_type='賞与',
                )
                bonus_records = _filter_allowed_records(bonus_records)

        if not records and not bonus_records:
            if target_ym is not None:
                st.warning("該当データがありません")
        else:
            # 法人別ブレイクダウン（給与のみ）
            if records:
                st.markdown("##### 法人別サマリ（給与）")
                _corp_summary_box(records)

            # 全体メトリック
            if records:
                full_time = [r for r in records if r.get('employment_type') == '正社員']
                part_time = [r for r in records if r.get('employment_type') == 'パート']

                def _sum(rs, key):
                    return sum((r[key] or 0) for r in rs)

                st.markdown("##### 全体合計（給与）")
                mc1, mc2, mc3, mc4, mc5 = st.columns(5)
                mc1.metric("人数", f"{len({r['employee_id'] for r in records})}名",
                            f"正 {len(full_time)} / パ {len(part_time)}")
                mc2.metric("基本給 合計", _format_yen(_sum(records, 'base_salary')))
                mc3.metric("総支給 合計", _format_yen(_sum(records, 'total_payment')))
                mc4.metric("控除 合計", _format_yen(_sum(records, 'total_deduction')))
                mc5.metric("差引支給 合計", _format_yen(_sum(records, 'net_payment')))

            if bonus_records:
                st.markdown(f"##### 同月支給の賞与（{pay_ym}）")
                bc1, bc2, bc3 = st.columns(3)
                bc1.metric("対象人数",
                            f"{len({r['employee_id'] for r in bonus_records})}名")
                bc2.metric("賞与 総支給 合計",
                            _format_yen(sum((r['total_payment'] or 0) for r in bonus_records)))
                bc3.metric("賞与 差引支給 合計",
                            _format_yen(sum((r['net_payment'] or 0) for r in bonus_records)))

            st.markdown("##### 詳細一覧")
            f1, f2 = st.columns([2, 3])
            with f1:
                emp_filter = st.multiselect(
                    "雇用区分", ['正社員', 'パート', '不明', '未設定'],
                    default=['正社員', 'パート', '不明', '未設定'],
                    key='m_emp_filter',
                )
            with f2:
                view_mode = st.radio(
                    "表示モード",
                    ['🔍 主要列のみ', '📋 主要列＋手当・保険料', '🗂️ 全項目'],
                    index=1, horizontal=True, key='m_view_mode',
                )

            all_recs = records + bonus_records

            # === DataFrame構築（全項目展開） ===
            base_rows = []
            all_item_keys = set()
            for r in all_recs:
                items = json.loads(r.get('items_json') or '{}')
                items = pp.normalize_items(items)
                row = {
                    '法人': r['corp_name'],
                    '社員番号': r['emp_code'],
                    '氏名': r['emp_name'],
                    '雇用区分': r['employment_type'] or '未設定',
                    '部署': r['department'] or '',
                    '区分': r['pay_type'] + (f"({r['bonus_round']})" if r['bonus_round'] else ''),
                    # 重要列（パッと見えるよう先頭近くに）
                    '総支給': r['total_payment'] or 0,
                    '差引支給': r['net_payment'] or 0,
                    '本給': r['base_salary'] or 0,
                    '控除合計': r['total_deduction'] or 0,
                    '課税支給': r['taxable_payment'] or 0,
                }
                for k, v in items.items():
                    # 既に列に出した重要列はスキップ
                    if k in ('総支給', '差引支給', '本給', '控除合計', '課税支給'):
                        continue
                    if isinstance(v, int):
                        row[k] = v
                        all_item_keys.add(k)
                    else:
                        row[k] = v if v is not None else ''
                        all_item_keys.add(k)
                base_rows.append(row)

            df = pd.DataFrame(base_rows)
            df = df[df['雇用区分'].isin(emp_filter)] if emp_filter else df

            # === 列順を ITEM_GROUPS に従って整える ===
            fixed_left = ['法人', '社員番号', '氏名', '雇用区分', '部署', '区分']
            key_metrics = ['総支給', '差引支給', '本給', '控除合計', '課税支給']

            # グループ毎の項目（重要列以外）を順に並べる
            grouped_remaining = []
            seen = set(key_metrics)
            for group_name, names in pp.ITEM_GROUPS:
                for n in names:
                    if n in df.columns and n not in seen:
                        grouped_remaining.append((group_name, n))
                        seen.add(n)
            # マスタにない項目（その他）
            other_keys = [k for k in df.columns
                          if k not in fixed_left and k not in seen]

            # モード別で見せる列を決定
            if view_mode.startswith('🔍'):
                # 主要列のみ
                visible_cols = fixed_left + key_metrics
            elif view_mode.startswith('📋'):
                # 主要列＋手当・税金・保険
                allow_groups = {'支給手当', '通勤・残業', '社会保険料',
                                '税金', 'その他控除', '賞与', '集計'}
                visible_cols = (
                    fixed_left + key_metrics +
                    [n for g, n in grouped_remaining if g in allow_groups]
                )
            else:
                # 全項目
                visible_cols = (
                    fixed_left + key_metrics +
                    [n for _, n in grouped_remaining] + other_keys
                )

            # 存在する列のみ
            visible_cols = [c for c in visible_cols if c in df.columns]
            df_view = df[visible_cols].copy()

            money_cols = [c for c in visible_cols
                          if c not in fixed_left and isinstance(
                              df_view[c].iloc[0] if len(df_view) > 0 else 0,
                              (int, float)
                          )]

            # === スタイル: 重要列をハイライト、控除/支給で色分け ===
            DEDUCT_COLS = {
                '控除合計', '健康保険', '介護保険', '厚生年金', '雇用保険',
                '所得税', '過不足税額', '住民税', '不足税額',
                '既払い定期代', '前払金控除', 'その他控除', '家賃控除', '清算',
            }

            def _highlight_key(val, col_name=None):
                return ''

            def _fmt_money(v):
                if isinstance(v, (int, float)) and not pd.isna(v):
                    return f"{int(v):,}"
                return str(v) if v else ''

            styler = (
                df_view.style
                .map(_color_corp, subset=['法人'])
                .map(_color_emp_type, subset=['雇用区分'])
            )
            # 重要列を強調（背景色）
            for kc, color in [('総支給', '#fef3c7'), ('差引支給', '#dcfce7')]:
                if kc in df_view.columns:
                    styler = styler.set_properties(
                        subset=[kc],
                        **{'background-color': color, 'font-weight': '700'},
                    )
            # 控除系を薄い赤
            deduct_in_view = [c for c in df_view.columns if c in DEDUCT_COLS]
            if deduct_in_view:
                styler = styler.set_properties(
                    subset=deduct_in_view,
                    **{'background-color': '#fef2f2', 'color': '#7f1d1d'},
                )
            # 数値フォーマット
            styler = styler.format({c: _fmt_money for c in money_cols})

            st.dataframe(styler, width='stretch', hide_index=True, height=560)
            st.caption(
                f"📊 表示列数: **{len(visible_cols)}** ／ "
                f"全項目数: {len(fixed_left) + len(key_metrics) + len(grouped_remaining) + len(other_keys)}"
            )

            csv = df.to_csv(index=False).encode('utf-8-sig')
            label_safe = corp_label.replace('🔀 ', '').replace('🟢 ', '').replace('🔵 ', '')
            st.download_button(
                "💾 全項目CSVダウンロード", csv,
                file_name=f"給与_対象{target_ym}_{label_safe}.csv",
                mime='text/csv',
            )

            # === 個別職員の縦長詳細 ===
            with st.expander("👤 1人ずつ詳細を見る（全項目を縦持ちで表示）"):
                emp_opts = {
                    f"[{r['corp_name']}] {r['emp_code']} {r['emp_name']}": i
                    for i, r in enumerate(all_recs)
                }
                if emp_opts:
                    pick_label = st.selectbox(
                        "職員", list(emp_opts.keys()), key='m_detail_pick'
                    )
                    rec = all_recs[emp_opts[pick_label]]
                    items = json.loads(rec.get('items_json') or '{}')
                    items = pp.normalize_items(items)

                    st.markdown(f"**{rec['emp_name']}**（{rec['corp_name']} / {rec['emp_code']}）")
                    st.caption(
                        f"区分: {rec['pay_type']} "
                        f"{('('+rec['bonus_round']+')') if rec['bonus_round'] else ''} ／ "
                        f"対象月: {db.payroll_target_ym(rec['year_month'], rec['pay_type'])} "
                        f"／ 支給月: {rec['year_month']} ／ 部署: {rec['department'] or '-'}"
                    )

                    # 重要メトリック
                    dc1, dc2, dc3, dc4 = st.columns(4)
                    dc1.metric("総支給", _format_yen(rec['total_payment']))
                    dc2.metric("差引支給", _format_yen(rec['net_payment']))
                    dc3.metric("本給", _format_yen(rec['base_salary']))
                    dc4.metric("控除合計", _format_yen(rec['total_deduction']))

                    # グループ別表示
                    grouped = pp.grouped_items(items)
                    cols_pair = st.columns(2)
                    for i, (gname, pairs) in enumerate(grouped):
                        with cols_pair[i % 2]:
                            st.markdown(f"**{gname}**")
                            sub_df = pd.DataFrame(pairs, columns=['項目', '金額'])
                            sub_df['金額'] = sub_df['金額'].apply(
                                lambda v: f"{v:,}" if isinstance(v, int) else (v or '')
                            )
                            st.dataframe(sub_df, hide_index=True,
                                          width='stretch')


# ============================================================
# ④ 年度サマリ（4月始まり、対象月ベース）
# ============================================================
with tabs[3]:
    if not db.list_payroll_fiscal_years():
        st.info("まだ給与データがありません。")
    else:
        c1, c2, c3 = st.columns([1.5, 1.5, 1])
        with c1:
            corp_id, corp_label = _corp_selector('y_corp')
        with c2:
            fys = db.list_payroll_fiscal_years(corp_id=corp_id)
            if not fys:
                st.warning("該当法人にデータがありません")
                fy = None
            else:
                fy_options = {f"{fy}年度（{fy}/4対象 〜 {fy+1}/3対象）": fy for fy in fys}
                fy_label = st.selectbox("年度（対象月ベース）", list(fy_options.keys()),
                                          key='y_fy')
                fy = fy_options[fy_label]
        with c3:
            include_bonus = st.checkbox("賞与を含む", value=True, key='y_inc_bonus')

        if fy is None:
            records = []
            target_months = []
        else:
            target_months = db.payroll_fiscal_year_months(fy)  # 2025-04..2026-03
            records = db.list_payroll_records_in_target_range(
                corp_id=corp_id,
                start_target_ym=target_months[0],
                end_target_ym=target_months[-1],
                pay_type=None if include_bonus else '給与',
            )
            records = _filter_allowed_records(records)

        if not records:
            st.warning("該当データがありません")
        else:
            # 月次集計（給与は対象月、賞与は支給月そのまま）
            month_agg = defaultdict(lambda: {
                'count': set(), 'base': 0, 'total_pay': 0, 'net': 0,
                'bonus_total': 0, 'bonus_count': 0,
                'med_pay': 0, 'npo_pay': 0,
                'med_bonus': 0, 'npo_bonus': 0,
            })
            for r in records:
                pay_ym = r['year_month']
                if r['pay_type'] == '給与':
                    target_ym = db.payroll_target_ym(pay_ym, '給与')
                    a = month_agg[target_ym]
                    a['count'].add(r['employee_id'])
                    a['base'] += r['base_salary'] or 0
                    a['total_pay'] += r['total_payment'] or 0
                    a['net'] += r['net_payment'] or 0
                    if r['corp_code'] == 'EMIFULL_NPO':
                        a['npo_pay'] += r['total_payment'] or 0
                    else:
                        a['med_pay'] += r['total_payment'] or 0
                else:  # 賞与: 支給月そのまま
                    a = month_agg[pay_ym]
                    a['bonus_total'] += r['total_payment'] or 0
                    a['bonus_count'] += 1
                    if r['corp_code'] == 'EMIFULL_NPO':
                        a['npo_bonus'] += r['total_payment'] or 0
                    else:
                        a['med_bonus'] += r['total_payment'] or 0

            df_month = pd.DataFrame([
                {
                    '対象月': m,
                    '支給月': db.payroll_pay_ym(m),
                    '人数': len(month_agg[m]['count']) if m in month_agg else 0,
                    '基本給合計': month_agg[m]['base'] if m in month_agg else 0,
                    '総支給合計（給与）': month_agg[m]['total_pay'] if m in month_agg else 0,
                    '└医療法人': month_agg[m]['med_pay'] if m in month_agg else 0,
                    '└NPO法人': month_agg[m]['npo_pay'] if m in month_agg else 0,
                    '差引支給合計（給与）': month_agg[m]['net'] if m in month_agg else 0,
                    '賞与合計': month_agg[m]['bonus_total'] if m in month_agg else 0,
                    '賞与人数': month_agg[m]['bonus_count'] if m in month_agg else 0,
                }
                for m in target_months
            ])
            total_row = {
                '対象月': '合計',
                '支給月': '',
                '人数': df_month['人数'].max() if not df_month.empty else 0,
                '基本給合計': int(df_month['基本給合計'].sum()),
                '総支給合計（給与）': int(df_month['総支給合計（給与）'].sum()),
                '└医療法人': int(df_month['└医療法人'].sum()),
                '└NPO法人': int(df_month['└NPO法人'].sum()),
                '差引支給合計（給与）': int(df_month['差引支給合計（給与）'].sum()),
                '賞与合計': int(df_month['賞与合計'].sum()),
                '賞与人数': int(df_month['賞与人数'].sum()),
            }
            df_month = pd.concat([df_month, pd.DataFrame([total_row])], ignore_index=True)

            st.markdown(f"#### {corp_label} - {fy}年度 月次推移")
            st.caption(f"対象月（労働月）{target_months[0]} 〜 {target_months[-1]} ／ 支給月でいうと {db.payroll_pay_ym(target_months[0])} 〜 {db.payroll_pay_ym(target_months[-1])}")

            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("基本給 年合計", _format_yen(total_row['基本給合計']))
            mc2.metric("給与総支給 年合計", _format_yen(total_row['総支給合計（給与）']))
            mc3.metric("賞与 年合計", _format_yen(total_row['賞与合計']))

            money_cols = ['基本給合計', '総支給合計（給与）', '└医療法人', '└NPO法人',
                          '差引支給合計（給与）', '賞与合計']
            styler = df_month.style.format({c: '{:,}' for c in money_cols})
            st.dataframe(styler, width='stretch', hide_index=True, height=520)

            # グラフ
            df_chart = df_month.iloc[:-1].set_index('対象月')[
                ['└医療法人', '└NPO法人', '賞与合計']
            ]
            st.markdown("##### 月別 総支給（法人別）＋賞与")
            st.bar_chart(df_chart, height=320)


# ============================================================
# ⑤ 職員一覧（年収）
# ============================================================
with tabs[4]:
    if not db.list_payroll_fiscal_years():
        st.info("まだ給与データがありません。")
    else:
        c1, c2, c3 = st.columns([1.5, 1.5, 1])
        with c1:
            corp_id, corp_label = _corp_selector('e_corp')
        with c2:
            fys = db.list_payroll_fiscal_years(corp_id=corp_id)
            if not fys:
                st.warning("該当法人にデータがありません")
                fy = None
            else:
                fy_options = {f"{fy}年度": fy for fy in fys}
                fy_label = st.selectbox("年度（対象月ベース）", list(fy_options.keys()),
                                          key='e_fy')
                fy = fy_options[fy_label]
        with c3:
            inc_bonus_e = st.checkbox("賞与を含む", value=True, key='e_inc_bonus')

        if fy is None:
            records = []
        else:
            target_months = db.payroll_fiscal_year_months(fy)
            records = db.list_payroll_records_in_target_range(
                corp_id=corp_id,
                start_target_ym=target_months[0],
                end_target_ym=target_months[-1],
            )
            records = _filter_allowed_records(records)

        if not records:
            if fy is not None:
                st.warning("該当データがありません")
        else:
            agg = defaultdict(lambda: {
                'corp_name': '', 'emp_code': '', 'name': '',
                'employment_type': '', 'department': '',
                'base_total': 0, 'pay_total': 0, 'net_total': 0,
                'bonus_total': 0, 'months_count': 0,
            })
            for r in records:
                key = (r['corp_id'], r['employee_id'])
                a = agg[key]
                a['corp_name'] = r['corp_name']
                a['emp_code'] = r['emp_code']
                a['name'] = r['emp_name']
                a['employment_type'] = r['employment_type'] or '未設定'
                a['department'] = r['department'] or a['department']
                if r['pay_type'] == '給与':
                    a['base_total'] += r['base_salary'] or 0
                    a['pay_total'] += r['total_payment'] or 0
                    a['net_total'] += r['net_payment'] or 0
                    a['months_count'] += 1
                else:
                    a['bonus_total'] += r['total_payment'] or 0

            rows = []
            for (cid, eid), a in agg.items():
                annual = a['pay_total'] + (a['bonus_total'] if inc_bonus_e else 0)
                rows.append({
                    '法人': a['corp_name'],
                    '社員番号': a['emp_code'],
                    '氏名': a['name'],
                    '雇用区分': a['employment_type'],
                    '部署': a['department'],
                    '稼働月数': a['months_count'],
                    '基本給(年合計)': a['base_total'],
                    '給与総支給(年合計)': a['pay_total'],
                    '賞与(年合計)': a['bonus_total'],
                    '年収(総支給+賞与)': annual,
                    '差引支給(年合計)': a['net_total'],
                })
            df = pd.DataFrame(rows)
            df = df.sort_values(['法人', '雇用区分', '年収(総支給+賞与)'],
                                  ascending=[True, True, False])

            # 法人別サマリ（必ず2法人並べる）
            st.markdown("##### 法人別サマリ")
            corps = db.list_payroll_corps()
            cards = st.columns(2)
            for col, corp_target_code in zip(cards, ['EMIFULL_MED', 'EMIFULL_NPO']):
                cm = next((c for c in corps if c['code'] == corp_target_code), None)
                if not cm:
                    continue
                sub = df[df['法人'] == cm['name']]
                color = '#16a34a' if 'NPO' in cm['name'] else '#2563eb'
                with col:
                    st.markdown(
                        f"<div style='background:white; border:1px solid #e2e8f0; border-left:6px solid {color};"
                        f" border-radius:8px; padding:14px 18px;'>"
                        f"<div style='font-size:13px; color:#64748b; font-weight:600;'>{cm['name']}</div>"
                        f"<div style='font-size:18px; color:#0f172a; font-weight:700; margin-top:4px;'>"
                        f"{len(sub)}名 <span style='font-size:12px; color:#475569; font-weight:600;'>"
                        f"（正{(sub['雇用区分']=='正社員').sum()} / "
                        f"パ{(sub['雇用区分']=='パート').sum()}）</span></div>"
                        f"<div style='font-size:13px; color:#64748b; margin-top:6px;'>年間 給与総支給 "
                        f"<strong style='color:#0f172a;'>{_format_yen(sub['給与総支給(年合計)'].sum())}</strong></div>"
                        f"<div style='font-size:13px; color:#64748b;'>年間 賞与 "
                        f"<strong style='color:#0f172a;'>{_format_yen(sub['賞与(年合計)'].sum())}</strong></div>"
                        f"<div style='font-size:13px; color:#64748b;'>年収 合計 "
                        f"<strong style='color:#0f172a;'>{_format_yen(sub['年収(総支給+賞与)'].sum())}</strong></div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

            # フィルタ
            st.markdown("##### 詳細一覧")
            f1, f2, f3 = st.columns(3)
            with f1:
                emp_f = st.multiselect(
                    "雇用区分", ['正社員', 'パート', '不明', '未設定'],
                    default=['正社員', 'パート', '不明', '未設定'],
                    key='e_emp_f',
                )
            with f2:
                kw = st.text_input("氏名検索", key='e_kw')
            with f3:
                only_active = st.checkbox("稼働月のあるスタッフのみ", value=True,
                                            key='e_only_active')

            view = df.copy()
            if emp_f:
                view = view[view['雇用区分'].isin(emp_f)]
            if kw:
                view = view[view['氏名'].str.contains(kw, na=False)]
            if only_active:
                view = view[view['稼働月数'] > 0]

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("対象人数", f"{len(view)}名")
            mc2.metric("年間 基本給合計", _format_yen(view['基本給(年合計)'].sum()))
            mc3.metric("年間 給与総支給", _format_yen(view['給与総支給(年合計)'].sum()))
            mc4.metric("年間 賞与", _format_yen(view['賞与(年合計)'].sum()))

            money_cols = ['基本給(年合計)', '給与総支給(年合計)', '賞与(年合計)',
                          '年収(総支給+賞与)', '差引支給(年合計)']
            styler = (
                view.style
                .map(_color_corp, subset=['法人'])
                .map(_color_emp_type, subset=['雇用区分'])
                .format({c: '{:,}' for c in money_cols})
                .background_gradient(subset=['年収(総支給+賞与)'], cmap='YlGn')
            )
            st.dataframe(styler, width='stretch', hide_index=True, height=560)

            csv = view.to_csv(index=False).encode('utf-8-sig')
            label_safe = corp_label.replace('🔀 ', '').replace('🟢 ', '').replace('🔵 ', '')
            st.download_button(
                "💾 CSVダウンロード", csv,
                file_name=f"職員年収_{fy}年度_{label_safe}.csv",
                mime='text/csv',
            )

            # 職員マスタ編集
            st.markdown("---")
            st.markdown("##### 職員マスタ - 雇用区分の編集")
            st.caption("社員番号からの自動推定は完璧ではありません。実態に合わせてここで修正してください（再取込しても保持されます）。")

            employees = db.list_payroll_employees(corp_id=corp_id) if IS_ADMIN else []
            if employees:
                emp_df = pd.DataFrame([
                    {
                        'id': e['id'],
                        '法人': e['corp_name'],
                        '社員番号': e['emp_code'],
                        '氏名': e['name'],
                        '雇用区分': e['employment_type'] or '未設定',
                        '部署': e['department'] or '',
                        '備考': e['note'] or '',
                    }
                    for e in employees
                ])
                edited = st.data_editor(
                    emp_df,
                    width='stretch', hide_index=True, num_rows='fixed',
                    column_config={
                        'id': None,
                        '法人': st.column_config.TextColumn(disabled=True),
                        '社員番号': st.column_config.TextColumn(disabled=True),
                        '氏名': st.column_config.TextColumn(disabled=True),
                        '部署': st.column_config.TextColumn(disabled=True),
                        '雇用区分': st.column_config.SelectboxColumn(
                            options=['正社員', 'パート', '不明', '未設定']
                        ),
                        '備考': st.column_config.TextColumn(),
                    },
                    key='emp_editor',
                )
                if st.button("💾 雇用区分を保存", type='primary', key='emp_save_btn'):
                    n = 0
                    for orig, new in zip(emp_df.itertuples(index=False),
                                           edited.itertuples(index=False)):
                        if orig._asdict() != new._asdict():
                            new_type = getattr(new, '雇用区分')
                            new_note = getattr(new, '備考')
                            db.update_payroll_employee(
                                getattr(new, 'id'),
                                employment_type=None if new_type == '未設定' else new_type,
                                note=new_note,
                            )
                            n += 1
                    st.success(f"{n}名分を更新しました")
                    st.rerun()


# ============================================================
# ⑥ 職員別 推移
# ============================================================
with tabs[5]:
    employees_all = db.list_payroll_employees()
    if not employees_all:
        st.info("職員マスタがまだありません。")
    else:
        # === 検索 / フィルタ ===
        f1, f2, f3, f4 = st.columns([1.5, 1.5, 1, 1])
        with f1:
            corp_filter = st.selectbox(
                "法人",
                ['すべて', '医療法人社団EMIFULL', 'NPO法人EMIFULL'],
                key='per_corp_filter',
            )
        with f2:
            kw = st.text_input(
                "🔍 氏名検索",
                placeholder="例: 見谷 / みや / 410001",
                help="氏名・社員番号・部署のいずれかに部分一致",
                key='per_kw',
            )
        with f3:
            type_filter = st.multiselect(
                "雇用区分", ['正社員', 'パート', '不明', '未設定'],
                default=['正社員', 'パート', '不明', '未設定'],
                key='per_type_filter',
            )
        with f4:
            inc_bonus_p = st.checkbox("賞与を含む", value=True, key='per_inc_bonus')

        # フィルタ適用
        filtered = _filter_allowed_employees(employees_all)
        if corp_filter != 'すべて':
            filtered = [e for e in filtered if e['corp_name'] == corp_filter]
        if type_filter:
            filtered = [
                e for e in filtered
                if (e['employment_type'] or '未設定') in type_filter
            ]
        if kw:
            kw_lower = kw.lower().strip()
            kw_normalized = kw.replace('　', ' ').replace(' ', '')
            def _match(e):
                name = (e['name'] or '').replace('　', '').replace(' ', '')
                code = (e['emp_code'] or '').lower()
                dept = (e['department'] or '')
                return (
                    kw_normalized in name
                    or kw_lower in code
                    or kw in dept
                )
            filtered = [e for e in filtered if _match(e)]

        st.caption(f"該当: **{len(filtered)}名** / 全{len(_filter_allowed_employees(employees_all))}名")

        if not filtered:
            st.warning("該当する職員が見つかりませんでした。検索条件を緩めてください。")
            st.stop()

        # 検索結果が1名なら自動選択、複数なら選択ボックス
        opts = {
            f"[{e['corp_name']}] {e['emp_code']} {e['name']}"
            f" {('('+e['employment_type']+')') if e['employment_type'] else ''}": e['id']
            for e in filtered
        }
        label = st.selectbox("職員を選択", list(opts.keys()), key='per_emp_pick')
        emp_id = opts[label]

        records = db.list_payroll_records(employee_id=emp_id)
        records = _filter_allowed_records(records)
        if not records:
            st.warning("データなし")
        else:
            df = pd.DataFrame([
                {
                    '対象月': db.payroll_target_ym(r['year_month'], '給与') if r['pay_type'] == '給与' else r['year_month'],
                    '支給月': r['year_month'],
                    '区分': r['pay_type'] + (f"({r['bonus_round']})" if r['bonus_round'] else ''),
                    '本給/基本給': r['base_salary'] or 0,
                    '総支給': r['total_payment'] or 0,
                    '差引支給': r['net_payment'] or 0,
                    '部署': r['department'] or '',
                }
                for r in records
            ]).sort_values(['対象月', '区分'])

            mc1, mc2, mc3 = st.columns(3)
            base_sum = df[df['区分'] == '給与']['本給/基本給'].sum()
            pay_sum = df[df['区分'] == '給与']['総支給'].sum()
            bonus_sum = df[df['区分'].str.startswith('賞与')]['総支給'].sum()
            mc1.metric("基本給 累計", _format_yen(base_sum))
            mc2.metric("給与総支給 累計", _format_yen(pay_sum))
            mc3.metric("賞与 累計", _format_yen(bonus_sum))

            money_cols = ['本給/基本給', '総支給', '差引支給']
            styler = df.style.format({c: '{:,}' for c in money_cols})
            st.dataframe(styler, width='stretch', hide_index=True)

            df_g = df[df['区分'] == '給与'].set_index('対象月')[
                ['本給/基本給', '総支給']
            ]
            if not df_g.empty:
                st.markdown("##### 月次推移（給与のみ／対象月ベース）")
                st.line_chart(df_g, height=320)

            if inc_bonus_p and not df[df['区分'].str.startswith('賞与')].empty:
                st.markdown("##### 賞与履歴")
                df_b = df[df['区分'].str.startswith('賞与')][['支給月', '区分', '総支給']]
                st.dataframe(
                    df_b.style.format({'総支給': '{:,}'}),
                    width='stretch', hide_index=True,
                )


# ============================================================
# ⑦ 処遇改善
# ============================================================
with tabs[6]:
    if not IS_ADMIN:
        st.warning('処遇改善は管理者のみ閲覧できます。')
    else:
        st.markdown(
            "国に処遇改善として計上できる金額を算出します。\n"
            "**計算式: ①最低賃金との差額 + ②役職・職位手当 + ③資格手当 + ④処遇改善 + "
            "⑤業務手当 + ⑥インセンティブ + ⑦医療費補助**\n\n"
            "- 部署が **てんり/天理** を含む → 奈良県の最低賃金、それ以外 → 兵庫県の最低賃金\n"
            "- **正社員**: 最低賃金時給 × 176時間（実出勤が176h未満なら実時間で按分）\n"
            "- **パート**: 最低賃金時給 × 各月の実出勤時間\n"
            "- ②役職・職位手当 = 「役職手当」+「職位手当」+「役職・職位手当」の合算\n"
            "- ④処遇改善 = 「処遇改善手当」+「処遇改善金」の合算\n"
            "- ⑥インセンティブ = 「インセンティブ」+「その他手当」の合算\n"
            "- 最低賃金は適用開始日に応じて自動切替（毎年10月頃に新賃金を登録すれば過去データも含めて再計算）"
        )

        if not db.list_payroll_fiscal_years():
            st.info("給与データがまだありません。")
        else:
            # === 最低賃金マスタ（折りたたみ） ===
            with st.expander("⚙️ 最低賃金マスタの編集（毎年10月の改定時に追加）"):
                mw_list = db.list_minimum_wages()
                if mw_list:
                    df_mw = pd.DataFrame([
                        {
                            'id': r['id'],
                            '都道府県': r['prefecture'],
                            '適用開始日': r['effective_from'],
                            '時給(円)': r['hourly_wage'],
                        }
                        for r in mw_list
                    ])
                    st.dataframe(df_mw.drop(columns=['id']),
                                  width='stretch', hide_index=True)

                st.markdown("##### 新規登録 / 更新")
                wc1, wc2, wc3, wc4 = st.columns([1, 1, 1, 1])
                with wc1:
                    add_pref = st.selectbox("都道府県", ['兵庫県', '奈良県'],
                                              key='mw_pref')
                with wc2:
                    add_eff = st.date_input("適用開始日",
                                              value=None, key='mw_eff',
                                              help="例: 2025-10-01")
                with wc3:
                    add_wage = st.number_input("時給(円)", min_value=500, max_value=3000,
                                                  step=1, value=1100, key='mw_wage')
                with wc4:
                    st.write("")
                    st.write("")
                    if st.button("💾 登録/更新", type='primary',
                                  key='mw_save_btn',
                                  use_container_width=True):
                        if not add_eff:
                            st.error("適用開始日を入力してください")
                        else:
                            db.upsert_minimum_wage(
                                add_pref, add_eff.isoformat(), int(add_wage)
                            )
                            st.success(
                                f"{add_pref} {add_eff} {add_wage}円 を登録しました"
                            )
                            st.rerun()

                st.markdown("##### 削除")
                if mw_list:
                    del_opts = {
                        f"{r['prefecture']} {r['effective_from']} {r['hourly_wage']}円": r['id']
                        for r in mw_list
                    }
                    del_label = st.selectbox("削除対象", list(del_opts.keys()),
                                                key='mw_del_pick')
                    if st.button("🗑️ 削除", key='mw_del_btn'):
                        db.delete_minimum_wage(del_opts[del_label])
                        st.success("削除しました")
                        st.rerun()

            # === 計算条件 ===
            st.markdown("---")
            c1, c2, c3 = st.columns([1.5, 1.5, 1])
            with c1:
                corp_id, corp_label = _corp_selector('sk_corp')
            with c2:
                fys = db.list_payroll_fiscal_years(corp_id=corp_id)
                if not fys:
                    st.warning("該当法人にデータがありません")
                    fy = None
                else:
                    fy_options = {f"{fy}年度": fy for fy in fys}
                    fy_label = st.selectbox("年度（対象月ベース）",
                                              list(fy_options.keys()), key='sk_fy')
                    fy = fy_options[fy_label]
            with c3:
                view_mode_sk = st.selectbox(
                    "表示", ['月別×職員', '月別合計', '職員別 年計'],
                    key='sk_view',
                )

            if fy is None:
                st.stop()

            target_months = db.payroll_fiscal_year_months(fy)
            # 給与のみ対象（賞与は処遇改善計算には入れない）
            records = db.list_payroll_records_in_target_range(
                corp_id=corp_id,
                start_target_ym=target_months[0],
                end_target_ym=target_months[-1],
                pay_type='給与',
            )
            records = _filter_allowed_records(records)

            if not records:
                st.warning("対象データがありません")
            else:
                # 各レコードに処遇改善計算を適用
                calc_rows = []
                for r in records:
                    calc = db.calc_shogu_kaizen_for_record(r)
                    target_ym_r = db.payroll_target_ym(r['year_month'], '給与')
                    shortened = (
                        (r['employment_type'] == '正社員')
                        and calc['基準時間'] < db.FULLTIME_STANDARD_HOURS
                    )
                    calc_rows.append({
                        '対象月': target_ym_r,
                        '支給月': r['year_month'],
                        '法人': r['corp_name'],
                        '社員番号': r['emp_code'],
                        '氏名': r['emp_name'],
                        '雇用区分': r['employment_type'] or '未設定',
                        '部署': r['department'] or '',
                        '都道府県': calc['都道府県'],
                        '時給': calc['最低賃金時給'] or 0,
                        '基準時間': calc['基準時間'],
                        '時間根拠': calc['基準時間根拠'],
                        '時間短縮': '⚠️' if shortened else '',
                        '最低賃金月額': calc['最低賃金月額'],
                        '本給': calc['本給'],
                        '①差額': calc['差額①(本給-最低賃金)'],
                        '②役職・職位': calc['役職・職位手当②'],
                        '③資格': calc['資格手当③'],
                        '④処遇改善': calc['処遇改善④'],
                        '⑤業務手当': calc['業務手当⑤'],
                        '⑥インセンティブ': calc['インセンティブ⑥'],
                        '⑦医療費補助': calc['医療費補助⑦'],
                        '計上額': calc['処遇改善計上額'],
                    })
                df_calc = pd.DataFrame(calc_rows)

                # === 全体メトリック ===
                st.markdown("##### 年度合計")
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("①差額 合計", _format_yen(int(df_calc['①差額'].sum())))
                mc2.metric("②役職・職位", _format_yen(int(df_calc['②役職・職位'].sum())))
                mc3.metric("③資格手当", _format_yen(int(df_calc['③資格'].sum())))
                mc4.metric("④処遇改善", _format_yen(int(df_calc['④処遇改善'].sum())))
                mc5, mc6, mc7, mc8 = st.columns(4)
                mc5.metric("⑤業務手当", _format_yen(int(df_calc['⑤業務手当'].sum())))
                mc6.metric("⑥インセンティブ",
                            _format_yen(int(df_calc['⑥インセンティブ'].sum())))
                mc7.metric("⑦医療費補助", _format_yen(int(df_calc['⑦医療費補助'].sum())))
                mc8.metric(
                    "💎 計上額 合計",
                    _format_yen(int(df_calc['計上額'].sum())),
                    help="処遇改善として国に計上する金額（①+②+③+④+⑤+⑥+⑦）",
                )

                # 警告: 最低賃金割れ
                below = df_calc[df_calc['①差額'] < 0]
                if not below.empty:
                    st.warning(
                        f"⚠️ 最低賃金を下回っているレコードが **{len(below)}件** あります"
                        "（差額がマイナス）。雇用区分や部署の設定を確認してください。"
                    )

                # 情報: 正社員の時間短縮（休職・欠勤等）
                shortened = df_calc[
                    (df_calc['雇用区分'] == '正社員')
                    & (df_calc['時間短縮'] == '⚠️')
                ]
                if not shortened.empty:
                    st.info(
                        f"ℹ️ 正社員で実出勤時間が **{db.FULLTIME_STANDARD_HOURS}h 未満** だった "
                        f"レコードが **{len(shortened)}件** あります "
                        f"（休職・欠勤・早退・短時間勤務等）。"
                        f"これらは実出勤時間ベースで最低賃金月額を再計算しています。"
                    )

                # === 表示 ===
                ALLOWANCE_COLS = ['②役職・職位', '③資格', '④処遇改善',
                                  '⑤業務手当', '⑥インセンティブ', '⑦医療費補助']
                money_cols_sk = ['最低賃金月額', '本給', '①差額'] + ALLOWANCE_COLS + ['計上額']

                if view_mode_sk == '月別×職員':
                    st.markdown("##### 月別×職員 一覧")
                    f1, f2 = st.columns(2)
                    with f1:
                        emp_f_sk = st.multiselect(
                            "雇用区分", ['正社員', 'パート', '不明', '未設定'],
                            default=['正社員', 'パート', '不明', '未設定'],
                            key='sk_emp_f',
                        )
                    with f2:
                        pref_f = st.multiselect(
                            "都道府県", ['兵庫県', '奈良県'],
                            default=['兵庫県', '奈良県'], key='sk_pref_f',
                        )

                    view = df_calc.copy()
                    if emp_f_sk:
                        view = view[view['雇用区分'].isin(emp_f_sk)]
                    if pref_f:
                        view = view[view['都道府県'].isin(pref_f)]

                    view = view.sort_values(['対象月', '法人', '雇用区分', '社員番号'])
                    view_disp = view.copy()
                    view_disp['基準時間'] = view_disp['基準時間'].apply(lambda x: f"{x:.1f}h")

                    styler = (
                        view_disp.style
                        .map(_color_corp, subset=['法人'])
                        .map(_color_emp_type, subset=['雇用区分'])
                        .format({c: '{:,}' for c in money_cols_sk + ['時給']})
                    )
                    styler = styler.map(_color_total_amount, subset=['計上額'])

                    def _warn_below(val):
                        try:
                            if int(val) < 0:
                                return 'background-color:#fee2e2; color:#991b1b; font-weight:700'
                        except (TypeError, ValueError):
                            pass
                        return ''
                    styler = styler.map(_warn_below, subset=['①差額'])

                    st.dataframe(styler, width='stretch', hide_index=True, height=560)

                    csv = view.to_csv(index=False).encode('utf-8-sig')
                    label_safe = corp_label.replace('🔀 ', '').replace('🟢 ', '').replace('🔵 ', '')
                    st.download_button(
                        "💾 月別×職員 CSV", csv,
                        file_name=f"処遇改善_{fy}年度_{label_safe}.csv",
                        mime='text/csv',
                    )

                elif view_mode_sk == '月別合計':
                    st.markdown("##### 月別合計（年度推移）")
                    agg_dict = {c: 'sum' for c in
                                ['①差額'] + ALLOWANCE_COLS + ['計上額']}
                    agg_dict['氏名'] = 'count'
                    month_agg = (
                        df_calc.groupby('対象月').agg(agg_dict)
                        .rename(columns={'氏名': '対象人数'})
                        .reset_index()
                        .sort_values('対象月')
                    )
                    full = pd.DataFrame({'対象月': target_months})
                    month_agg = full.merge(month_agg, on='対象月', how='left').fillna(0)
                    # 年度順（4月→翌3月）で表示
                    month_agg = month_agg.sort_values('対象月').reset_index(drop=True)
                    # 合計行（先頭）
                    total_row = {
                        '対象月': '合計',
                        '対象人数': int(month_agg['対象人数'].max() if len(month_agg) else 0),
                    }
                    for c in ['①差額'] + ALLOWANCE_COLS + ['計上額']:
                        total_row[c] = int(month_agg[c].sum())
                    month_agg = pd.concat(
                        [pd.DataFrame([total_row]), month_agg], ignore_index=True
                    )
                    for c in ['①差額'] + ALLOWANCE_COLS + ['計上額']:
                        month_agg[c] = month_agg[c].astype(int)

                    fmt_cols = ['①差額'] + ALLOWANCE_COLS + ['計上額']
                    styler = month_agg.style.format({c: '{:,}' for c in fmt_cols})
                    styler = styler.map(_color_total_amount, subset=['計上額'])
                    styler = styler.map(
                        lambda v: 'background-color:#fee2e2; color:#991b1b; font-weight:700'
                        if isinstance(v, (int, float)) and v < 0 else '',
                        subset=['①差額'],
                    )
                    st.dataframe(styler, width='stretch', hide_index=True)

                    # グラフは時系列順（昇順）で見やすく
                    df_chart = (
                        month_agg[month_agg['対象月'] != '合計']
                        .sort_values('対象月')
                        .set_index('対象月')[['①差額'] + ALLOWANCE_COLS]
                    )
                    st.markdown("##### 月別 内訳")
                    st.bar_chart(df_chart, height=320)

                else:  # 職員別 年計
                    st.markdown("##### 職員別 年計")
                    agg_dict = {c: 'sum' for c in
                                ['①差額'] + ALLOWANCE_COLS + ['計上額']}
                    agg_dict['対象月'] = 'count'
                    emp_agg = (
                        df_calc.groupby([
                            '法人', '社員番号', '氏名', '雇用区分', '都道府県'
                        ]).agg(agg_dict)
                        .rename(columns={'対象月': '稼働月数'})
                        .reset_index()
                        .sort_values(['法人', '計上額'], ascending=[True, False])
                    )
                    fmt_cols = ['①差額'] + ALLOWANCE_COLS + ['計上額']
                    styler = (
                        emp_agg.style
                        .map(_color_corp, subset=['法人'])
                        .map(_color_emp_type, subset=['雇用区分'])
                        .format({c: '{:,}' for c in fmt_cols})
                    )
                    styler = styler.map(_color_total_amount, subset=['計上額'])
                    styler = styler.map(
                        lambda v: 'background-color:#fee2e2; color:#991b1b; font-weight:700'
                        if isinstance(v, (int, float)) and v < 0 else '',
                        subset=['①差額'],
                    )
                    st.dataframe(styler, width='stretch', hide_index=True, height=560)

                    csv = emp_agg.to_csv(index=False).encode('utf-8-sig')
                    label_safe = corp_label.replace('🔀 ', '').replace('🟢 ', '').replace('🔵 ', '')
                    st.download_button(
                        "💾 職員別年計 CSV", csv,
                        file_name=f"処遇改善_職員別{fy}年度_{label_safe}.csv",
                        mime='text/csv',
                    )

# ============================================================
# ⑧ 残業管理
# ============================================================
with tabs[7]:
    st.markdown(
        "残業時間と残業手当の集計。月次／年次で確認できます。\n\n"
        "- **残業時間** = 普通残業＋深夜残業＋休出残業＋早出残業（法定内残業は別カラム）\n"
        "- **残業金額** = 残業手当＋定額時間外手当\n"
        "- 時間は `HH:MM` を時間（小数）に変換して集計"
    )

    if not db.list_payroll_fiscal_years():
        st.info("給与データがまだありません。")
    else:
        c1, c2, c3 = st.columns([1.5, 1.5, 1])
        with c1:
            corp_id_o, corp_label_o = _corp_selector('ot_corp')
        with c2:
            fys_o = db.list_payroll_fiscal_years(corp_id=corp_id_o)
            if not fys_o:
                st.warning("該当法人にデータがありません")
                fy_o = None
            else:
                fy_options_o = {f"{fy}年度": fy for fy in fys_o}
                fy_label_o = st.selectbox(
                    "年度（対象月ベース）", list(fy_options_o.keys()),
                    key='ot_fy',
                )
                fy_o = fy_options_o[fy_label_o]
        with c3:
            view_mode_o = st.selectbox(
                "表示",
                [
                    '月別合計',
                    '月別×職員',
                    '職員別 年計',
                    '👤 職員個別 推移',
                    '🏢 施設別 月別',
                    '🏢 施設別 年計',
                ],
                key='ot_view',
            )

        if fy_o is None:
            st.stop()

        target_months_o = db.payroll_fiscal_year_months(fy_o)
        records_o = db.list_payroll_records_in_target_range(
            corp_id=corp_id_o,
            start_target_ym=target_months_o[0],
            end_target_ym=target_months_o[-1],
            pay_type='給与',
        )
        records_o = _filter_allowed_records(records_o)

        if not records_o:
            st.warning("対象データがありません")
        else:
            ot_rows = []
            for r in records_o:
                ot = db.collect_overtime_for_record(r)
                target_ym_r = db.payroll_target_ym(r['year_month'], '給与')
                ot_rows.append({
                    '対象月': target_ym_r,
                    '支給月': r['year_month'],
                    '法人': r['corp_name'],
                    '社員番号': r['emp_code'],
                    '氏名': r['emp_name'],
                    '雇用区分': r['employment_type'] or '未設定',
                    '部署': r['department'] or '',
                    '法定内残業(h)': round(ot['法定内残業時間'], 2),
                    '普通残業(h)': round(ot['普通残業時間'], 2),
                    '深夜残業(h)': round(ot['深夜残業時間'], 2),
                    '休出残業(h)': round(ot['休出残業時間'], 2),
                    '早出残業(h)': round(ot['早出残業時間'], 2),
                    '残業時間合計(h)': round(ot['残業時間合計(法定外のみ)'], 2),
                    '残業手当(円)': ot['残業手当'],
                    '定額時間外(円)': ot['定額時間外手当'],
                    '残業金額合計(円)': ot['残業金額合計'],
                })
            df_ot = pd.DataFrame(ot_rows)

            total_h = df_ot['残業時間合計(h)'].sum()
            total_y = df_ot['残業金額合計(円)'].sum()
            ot_employees = df_ot[df_ot['残業時間合計(h)'] > 0]['氏名'].nunique()

            st.markdown("##### 年度合計")
            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("残業時間 年合計", f"{total_h:.1f} h")
            mc2.metric("残業金額 年合計", _format_yen(int(total_y)))
            mc3.metric("残業発生 職員数", f"{ot_employees}名")
            avg = (total_y / total_h) if total_h > 0 else 0
            mc4.metric("平均時給単価", _format_yen(int(avg)),
                        help="残業金額合計 ÷ 残業時間合計")

            time_cols = ['法定内残業(h)', '普通残業(h)', '深夜残業(h)',
                         '休出残業(h)', '早出残業(h)', '残業時間合計(h)']
            money_cols_ot = ['残業手当(円)', '定額時間外(円)', '残業金額合計(円)']

            if view_mode_o == '月別×職員':
                st.markdown("##### 月別×職員 一覧")
                f1, f2, f3 = st.columns([2, 2, 1])
                with f1:
                    emp_f_o = st.multiselect(
                        "雇用区分", ['正社員', 'パート', '不明', '未設定'],
                        default=['正社員', 'パート', '不明', '未設定'],
                        key='ot_emp_f',
                    )
                with f2:
                    # 単月絞り込み（オプション）
                    month_options = ['（年度全月）'] + target_months_o
                    month_pick = st.selectbox(
                        "単月で絞り込む", month_options, key='ot_month_pick',
                    )
                with f3:
                    only_ot = st.checkbox("残業発生のみ", value=True,
                                            key='ot_only')

                view = df_ot.copy()
                if emp_f_o:
                    view = view[view['雇用区分'].isin(emp_f_o)]
                if month_pick != '（年度全月）':
                    view = view[view['対象月'] == month_pick]
                if only_ot:
                    view = view[
                        (view['残業時間合計(h)'] > 0)
                        | (view['残業金額合計(円)'] > 0)
                    ]
                view = view.sort_values(
                    ['対象月', '法人', '残業時間合計(h)'],
                    ascending=[True, True, False],
                )

                fmt_ot = {c: '{:.2f}' for c in time_cols}
                fmt_ot.update({c: '{:,}' for c in money_cols_ot})
                styler = (
                    view.style
                    .map(_color_corp, subset=['法人'])
                    .map(_color_emp_type, subset=['雇用区分'])
                    .format(fmt_ot)
                )
                styler = styler.set_properties(
                    subset=['残業時間合計(h)'],
                    **{'background-color': '#fef3c7', 'font-weight': '700'},
                )
                styler = styler.set_properties(
                    subset=['残業金額合計(円)'],
                    **{'background-color': '#dbeafe', 'font-weight': '700'},
                )
                st.dataframe(styler, width='stretch', hide_index=True, height=560)

                csv = view.to_csv(index=False).encode('utf-8-sig')
                label_safe = corp_label_o.replace('🔀 ', '').replace('🟢 ', '').replace('🔵 ', '')
                st.download_button(
                    "💾 月別×職員 CSV", csv,
                    file_name=f"残業_{fy_o}年度_{label_safe}.csv",
                    mime='text/csv',
                )

            elif view_mode_o == '月別合計':
                st.markdown("##### 月別合計（年度推移）")
                agg_o = (
                    df_ot.groupby('対象月')
                    .agg({
                        '法定内残業(h)': 'sum',
                        '普通残業(h)': 'sum',
                        '深夜残業(h)': 'sum',
                        '休出残業(h)': 'sum',
                        '早出残業(h)': 'sum',
                        '残業時間合計(h)': 'sum',
                        '残業手当(円)': 'sum',
                        '定額時間外(円)': 'sum',
                        '残業金額合計(円)': 'sum',
                        '氏名': 'nunique',
                    })
                    .rename(columns={'氏名': '残業発生 人数'})
                    .reset_index()
                )
                full_o = pd.DataFrame({'対象月': target_months_o})
                agg_o = full_o.merge(agg_o, on='対象月', how='left').fillna(0)
                # 年度順（4月→翌3月）で表示
                agg_o = agg_o.sort_values('対象月').reset_index(drop=True)
                # 合計行（先頭）
                total_row_o = {'対象月': '合計', '残業発生 人数': '-'}
                for c in time_cols + money_cols_ot:
                    total_row_o[c] = float(agg_o[c].sum())
                agg_with_total = pd.concat(
                    [pd.DataFrame([total_row_o]), agg_o], ignore_index=True
                )

                fmt_ot = {c: '{:.2f}' for c in time_cols}
                fmt_ot.update({c: '{:,.0f}' for c in money_cols_ot})
                styler = agg_with_total.style.format(fmt_ot)
                styler = styler.set_properties(
                    subset=['残業時間合計(h)'],
                    **{'background-color': '#fef3c7', 'font-weight': '700'},
                )
                styler = styler.set_properties(
                    subset=['残業金額合計(円)'],
                    **{'background-color': '#dbeafe', 'font-weight': '700'},
                )
                st.dataframe(styler, width='stretch', hide_index=True)

                # グラフは時系列順（昇順）で見やすく
                agg_chart = agg_o.sort_values('対象月')
                st.markdown("##### 月別 残業時間")
                df_ch_h = agg_chart.set_index('対象月')[
                    ['普通残業(h)', '深夜残業(h)', '休出残業(h)', '早出残業(h)']
                ]
                st.bar_chart(df_ch_h, height=300)

                st.markdown("##### 月別 残業金額")
                df_ch_y = agg_chart.set_index('対象月')[
                    ['残業手当(円)', '定額時間外(円)']
                ]
                st.bar_chart(df_ch_y, height=300)

            elif view_mode_o == '職員別 年計':
                st.markdown("##### 職員別 年計")
                emp_agg_o = (
                    df_ot.groupby([
                        '法人', '社員番号', '氏名', '雇用区分', '部署',
                    ])
                    .agg({c: 'sum' for c in time_cols + money_cols_ot})
                    .reset_index()
                    .sort_values(['法人', '残業時間合計(h)'],
                                  ascending=[True, False])
                )
                f1, _ = st.columns([1, 3])
                with f1:
                    only_ot_y = st.checkbox(
                        "残業発生のあった職員のみ", value=True, key='ot_emp_only',
                    )
                if only_ot_y:
                    emp_agg_o = emp_agg_o[emp_agg_o['残業時間合計(h)'] > 0]

                fmt_ot = {c: '{:.2f}' for c in time_cols}
                fmt_ot.update({c: '{:,}' for c in money_cols_ot})
                styler = (
                    emp_agg_o.style
                    .map(_color_corp, subset=['法人'])
                    .map(_color_emp_type, subset=['雇用区分'])
                    .format(fmt_ot)
                )
                styler = styler.set_properties(
                    subset=['残業時間合計(h)'],
                    **{'background-color': '#fef3c7', 'font-weight': '700'},
                )
                styler = styler.set_properties(
                    subset=['残業金額合計(円)'],
                    **{'background-color': '#dbeafe', 'font-weight': '700'},
                )
                st.dataframe(styler, width='stretch', hide_index=True, height=560)

                csv = emp_agg_o.to_csv(index=False).encode('utf-8-sig')
                label_safe = corp_label_o.replace('🔀 ', '').replace('🟢 ', '').replace('🔵 ', '')
                st.download_button(
                    "💾 職員別年計 CSV", csv,
                    file_name=f"残業_職員別{fy_o}年度_{label_safe}.csv",
                    mime='text/csv',
                )

            elif view_mode_o == '👤 職員個別 推移':
                st.markdown("##### 職員個別 月次推移")
                # 検索 + 法人 + 雇用区分フィルタ
                f1, f2, f3 = st.columns([1.5, 1.5, 1])
                with f1:
                    kw_o = st.text_input(
                        "🔍 氏名検索",
                        placeholder="氏名・社員番号・部署",
                        key='ot_emp_kw',
                    )
                with f2:
                    type_f_o = st.multiselect(
                        "雇用区分", ['正社員', 'パート', '不明', '未設定'],
                        default=['正社員', 'パート', '不明', '未設定'],
                        key='ot_emp_type_f',
                    )
                with f3:
                    only_ot_emp = st.checkbox(
                        "残業発生者のみ", value=True, key='ot_emp_only_filter',
                    )

                # 当年度の職員リスト（そもそもデータがある人のみ）
                emp_in_period = (
                    df_ot.groupby(['法人', '社員番号', '氏名', '雇用区分', '部署'])
                    .agg({'残業時間合計(h)': 'sum'})
                    .reset_index()
                )
                if type_f_o:
                    emp_in_period = emp_in_period[
                        emp_in_period['雇用区分'].isin(type_f_o)
                    ]
                if only_ot_emp:
                    emp_in_period = emp_in_period[
                        emp_in_period['残業時間合計(h)'] > 0
                    ]
                if kw_o:
                    kw_norm = kw_o.replace('　', '').replace(' ', '').lower()
                    def _emp_match(row):
                        n = (row['氏名'] or '').replace('　', '').replace(' ', '')
                        c = (row['社員番号'] or '').lower()
                        d = (row['部署'] or '')
                        return (
                            kw_norm in n or kw_norm in c or kw_o in d
                        )
                    emp_in_period = emp_in_period[emp_in_period.apply(_emp_match, axis=1)]

                st.caption(f"該当職員: **{len(emp_in_period)}名**")
                if emp_in_period.empty:
                    st.warning("該当する職員が見つかりません")
                else:
                    # ソート: 残業時間が多い順
                    emp_in_period = emp_in_period.sort_values(
                        '残業時間合計(h)', ascending=False,
                    )
                    opts_emp = {
                        f"[{r['法人']}] {r['社員番号']} {r['氏名']} ({r['雇用区分']}) "
                        f"年計{r['残業時間合計(h)']:.1f}h": (r['法人'], r['社員番号'])
                        for _, r in emp_in_period.iterrows()
                    }
                    pick_emp = st.selectbox(
                        "職員を選択", list(opts_emp.keys()), key='ot_indiv_pick',
                    )
                    target_corp, target_code = opts_emp[pick_emp]

                    emp_data = df_ot[
                        (df_ot['法人'] == target_corp)
                        & (df_ot['社員番号'] == target_code)
                    ].copy().sort_values('対象月')

                    # 全月並べ
                    full_emp = pd.DataFrame({'対象月': target_months_o})
                    emp_data = full_emp.merge(emp_data, on='対象月', how='left')
                    emp_data['法人'] = emp_data['法人'].fillna(target_corp)
                    emp_data['社員番号'] = emp_data['社員番号'].fillna(target_code)
                    for c in time_cols + money_cols_ot:
                        emp_data[c] = emp_data[c].fillna(0)

                    # メトリック
                    total_h_emp = emp_data['残業時間合計(h)'].sum()
                    total_y_emp = emp_data['残業金額合計(円)'].sum()
                    months_with_ot = (emp_data['残業時間合計(h)'] > 0).sum()
                    avg_emp = (total_y_emp / total_h_emp) if total_h_emp > 0 else 0
                    em1, em2, em3, em4 = st.columns(4)
                    em1.metric("年合計 残業時間", f"{total_h_emp:.1f} h")
                    em2.metric("年合計 残業金額", _format_yen(int(total_y_emp)))
                    em3.metric("残業発生月数", f"{months_with_ot}/12 ヶ月")
                    em4.metric("平均時給単価", _format_yen(int(avg_emp)))

                    # 月別表
                    st.markdown("##### 月次表")
                    show = emp_data[['対象月', '支給月'] + time_cols + money_cols_ot]
                    fmt_ot = {c: '{:.2f}' for c in time_cols}
                    fmt_ot.update({c: '{:,}' for c in money_cols_ot})
                    styler = show.style.format(fmt_ot)
                    styler = styler.set_properties(
                        subset=['残業時間合計(h)'],
                        **{'background-color': '#fef3c7', 'font-weight': '700'},
                    )
                    styler = styler.set_properties(
                        subset=['残業金額合計(円)'],
                        **{'background-color': '#dbeafe', 'font-weight': '700'},
                    )
                    st.dataframe(styler, width='stretch', hide_index=True)

                    # 月次推移グラフ
                    if total_h_emp > 0 or total_y_emp > 0:
                        cc1, cc2 = st.columns(2)
                        with cc1:
                            st.markdown("**残業時間 推移**")
                            st.bar_chart(
                                emp_data.set_index('対象月')[['残業時間合計(h)']],
                                height=280,
                            )
                        with cc2:
                            st.markdown("**残業金額 推移**")
                            st.bar_chart(
                                emp_data.set_index('対象月')[['残業金額合計(円)']],
                                height=280,
                            )

            elif view_mode_o == '🏢 施設別 月別':
                st.markdown("##### 施設別 × 月別")
                # 部署が空の職員は「(未設定)」扱い
                df_dept = df_ot.copy()
                df_dept['部署'] = df_dept['部署'].replace('', '(未設定)').fillna('(未設定)')

                # 単月フィルタ
                month_options_d = ['（年度全月）'] + target_months_o
                month_pick_d = st.selectbox(
                    "単月で絞り込む", month_options_d, key='ot_dept_month',
                )
                if month_pick_d != '（年度全月）':
                    df_dept = df_dept[df_dept['対象月'] == month_pick_d]

                if df_dept.empty:
                    st.warning("該当データがありません")
                else:
                    # 集計: 施設(部署)×月
                    pivot_h = df_dept.pivot_table(
                        index='部署', columns='対象月',
                        values='残業時間合計(h)', aggfunc='sum',
                        fill_value=0,
                    )
                    pivot_y = df_dept.pivot_table(
                        index='部署', columns='対象月',
                        values='残業金額合計(円)', aggfunc='sum',
                        fill_value=0,
                    )
                    # 列順を target_months_o に揃える
                    cols_in = [m for m in target_months_o if m in pivot_h.columns]
                    if month_pick_d != '（年度全月）':
                        cols_in = [month_pick_d] if month_pick_d in pivot_h.columns else []
                    if cols_in:
                        pivot_h = pivot_h[cols_in]
                        pivot_y = pivot_y[cols_in]
                    pivot_h['年合計(h)'] = pivot_h.sum(axis=1).round(1)
                    pivot_y['年合計(円)'] = pivot_y.sum(axis=1).astype(int)
                    pivot_h = pivot_h.sort_values('年合計(h)', ascending=False)
                    pivot_y = pivot_y.loc[pivot_h.index]

                    st.markdown("**残業時間 (h)**")
                    st.dataframe(
                        pivot_h.style.format('{:.1f}')
                        .set_properties(subset=['年合計(h)'],
                                          **{'background-color': '#fef3c7',
                                              'font-weight': '700'}),
                        width='stretch',
                    )
                    st.markdown("**残業金額 (円)**")
                    st.dataframe(
                        pivot_y.style.format('{:,.0f}')
                        .set_properties(subset=['年合計(円)'],
                                          **{'background-color': '#dbeafe',
                                              'font-weight': '700'}),
                        width='stretch',
                    )

                    # グラフ
                    if month_pick_d == '（年度全月）':
                        st.markdown("##### 施設×月 残業時間 推移")
                        st.bar_chart(pivot_h.drop(columns=['年合計(h)']).T,
                                       height=320)

            else:  # 🏢 施設別 年計
                st.markdown("##### 施設別 年計")
                df_dept2 = df_ot.copy()
                df_dept2['部署'] = df_dept2['部署'].replace('', '(未設定)').fillna('(未設定)')
                dept_agg = (
                    df_dept2.groupby(['法人', '部署'])
                    .agg({
                        **{c: 'sum' for c in time_cols + money_cols_ot},
                        '氏名': 'nunique',
                    })
                    .rename(columns={'氏名': '職員数'})
                    .reset_index()
                    .sort_values(['法人', '残業時間合計(h)'],
                                  ascending=[True, False])
                )
                fmt_ot = {c: '{:.2f}' for c in time_cols}
                fmt_ot.update({c: '{:,}' for c in money_cols_ot})
                styler = (
                    dept_agg.style
                    .map(_color_corp, subset=['法人'])
                    .format(fmt_ot)
                )
                styler = styler.set_properties(
                    subset=['残業時間合計(h)'],
                    **{'background-color': '#fef3c7', 'font-weight': '700'},
                )
                styler = styler.set_properties(
                    subset=['残業金額合計(円)'],
                    **{'background-color': '#dbeafe', 'font-weight': '700'},
                )
                st.dataframe(styler, width='stretch', hide_index=True, height=520)

                csv = dept_agg.to_csv(index=False).encode('utf-8-sig')
                label_safe = corp_label_o.replace('🔀 ', '').replace('🟢 ', '').replace('🔵 ', '')
                st.download_button(
                    "💾 施設別年計 CSV", csv,
                    file_name=f"残業_施設別{fy_o}年度_{label_safe}.csv",
                    mime='text/csv',
                )

                # グラフ
                st.markdown("##### 施設別 残業時間（年合計）")
                st.bar_chart(
                    dept_agg.set_index('部署')[['残業時間合計(h)']],
                    height=320,
                )
