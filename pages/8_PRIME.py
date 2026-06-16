"""PRIME - 別会社収支管理ダッシュボード."""
from __future__ import annotations

from collections import defaultdict

import pandas as pd
import streamlit as st

from lib import auth, db, prime_parser, styling


CATEGORY_ORDER = {
    "revenue": 10,
    "revenue_total": 19,
    "cogs": 20,
    "cogs_total": 29,
    "gross_profit": 30,
    "sga": 40,
    "sga_total": 49,
    "operating_profit": 50,
    "non_operating_income": 60,
    "non_operating_expense": 70,
    "ordinary_profit": 80,
    "special_income": 90,
    "special_loss": 100,
    "pretax_income": 110,
    "tax": 120,
    "net_income": 130,
    "other": 999,
}

PERSONNEL_KEYWORDS = (
    "給与",
    "給料",
    "賃金",
    "役員報酬",
    "賞与",
    "法定福利",
    "福利厚生",
    "退職",
    "出向",
    "派遣",
    "人件費",
)


styling.inject_global_css()
auth.require_admin()
auth.render_sidebar_navigation()
db.init_prime_schema()

st.title("PRIME")
st.caption("株式会社PRIMEの試算表（損益計算書）CSVと仕訳帳CSVを、障がい事業部データとは分けて管理します。")


def _yen(value) -> str:
    try:
        return f"{int(value):,} 円"
    except (TypeError, ValueError):
        return "0 円"


def _pct(num, den) -> str:
    try:
        den = float(den)
        if den == 0:
            return "-"
        return f"{float(num) / den * 100:.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "-"


def _category_label(category: str) -> str:
    return prime_parser.PL_CATEGORY_LABELS.get(category, category or "その他")


def _metric_amount(entries: list[dict], primary_category: str,
                   fallback_categories: tuple[str, ...] = ()) -> int:
    primary = sum(int(e.get("amount") or 0) for e in entries if e.get("category") == primary_category)
    if primary != 0:
        return primary
    return sum(int(e.get("amount") or 0) for e in entries if e.get("category") in fallback_categories)


def _amount_by_accounts(entries: list[dict], account_names: tuple[str, ...]) -> int | None:
    targets = set(account_names)
    matched = [int(e.get("amount") or 0) for e in entries if e.get("account_name") in targets]
    if not matched:
        return None
    return sum(matched)


def _is_personnel_account(entry: dict) -> bool:
    account_name = entry.get("account_name") or ""
    if int(entry.get("is_total") or 0):
        return False
    return any(keyword in account_name for keyword in PERSONNEL_KEYWORDS)


def _metrics(entries: list[dict]) -> dict[str, int]:
    revenue = _amount_by_accounts(entries, ("売上高 計", "売上高計"))
    if revenue is None:
        revenue = _metric_amount(entries, "revenue_total", ("revenue",))
    cogs = _amount_by_accounts(entries, ("商品売上原価", "売上原価 計", "売上原価計"))
    if cogs is None:
        cogs = _metric_amount(entries, "cogs_total", ("cogs",))
    gross = _amount_by_accounts(entries, ("売上総損益金額", "売上総利益", "売上総損益"))
    if gross is None:
        gross = _metric_amount(entries, "gross_profit")
    if gross == 0 and (revenue or cogs):
        gross = revenue - cogs
    sga_total = _amount_by_accounts(entries, ("販売管理費 計", "販管費 計", "販売費及び一般管理費 計"))
    if sga_total is None:
        sga_total = _metric_amount(entries, "sga_total", ("sga",))
    personnel = sum(
        int(e.get("amount") or 0)
        for e in entries
        if _is_personnel_account(e)
    )
    operating = _amount_by_accounts(entries, ("営業損益金額", "営業利益", "営業損失"))
    if operating is None:
        operating = _metric_amount(entries, "operating_profit")
    if operating == 0 and (gross or sga_total):
        operating = gross - sga_total
    ordinary = _amount_by_accounts(entries, ("経常損益金額", "経常利益", "経常損失"))
    if ordinary is None:
        ordinary = _metric_amount(entries, "ordinary_profit")
    net_income = _amount_by_accounts(entries, ("当期純損益金額", "当期純利益", "当期純損失"))
    if net_income is None:
        net_income = _metric_amount(entries, "net_income")
    return {
        "売上高": revenue,
        "売上原価": cogs,
        "売上総利益": gross,
        "販管費": sga_total,
        "人件費": personnel,
        "経費": sga_total - personnel,
        "営業利益": operating,
        "経常利益": ordinary,
        "当期純利益": net_income,
    }


