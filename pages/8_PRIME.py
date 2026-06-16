"""PRIME - 別会社収支管理ダッシュボード."""
from __future__ import annotations

import io
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

PRIME_SUBUNITS = [
    {"key": "itami", "label": "伊丹院", "departments": ("伊丹院",)},
    {"key": "himeji", "label": "姫路院", "departments": ("姫路院",)},
    {"key": "kishiwada", "label": "岸和田院", "departments": ("岸和田院",)},
    {"key": "asagiri", "label": "朝霧院", "departments": ("朝霧院",)},
    {"key": "kobe", "label": "神戸院", "departments": ("神戸院",)},
    {"key": "inami", "label": "稲美院", "departments": ("稲美院", "未選択")},
    {"key": "nishinomiya", "label": "西宮院", "departments": ("西宮院",)},
]

KIRARI_DEPARTMENTS = (
    "きらり",
    "伊丹院",
    "姫路院",
    "岸和田院",
    "朝霧院",
    "神戸院",
    "稲美院",
    "未選択",
    "西宮院",
)

PRIME_GROUPS = [
    {
        "key": "karada",
        "label": "からだケア",
        "kind": "group",
        "departments": ("からだケア鍼灸整骨院",),
    },
    {
        "key": "bpo",
        "label": "在宅BPO",
        "kind": "group",
        "departments": ("在宅BPO",),
    },
    {
        "key": "beauty",
        "label": "美容コンサル",
        "kind": "group",
        "departments": ("美容コンサル",),
    },
    {
        "key": "kirari",
        "label": "きらり",
        "kind": "group",
        "departments": KIRARI_DEPARTMENTS,
    },
]

PRIME_AREAS = [
    {
        "key": "higashi_harima",
        "label": "東播磨エリア",
        "kind": "area",
        "departments": ("稲美院", "未選択", "朝霧院", "姫路院"),
    },
    {
        "key": "hanshin",
        "label": "阪神エリア",
        "kind": "area",
        "departments": ("神戸院", "伊丹院", "岸和田院", "西宮院"),
    },
]


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


def _yen_short(value) -> str:
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = 0
    return f"{v:,}"


def _pct(num, den) -> str:
    try:
        den = float(den)
        if den == 0:
            return "-"
        return f"{float(num) / den * 100:.1f}%"
    except (TypeError, ValueError, ZeroDivisionError):
        return "-"


def _ratio_value(num, den) -> float | None:
    try:
        den = float(den)
        if den == 0:
            return None
        return float(num) / den * 100
    except (TypeError, ValueError, ZeroDivisionError):
        return None


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


def _is_total_row(entry: dict) -> bool:
    return bool(int(entry.get("is_total") or 0))