def _preview_metrics(entries: list[dict]) -> dict[str, str]:
    m = _metrics(entries)
    return {
        "売上高": _yen(m["売上高"]),
        "営業利益": _yen(m["営業利益"]),
        "経常利益": _yen(m["経常利益"]),
        "当期純利益": _yen(m["当期純利益"]),
    }


def _department_picker(year_months: list[str], key: str) -> str:
    departments = db.list_prime_departments(year_months)
    if not departments:
        return "部門合計"
    default_idx = departments.index("部門合計") if "部門合計" in departments else 0
    return st.selectbox("表示部門", departments, index=default_idx, key=key)


def _select_period(existing_yms: list[str], key_prefix: str) -> tuple[list[str], str]:
    mode = st.radio("期間モード", ["単月", "期間指定"], horizontal=True, key=f"{key_prefix}_mode")
    if mode == "単月":
        ym = st.selectbox("対象月", existing_yms, index=0, key=f"{key_prefix}_ym")
        return [ym], ym

    c1, c2 = st.columns(2)
    with c1:
        start = st.selectbox("開始月", existing_yms, index=len(existing_yms) - 1, key=f"{key_prefix}_start")
    with c2:
        end = st.selectbox("終了月", existing_yms, index=0, key=f"{key_prefix}_end")
    selected = sorted([ym for ym in existing_yms if start <= ym <= end])
    return selected, f"{start}〜{end}"


def _pl_table(entries: list[dict]) -> pd.DataFrame:
    if not entries:
        return pd.DataFrame(columns=["区分", "科目", "金額"])
    rows = []
    grouped = defaultdict(int)
    order_map = {}
    cat_map = {}
    total_map = {}
    for e in entries:
        key = (e.get("category") or "other", e.get("account_name") or "")
        grouped[key] += int(e.get("amount") or 0)
        order_map[key] = min(order_map.get(key, 99999), int(e.get("display_order") or 0))
        cat_map[key] = e.get("category") or "other"
        total_map[key] = max(total_map.get(key, 0), int(e.get("is_total") or 0))
    for key, amount in sorted(grouped.items(), key=lambda kv: (CATEGORY_ORDER.get(kv[0][0], 999), order_map[kv[0]])):
        category, account = key
        rows.append({
            "区分": _category_label(cat_map[key]),
            "科目": account,
            "金額": amount,
            "集計行": "○" if total_map[key] else "",
        })
    return pd.DataFrame(rows)


tab_import, tab_summary, tab_trend, tab_accounts, tab_journal = st.tabs([
    "📥 CSV取込",
    "📊 サマリ",
    "📈 月次推移",
    "🔍 科目分析",
    "🧾 仕訳帳",
])

existing_yms = db.list_prime_pl_year_months()
journal_yms = db.list_prime_journal_year_months()


with tab_import:
    st.markdown("### 試算表CSV（損益計算書）")
    st.caption("1ファイル＝1か月分として取り込みます。同じ月を再取込すると、PRIMEのその月だけ上書きします。")
    pl_files = st.file_uploader(
        "試算表：損益計算書 CSV",
        type=["csv"],
        accept_multiple_files=True,
        key="prime_pl_csv_uploader",
    )
    if pl_files:
        parsed = [prime_parser.parse_prime_pl_csv(f, f.name) for f in pl_files]
        preview_rows = []
        for result in parsed:
            total_entries = [e for e in result.entries if e.get("department_name") == "部門合計"]
            department_count = len({e.get("department_name") for e in result.entries if e.get("department_name")})
            account_count = len({(e.get("display_order"), e.get("account_name")) for e in result.entries})
            if result.error:
                preview_rows.append({
                    "ファイル": result.filename,
                    "対象月": result.year_month or "判定不可",
                    "科目行数": 0,
                    "部門数": 0,
                    "状態": result.error,
                })
            else:
                metrics = _preview_metrics(total_entries)
                preview_rows.append({
                    "ファイル": result.filename,
                    "対象月": result.year_month,
                    "科目行数": account_count,
                    "部門数": department_count,
                    "売上高": metrics["売上高"],
                    "営業利益": metrics["営業利益"],
                    "経常利益": metrics["経常利益"],
                    "状態": "取込可能",
                })
        st.dataframe(pd.DataFrame(preview_rows), hide_index=True, width="stretch")

        warnings = [
            f"{r.filename}: {w}"
            for r in parsed
            for w in r.warnings
        ]
        if warnings:
            with st.expander("読み取り時の注意"):
                for warning in warnings:
                    st.write(f"- {warning}")

        ok_results = [r for r in parsed if not r.error and r.year_month and r.entries]
        overwrite_confirmed = st.checkbox(
            "PRIMEの該当月だけを上書きして取り込むことを確認しました",
            key="prime_pl_overwrite_confirm",
        )
        if st.button(
            "試算表CSVを取り込む",
            type="primary",
            disabled=not ok_results or not overwrite_confirmed,
            key="prime_pl_import_btn",
        ):
            total = 0
            for result in ok_results:
                summary = db.replace_prime_pl_entries(
                    result.year_month,
                    result.entries,
                    result.filename,
                    result.file_hash,
                )
                total += summary["entries"]
            st.success(f"PRIME損益データを取り込みました（{len(ok_results)}ファイル / {total:,}行）。")
            st.rerun()

    st.markdown("---")
    st.markdown("### 仕訳帳CSV")
    st.caption("仕訳帳は重複防止のため、同じファイル内容・同じ行番号は二重登録しません。")
    journal_files = st.file_uploader(
        "仕訳帳 CSV",
        type=["csv"],
        accept_multiple_files=True,
        key="prime_journal_csv_uploader",
    )
    if journal_files:
        parsed_journals = [prime_parser.parse_prime_journal_csv(f, f.name) for f in journal_files]
        journal_preview = []
        for result in parsed_journals:
            journal_preview.append({
                "ファイル": result.filename,
                "期間": result.date_range,
                "行数": len(result.rows),
                "状態": result.error or "取込可能",
            })
        st.dataframe(pd.DataFrame(journal_preview), hide_index=True, width="stretch")

        ok_journals = [r for r in parsed_journals if not r.error and r.rows]
        if st.button(
            "仕訳帳CSVを取り込む",
            type="primary",
            disabled=not ok_journals,
            key="prime_journal_import_btn",
        ):
            inserted = 0
            skipped = 0
            for result in ok_journals:
                summary = db.insert_prime_journal_entries(
                    result.rows,
                    result.filename,
                    result.file_hash,
                    result.date_range,
                )
                inserted += summary["inserted"]
                skipped += summary["skipped"]
            st.success(f"仕訳帳を取り込みました（追加 {inserted:,}行 / 重複 {skipped:,}行）。")
            st.rerun()

    c1, c2 = st.columns(2)
    with c1:
        imports = db.list_prime_pl_imports(limit=10)
        st.markdown("#### 試算表 取込履歴")
        if imports:
            st.dataframe(pd.DataFrame(imports), hide_index=True, width="stretch")
        else:
            st.info("まだ取込履歴がありません。")
    with c2:
        journal_imports = db.list_prime_journal_imports(limit=10)
        st.markdown("#### 仕訳帳 取込履歴")
        if journal_imports:
            st.dataframe(pd.DataFrame(journal_imports), hide_index=True, width="stretch")
        else:
            st.info("まだ取込履歴がありません。")