def _is_personnel_account(entry: dict) -> bool:
    account_name = entry.get("account_name") or ""
    if _is_total_row(entry):
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

    personnel = sum(int(e.get("amount") or 0) for e in entries if _is_personnel_account(e))

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

    total_cost = revenue - operating if revenue or operating else cogs + sga_total
    if sga_total >= personnel:
        expenses = sga_total - personnel
    else:
        expenses = total_cost - personnel

    return {
        "売上高": revenue,
        "売上原価": cogs,
        "売上総利益": gross,
        "販管費": sga_total,
        "総費用": total_cost,
        "人件費": personnel,
        "経費": expenses,
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


def _ym_add(ym: str, months: int) -> str:
    year, month = int(ym[:4]), int(ym[5:7])
    total = year * 12 + (month - 1) + months
    return f"{total // 12}-{(total % 12) + 1:02d}"


def _fiscal_year(ym: str, start_month: int) -> int:
    year, month = int(ym[:4]), int(ym[5:7])
    return year if month >= start_month else year - 1


def _fiscal_years(year_months: list[str], start_month: int) -> list[int]:
    return sorted({_fiscal_year(ym, start_month) for ym in year_months}, reverse=True)


def _fiscal_months(fiscal_year: int, start_month: int) -> list[str]:
    start = f"{fiscal_year}-{start_month:02d}"
    return [_ym_add(start, i) for i in range(12)]


def _fiscal_label(fiscal_year: int, start_month: int) -> str:
    end = _ym_add(f"{fiscal_year}-{start_month:02d}", 11)
    return f"{fiscal_year}年度（{fiscal_year}-{start_month:02d}〜{end}）"


def _known_departments() -> set[str]:
    names = {"部門合計"}
    for item in PRIME_GROUPS + PRIME_AREAS + PRIME_SUBUNITS:
        names.update(item["departments"])
    return names


def _available_departments(year_months: list[str] | None = None) -> list[str]:
    return db.list_prime_departments(year_months)


def _other_departments(available: list[str]) -> tuple[str, ...]:
    known = _known_departments()
    return tuple(d for d in available if d not in known and d not in {"部門合計", "未選択"})


def _target_options(available: list[str]) -> list[dict]:
    options = [{
        "key": "all",
        "label": "📊 全グループ（比較表示）",
        "display": "全グループ（比較表示）",
        "kind": "all",
        "departments": ("部門合計",),
    }]
    for group in PRIME_GROUPS:
        options.append({**group, "label": f"🏢 {group['label']}", "display": group["label"]})
        if group["key"] == "kirari":
            for subunit in PRIME_SUBUNITS:
                options.append({
                    **subunit,
                    "label": f"　└ {subunit['label']}",
                    "display": subunit["label"],
                    "kind": "subunit",
                })
    for area in PRIME_AREAS:
        options.append({**area, "label": f"🗺️ {area['label']}", "display": area["label"]})

    others = _other_departments(available)
    if others:
        options.append({
            "key": "other",
            "label": "📦 その他（未分類）",
            "display": "その他（未分類）",
            "kind": "other",
            "departments": others,
        })
    return options


def _clean_departments(departments: tuple[str, ...], available: list[str]) -> list[str]:
    existing = set(available)
    return [d for d in departments if d in existing]


def _entries_for_target(year_months: list[str], target: dict) -> list[dict]:
    available = _available_departments(year_months)
    if target["kind"] == "all":
        if "部門合計" in available:
            return db.fetch_prime_pl_entries(year_months, ["部門合計"])
        departments = [d for d in available if d != "部門合計"]
    elif target["kind"] == "other":
        departments = list(_other_departments(available))
    else:
        departments = _clean_departments(tuple(target["departments"]), available)
    if not departments:
        return []
    return db.fetch_prime_pl_entries(year_months, departments)


def _entries_for_departments(year_months: list[str], departments: tuple[str, ...]) -> list[dict]:
    available = _available_departments(year_months)
    clean = _clean_departments(departments, available)
    if not clean:
        return []
    return db.fetch_prime_pl_entries(year_months, clean)


def _select_period(existing_yms: list[str], key_prefix: str,
                   start_month: int | None = None,
                   allow_fiscal: bool = False) -> tuple[list[str], str]:
    modes = ["単月", "期間指定"] + (["期累計"] if allow_fiscal and start_month else [])
    mode = st.radio("期間モード", modes, horizontal=True, key=f"{key_prefix}_mode")
    if mode == "単月":
        ym = st.selectbox("対象月", existing_yms, index=0, key=f"{key_prefix}_ym")
        return [ym], ym
    if mode == "期累計":
        fiscal_years = _fiscal_years(existing_yms, start_month or 2)
        fiscal_year = st.selectbox(
            "対象年度",
            fiscal_years,
            index=0,
            format_func=lambda fy: _fiscal_label(fy, start_month or 2),
            key=f"{key_prefix}_fy",
        )
        months = [m for m in _fiscal_months(fiscal_year, start_month or 2) if m in existing_yms]
        return months, _fiscal_label(fiscal_year, start_month or 2)

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
            "構成比": "",
            "集計行": "○" if total_map[key] else "",
        })
    return pd.DataFrame(rows)


def _comparison_defs(available: list[str]) -> list[dict]:
    defs = [{**g, "type_label": "グループ"} for g in PRIME_GROUPS]
    defs.extend({**a, "type_label": "エリア"} for a in PRIME_AREAS)
    others = _other_departments(available)
    if others:
        defs.append({
            "key": "other",
            "label": "その他（未分類）",
            "display": "その他（未分類）",
            "kind": "other",
            "departments": others,
            "type_label": "未分類",
        })
    return defs


def _comparison_rows(year_months: list[str]) -> list[dict]:
    available = _available_departments(year_months)
    rows = []
    for item in _comparison_defs(available):
        entries = _entries_for_departments(year_months, tuple(item["departments"]))
        metrics = _metrics(entries)
        if any(metrics.values()):
            rows.append({
                "種別": item["type_label"],
                "対象": item.get("display") or item["label"],
                "売上高": metrics["売上高"],
                "人件費": metrics["人件費"],
                "経費": metrics["経費"],
                "営業利益": metrics["営業利益"],
                "営業利益率": _pct(metrics["営業利益"], metrics["売上高"]),
                "経常利益": metrics["経常利益"],
                "経常利益率": _pct(metrics["経常利益"], metrics["売上高"]),
                "人件費率": _pct(metrics["人件費"], metrics["売上高"]),
            })
    return rows