with tab_summary:
    if not existing_yms:
        st.info("まず「CSV取込」からPRIMEの試算表CSVを取り込んでください。")
    else:
        target_yms, period_label = _select_period(existing_yms, "prime_summary")
        department_name = _department_picker(target_yms, "prime_summary_department")
        entries = db.fetch_prime_pl_entries(target_yms, [department_name])
        metrics = _metrics(entries)

        st.markdown(
            f"<div style='background:linear-gradient(90deg,#dbeafe 0%,#fef3c7 100%); "
            f"padding:14px 20px; border-radius:10px; border-left:6px solid #2563eb; margin:16px 0;'>"
            f"<div style='font-size:13px; color:#475569; font-weight:600;'>表示中</div>"
            f"<div style='font-size:20px; color:#0f172a; font-weight:700; margin-top:4px;'>"
            f"{period_label} ／ PRIME ／ {department_name}</div></div>",
            unsafe_allow_html=True,
        )

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("売上高", _yen(metrics["売上高"]))
        c2.metric("人件費", _yen(metrics["人件費"]), delta=f"人件費率 {_pct(metrics['人件費'], metrics['売上高'])}", delta_color="off")
        c3.metric("経費", _yen(metrics["経費"]), delta=f"経費率 {_pct(metrics['経費'], metrics['売上高'])}", delta_color="off")
        c4.metric("営業利益", _yen(metrics["営業利益"]), delta=f"営業利益率 {_pct(metrics['営業利益'], metrics['売上高'])}")
        c5.metric("経常利益", _yen(metrics["経常利益"]), delta=f"経常利益率 {_pct(metrics['経常利益'], metrics['売上高'])}")

        st.markdown("### 損益内訳")
        df = _pl_table(entries)
        if df.empty:
            st.info("選択期間にデータがありません。")
        else:
            styled = df.style.format({"金額": "{:,.0f}"})
            st.dataframe(styled, hide_index=True, width="stretch", height=min(38 * (len(df) + 1), 700))

        if department_name == "部門合計":
            st.markdown("### 部門別 損益比較")
            departments = [d for d in db.list_prime_departments(target_yms) if d != "部門合計"]
            comparison_rows = []
            for dept in departments:
                dept_metrics = _metrics(db.fetch_prime_pl_entries(target_yms, [dept]))
                if any(dept_metrics.values()):
                    comparison_rows.append({
                        "部門": dept,
                        "売上高": dept_metrics["売上高"],
                        "人件費": dept_metrics["人件費"],
                        "経費": dept_metrics["経費"],
                        "営業利益": dept_metrics["営業利益"],
                        "営業利益率": _pct(dept_metrics["営業利益"], dept_metrics["売上高"]),
                        "経常利益": dept_metrics["経常利益"],
                        "経常利益率": _pct(dept_metrics["経常利益"], dept_metrics["売上高"]),
                    })
            if comparison_rows:
                df_compare = pd.DataFrame(comparison_rows)
                st.dataframe(
                    df_compare.style.format({
                        "売上高": "{:,.0f}",
                        "人件費": "{:,.0f}",
                        "経費": "{:,.0f}",
                        "営業利益": "{:,.0f}",
                        "経常利益": "{:,.0f}",
                    }),
                    hide_index=True,
                    width="stretch",
                    height=min(38 * (len(df_compare) + 1), 520),
                )