def _render_metric_cards(metrics: dict[str, int]) -> None:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("売上高", _yen(metrics["売上高"]))
    c2.metric("人件費", _yen(metrics["人件費"]), delta=f"人件費率 {_pct(metrics['人件費'], metrics['売上高'])}", delta_color="off")
    c3.metric("経費", _yen(metrics["経費"]), delta=f"経費率 {_pct(metrics['経費'], metrics['売上高'])}", delta_color="off")
    c4.metric("営業利益", _yen(metrics["営業利益"]), delta=f"営業利益率 {_pct(metrics['営業利益'], metrics['売上高'])}")
    c5.metric("経常利益", _yen(metrics["経常利益"]), delta=f"経常利益率 {_pct(metrics['経常利益'], metrics['売上高'])}")


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


existing_yms = db.list_prime_pl_year_months()
journal_yms = db.list_prime_journal_year_months()
target = None
target_label = ""
fy_mode_label = "法人決算(2月開始)"
fy_start_month = 2

if existing_yms:
    st.markdown("---")
    st.markdown("### 🎯 表示対象")
    available_all = _available_departments(existing_yms)
    options = _target_options(available_all)
    option_by_label = {o["label"]: o for o in options}
    c_target, c_mode = st.columns([2, 1])
    with c_target:
        selected_label = st.selectbox(
            "対象（親グループ または サブ部門）",
            list(option_by_label.keys()),
            index=0,
            key="prime_target_scope",
        )
        target = option_by_label[selected_label]
        target_label = target.get("display") or selected_label.replace("　└ ", "").strip()
    with c_mode:
        fy_mode_label = st.radio(
            "会計年度の基準",
            ["法人決算(2月開始)", "現場評価(4月開始・年度)"],
            key="prime_fy_mode",
        )
        fy_start_month = 2 if fy_mode_label.startswith("法人") else 4


tab_import, tab_summary, tab_ratio, tab_compare, tab_meeting, tab_report, tab_sga_trend, tab_journal = st.tabs([
    "📥 CSV取込",
    "📊 サマリ",
    "🔍 構成比",
    "📅 比較",
    "🏛️ 業績会議",
    "📝 報告書提出",
    "📈 販管費推移",
    "🧾 仕訳帳",
])


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

        warnings = [f"{r.filename}: {w}" for r in parsed for w in r.warnings]
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
    if not existing_yms or target is None:
        st.info("まず「CSV取込」からPRIMEの試算表CSVを取り込んでください。")
    else:
        target_yms, period_label = _select_period(existing_yms, "prime_summary")
        entries = _entries_for_target(target_yms, target)
        metrics = _metrics(entries)

        st.markdown(
            f"<div style='background:linear-gradient(90deg,#dbeafe 0%,#fef3c7 100%); "
            f"padding:14px 20px; border-radius:10px; border-left:6px solid #2563eb; margin:16px 0;'>"
            f"<div style='font-size:13px; color:#475569; font-weight:600;'>表示中</div>"
            f"<div style='font-size:20px; color:#0f172a; font-weight:700; margin-top:4px;'>"
            f"{period_label} ／ PRIME ／ {target_label}</div></div>",
            unsafe_allow_html=True,
        )
        _render_metric_cards(metrics)

        st.markdown("### 損益内訳")
        df = _pl_table(entries)
        if df.empty:
            st.info("選択期間にデータがありません。")
        else:
            revenue = metrics["売上高"]
            if revenue:
                df["構成比"] = df["金額"].apply(lambda v: _pct(v, revenue))
            st.dataframe(
                df.style.format({"金額": "{:,.0f}"}),
                hide_index=True,
                width="stretch",
                height=min(38 * (len(df) + 1), 700),
            )

        if target["kind"] == "all":
            st.markdown("### グループ・エリア別 損益比較")
            rows = _comparison_rows(target_yms)
            if rows:
                df_compare = pd.DataFrame(rows)
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
                    height=min(38 * (len(df_compare) + 1), 560),
                )


with tab_ratio:
    if not existing_yms or target is None:
        st.info("まずPRIMEの試算表CSVを取り込んでください。")
    else:
        ratio_yms, ratio_label = _select_period(
            existing_yms,
            "prime_ratio",
            start_month=fy_start_month,
            allow_fiscal=True,
        )
        entries = _entries_for_target(ratio_yms, target)
        metrics = _metrics(entries)
        revenue = metrics["売上高"]

        rows = []
        for category, label in [
            ("revenue", "売上"),
            ("cogs", "売上原価"),
            ("sga", "販管費"),
            ("non_operating_income", "営業外収益"),
            ("non_operating_expense", "営業外費用"),
        ]:
            sub = [e for e in entries if (e.get("category") or "") == category and not _is_total_row(e)]
            grouped = defaultdict(int)
            for e in sub:
                grouped[e.get("account_name") or ""] += int(e.get("amount") or 0)
            for account, amount in sorted(grouped.items(), key=lambda kv: abs(kv[1]), reverse=True):
                if amount:
                    rows.append({
                        "区分": label,
                        "科目": account,
                        "金額": amount,
                        "構成比": _pct(amount, revenue),
                    })
        st.markdown(f"### 構成比（{ratio_label} ／ {target_label}）")
        _render_metric_cards(metrics)
        if rows:
            df_ratio = pd.DataFrame(rows)
            st.dataframe(
                df_ratio.style.format({"金額": "{:,.0f}"}),
                hide_index=True,
                width="stretch",
                height=min(38 * (len(df_ratio) + 1), 700),
            )
            chart = df_ratio.head(15).set_index("科目")[["金額"]]
            st.bar_chart(chart, height=320)
        else:
            st.info("構成比に表示できる科目がありません。")


with tab_compare:
    if not existing_yms or target is None:
        st.info("まずPRIMEの試算表CSVを取り込んでください。")
    else:
        current_ym = st.selectbox("対象月", existing_yms, index=0, key="prime_compare_ym")
        prev_ym = _ym_add(current_ym, -1)
        prev_year_ym = _ym_add(current_ym, -12)
        compare_months = [
            ("当月", current_ym),
            ("前月", prev_ym),
            ("前年", prev_year_ym),
        ]
        metric_by_label = {
            label: _metrics(_entries_for_target([ym], target))
            for label, ym in compare_months
        }
        rows = []
        for metric_name in ["売上高", "人件費", "経費", "営業利益", "経常利益", "当期純利益"]:
            current = metric_by_label["当月"][metric_name]
            prev = metric_by_label["前月"][metric_name]
            prev_year = metric_by_label["前年"][metric_name]
            rows.append({
                "項目": metric_name,
                f"当月 {current_ym}": current,
                f"前月 {prev_ym}": prev,
                "前月差": current - prev,
                f"前年 {prev_year_ym}": prev_year,
                "前年差": current - prev_year,
                "対売上比": _pct(current, metric_by_label["当月"]["売上高"]),
            })
        st.markdown(f"### 比較（{target_label}）")
        _render_metric_cards(metric_by_label["当月"])
        df_compare = pd.DataFrame(rows)
        amount_cols = [c for c in df_compare.columns if c != "項目" and c != "対売上比"]
        st.dataframe(
            df_compare.style.format({c: "{:,.0f}" for c in amount_cols}),
            hide_index=True,
            width="stretch",
        )

        if target["kind"] == "all":
            st.markdown("### 当月 グループ・エリア別")
            rows_scope = _comparison_rows([current_ym])
            if rows_scope:
                df_scope = pd.DataFrame(rows_scope)
                st.dataframe(
                    df_scope.style.format({
                        "売上高": "{:,.0f}",
                        "人件費": "{:,.0f}",
                        "経費": "{:,.0f}",
                        "営業利益": "{:,.0f}",
                        "経常利益": "{:,.0f}",
                    }),
                    hide_index=True,
                    width="stretch",
                )