with tab_trend:
    if not existing_yms:
        st.info("まずPRIMEの試算表CSVを取り込んでください。")
    else:
        department_name = _department_picker(existing_yms, "prime_trend_department")
        months = sorted(existing_yms)
        monthly_rows = []
        for ym in months:
            m = _metrics(db.fetch_prime_pl_entries([ym], [department_name]))
            monthly_rows.append({
                "月": ym,
                "売上高": m["売上高"],
                "営業利益": m["営業利益"],
                "経常利益": m["経常利益"],
                "当期純利益": m["当期純利益"],
                "営業利益率": _pct(m["営業利益"], m["売上高"]),
                "経常利益率": _pct(m["経常利益"], m["売上高"]),
            })
        trend_df = pd.DataFrame(monthly_rows)
        if not trend_df.empty:
            chart_df = trend_df.set_index("月")[["売上高", "営業利益", "経常利益", "当期純利益"]]
            st.line_chart(chart_df, height=360)
            st.dataframe(
                trend_df.style.format({
                    "売上高": "{:,.0f}",
                    "営業利益": "{:,.0f}",
                    "経常利益": "{:,.0f}",
                    "当期純利益": "{:,.0f}",
                }),
                hide_index=True,
                width="stretch",
            )


with tab_accounts:
    if not existing_yms:
        st.info("まずPRIMEの試算表CSVを取り込んでください。")
    else:
        department_name = _department_picker(existing_yms, "prime_account_department")
        all_entries = db.fetch_prime_pl_entries(existing_yms, [department_name])
        categories = sorted(
            {e.get("category") or "other" for e in all_entries},
            key=lambda c: CATEGORY_ORDER.get(c, 999),
        )
        category_options = {f"{_category_label(c)}": c for c in categories}
        category_label = st.selectbox("区分", list(category_options.keys()), key="prime_account_category")
        selected_category = category_options[category_label]
        account_names = sorted({
            e.get("account_name") or ""
            for e in all_entries
            if (e.get("category") or "other") == selected_category and e.get("account_name")
        })
        if not account_names:
            st.info("この区分に科目がありません。")
        else:
            account = st.selectbox("科目", account_names, key="prime_account_name")
            rows = []
            for ym in sorted(existing_yms):
                amount = sum(
                    int(e.get("amount") or 0)
                    for e in all_entries
                    if e.get("year_month") == ym and e.get("account_name") == account
                )
                rows.append({"月": ym, "金額": amount})
            df_account = pd.DataFrame(rows).set_index("月")
            st.bar_chart(df_account, height=340)
            st.dataframe(df_account.reset_index().style.format({"金額": "{:,.0f}"}), hide_index=True, width="stretch")


with tab_journal:
    if not journal_yms:
        st.info("仕訳帳CSVを取り込むと、勘定科目や取引先ごとの内訳確認ができます。")
    else:
        target_yms, period_label = _select_period(journal_yms, "prime_journal")
        keyword = st.text_input("検索（科目・取引先・摘要）", key="prime_journal_keyword")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### 借方科目 上位")
            debit_summary = db.prime_journal_account_summary(target_yms, side="debit", limit=15)
            if debit_summary:
                st.dataframe(
                    pd.DataFrame(debit_summary).style.format({"total_amount": "{:,.0f}"}),
                    hide_index=True,
                    width="stretch",
                )
        with c2:
            st.markdown("#### 貸方科目 上位")
            credit_summary = db.prime_journal_account_summary(target_yms, side="credit", limit=15)
            if credit_summary:
                st.dataframe(
                    pd.DataFrame(credit_summary).style.format({"total_amount": "{:,.0f}"}),
                    hide_index=True,
                    width="stretch",
                )

        st.markdown(f"#### 仕訳明細（{period_label}）")
        rows = db.fetch_prime_journal_entries(target_yms, keyword=keyword.strip() or None, limit=1000)
        if rows:
            df_journal = pd.DataFrame(rows)
            show_cols = [
                "transaction_date", "debit_account", "debit_amount", "debit_partner",
                "credit_account", "credit_amount", "credit_partner", "transaction_content",
                "source_filename",
            ]
            show_cols = [c for c in show_cols if c in df_journal.columns]
            st.dataframe(
                df_journal[show_cols].style.format({
                    "debit_amount": "{:,.0f}",
                    "credit_amount": "{:,.0f}",
                }),
                hide_index=True,
                width="stretch",
                height=520,
            )
        else:
            st.info("条件に合う仕訳明細がありません。")

auth.render_sidebar_user_box()