with tab_meeting:
    if not existing_yms:
        st.info("まずPRIMEの試算表CSVを取り込んでください。")
    else:
        meeting_ym = st.selectbox("対象月", existing_yms, index=0, key="prime_meeting_ym")
        rows = []
        for item in _comparison_defs(_available_departments([meeting_ym])):
            metrics = _metrics(_entries_for_departments([meeting_ym], tuple(item["departments"])))
            if any(metrics.values()):
                rows.append({
                    "種別": item["type_label"],
                    "対象": item.get("display") or item["label"],
                    "売上高(千円)": int(metrics["売上高"] / 1000),
                    "人件費(千円)": int(metrics["人件費"] / 1000),
                    "経費(千円)": int(metrics["経費"] / 1000),
                    "営業利益(千円)": int(metrics["営業利益"] / 1000),
                    "経常利益(千円)": int(metrics["経常利益"] / 1000),
                    "営業利益率": _pct(metrics["営業利益"], metrics["売上高"]),
                    "人件費率": _pct(metrics["人件費"], metrics["売上高"]),
                })
        st.markdown(f"### 業績会議（{meeting_ym} ／ 千円）")
        if rows:
            df_meeting = pd.DataFrame(rows)
            st.dataframe(df_meeting, hide_index=True, width="stretch", height=min(38 * (len(df_meeting) + 1), 600))
            st.download_button(
                "📥 業績会議CSV",
                data=_csv_bytes(rows),
                file_name=f"PRIME_業績会議_{meeting_ym}.csv",
                mime="text/csv",
                key="prime_meeting_csv",
            )
        else:
            st.info("対象月にデータがありません。")


with tab_report:
    if not existing_yms or target is None:
        st.info("まずPRIMEの試算表CSVを取り込んでください。")
    else:
        report_ym = st.selectbox("対象月", existing_yms, index=0, key="prime_report_ym")
        report_entries = _entries_for_target([report_ym], target)
        report_metrics = _metrics(report_entries)
        _render_metric_cards(report_metrics)

        st.markdown("### 報告メモ")
        previous_review = st.text_area("前月の振り返り", key="prime_report_previous")
        issue_review = st.text_area("現在の課題", key="prime_report_issue")
        next_actions = st.text_area("次月以降の対策", key="prime_report_actions")
        other_notes = st.text_area("その他", key="prime_report_other")
        row = {
            "対象月": report_ym,
            "対象": target_label,
            "会計年度基準": fy_mode_label,
            "売上高": report_metrics["売上高"],
            "人件費": report_metrics["人件費"],
            "経費": report_metrics["経費"],
            "営業利益": report_metrics["営業利益"],
            "経常利益": report_metrics["経常利益"],
            "前月の振り返り": previous_review,
            "現在の課題": issue_review,
            "次月以降の対策": next_actions,
            "その他": other_notes,
        }
        st.download_button(
            "📥 報告メモCSV",
            data=_csv_bytes([row]),
            file_name=f"PRIME_報告メモ_{report_ym}_{target_label}.csv",
            mime="text/csv",
            key="prime_report_csv",
        )


with tab_sga_trend:
    if not existing_yms or target is None:
        st.info("まずPRIMEの試算表CSVを取り込んでください。")
    else:
        fiscal_years = _fiscal_years(existing_yms, fy_start_month)
        trend_fy = st.selectbox(
            "対象年度",
            fiscal_years,
            index=0,
            format_func=lambda fy: _fiscal_label(fy, fy_start_month),
            key="prime_sga_fy",
        )
        trend_months = [m for m in _fiscal_months(trend_fy, fy_start_month) if m in existing_yms]
        all_entries = _entries_for_target(trend_months, target)
        accounts = sorted({
            e.get("account_name") or ""
            for e in all_entries
            if e.get("account_name")
            and not _is_total_row(e)
            and ((e.get("category") == "sga") or _is_personnel_account(e))
        })
        metric_options = ["人件費合計", "経費合計"] + accounts
        selected_metric = st.selectbox("表示科目", metric_options, key="prime_sga_account")
        rows = []
        for ym in trend_months:
            entries = _entries_for_target([ym], target)
            metrics = _metrics(entries)
            if selected_metric == "人件費合計":
                amount = metrics["人件費"]
            elif selected_metric == "経費合計":
                amount = metrics["経費"]
            else:
                amount = sum(
                    int(e.get("amount") or 0)
                    for e in entries
                    if e.get("account_name") == selected_metric and not _is_total_row(e)
                )
            rows.append({"月": ym, "金額": amount})
        df_trend = pd.DataFrame(rows)
        st.markdown(f"### 販管費推移（{_fiscal_label(trend_fy, fy_start_month)} ／ {target_label}）")
        if not df_trend.empty:
            st.line_chart(df_trend.set_index("月")[["金額"]], height=340)
            st.dataframe(df_trend.style.format({"金額": "{:,.0f}"}), hide_index=True, width="stretch")


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
