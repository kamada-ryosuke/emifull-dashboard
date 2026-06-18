"""PRIME - 別会社収支管理ダッシュボード."""
from __future__ import annotations

import io
import re
import zipfile
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

TRANSPORT_COST_KEYWORDS = (
    "燃料費",
    "ガソリン",
    "車両費",
    "車輌費",
    "車両",
    "自動車",
    "車検",
    "保険料",
    "自動車保険",
    "車両保険",
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

REPORTER_ROLES = ["部長", "次長", "課長", "係長", "主任", "副主任", "一般職"]


styling.inject_global_css()
auth.require_prime_access()
auth.render_sidebar_navigation()
db.init_prime_schema()

st.title("PRIME")
st.caption("株式会社PRIMEの試算表（損益計算書）CSVを、障がい事業部データとは分けて管理します。")


def _safe_filename(value: str) -> str:
    value = str(value or "").replace("／", "_").replace("（", "(").replace("）", ")").replace("/", "_")
    value = re.sub(r'[\\:*?"<>|]', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "PRIME"


def _build_pdfs_zip(items, period_tag: str, prefix: str) -> bytes:
    from lib import pdf_report as _pdf

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        seen = {}
        for label, page_elems in items:
            base = _safe_filename(f"PRIME_{prefix}_{label}_{period_tag}")
            n = seen.get(base, 0)
            seen[base] = n + 1
            suffix = "" if n == 0 else f"_{n + 1}"
            zf.writestr(f"{base}{suffix}.pdf", _pdf.build_pdf([page_elems], footer_label="PRIME"))
    return buf.getvalue()


def _no_prime_data_message() -> str:
    if auth.can_manage_prime():
        return "まず「CSV取込」からPRIMEの試算表CSVを取り込んでください。"
    return "PRIMEのデータがまだ取り込まれていません。管理者に確認してください。"


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


def _diff_pct(curr, prev) -> str:
    try:
        prev = float(prev)
        if prev == 0:
            return "-"
        return f"{(float(curr) - prev) / abs(prev) * 100:+.1f}%"
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


def _is_total_row(entry: dict) -> bool:
    return bool(int(entry.get("is_total") or 0))


def _is_personnel_account(entry: dict) -> bool:
    account_name = entry.get("account_name") or ""
    if _is_total_row(entry):
        return False
    return any(keyword in account_name for keyword in PERSONNEL_KEYWORDS)


def _is_transport_cost_account(entry: dict) -> bool:
    account_name = entry.get("account_name") or ""
    if _is_total_row(entry):
        return False
    return any(keyword in account_name for keyword in TRANSPORT_COST_KEYWORDS)


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


def _transport_cost(entries: list[dict]) -> int:
    return sum(int(e.get("amount") or 0) for e in entries if _is_transport_cost_account(e))


def _ratio_metric_rows(entries: list[dict]) -> list[dict]:
    metrics = _metrics(entries)
    revenue = metrics["売上高"]
    rows = [
        {
            "項目": "人件費",
            "値": _yen(metrics["人件費"]),
            "補足": f"人件費率 {_pct(metrics['人件費'], revenue)}",
            "_amount": metrics["人件費"],
            "_kind": "cost",
        },
        {
            "項目": "その他経費",
            "値": _yen(metrics["経費"]),
            "補足": f"経費比率 {_pct(metrics['経費'], revenue)}",
            "_amount": metrics["経費"],
            "_kind": "cost",
        },
        {
            "項目": "販管費",
            "値": _yen(metrics["販管費"]),
            "補足": f"販管費率 {_pct(metrics['販管費'], revenue)}",
            "_amount": metrics["販管費"],
            "_kind": "cost",
        },
        {
            "項目": "営業利益",
            "値": _yen(metrics["営業利益"]),
            "補足": f"営業利益率 {_pct(metrics['営業利益'], revenue)}",
            "_amount": metrics["営業利益"],
            "_kind": "profit",
        },
        {
            "項目": "経常利益",
            "値": _yen(metrics["経常利益"]),
            "補足": f"経常利益率 {_pct(metrics['経常利益'], revenue)}",
            "_amount": metrics["経常利益"],
            "_kind": "profit",
        },
        {
            "項目": "送迎コスト",
            "値": _yen(_transport_cost(entries)),
            "補足": f"対売上比 {_pct(_transport_cost(entries), revenue)}",
            "_amount": _transport_cost(entries),
            "_kind": "cost",
        },
    ]
    return rows


def _style_ratio_metrics(row):
    styles = [""] * len(row)
    if row["項目"] not in ("営業利益", "経常利益"):
        return styles
    value_idx = list(row.index).index("値")
    note_idx = list(row.index).index("補足")
    if str(row["値"]).startswith("-"):
        styles[value_idx] = "color:#dc2626; font-weight:700"
        styles[note_idx] = "color:#dc2626; font-weight:700"
    elif str(row["値"]) not in ("0 円", "-"):
        styles[value_idx] = "color:#15803d; font-weight:700"
        styles[note_idx] = "color:#15803d; font-weight:700"
    return styles


def _ratio_metric_pdf_data(entries: list[dict]) -> list[tuple]:
    return [
        (r["項目"], r["値"], r["補足"], r.get("_amount"), r.get("_kind", "neutral"))
        for r in _ratio_metric_rows(entries)
    ]


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


def _target_display(option: dict) -> str:
    return option.get("display") or str(option.get("label") or "").replace("　└ ", "").strip()


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


def _sga_detail_entries(entries: list[dict]) -> list[dict]:
    """PRIME CSVで販管費明細が売上原価区分に寄る場合も表示時に補正する。"""
    gross_orders = [
        int(e.get("display_order") or 0)
        for e in entries
        if e.get("category") == "gross_profit" or "売上総" in (e.get("account_name") or "")
    ]
    op_orders = [
        int(e.get("display_order") or 0)
        for e in entries
        if e.get("category") == "operating_profit" or "営業損益" in (e.get("account_name") or "")
    ]
    gross_order = min(gross_orders) if gross_orders else None
    op_order = min(op_orders) if op_orders else None

    details = []
    for e in entries:
        if _is_total_row(e):
            continue
        if e.get("category") == "sga":
            details.append(e)
            continue
        if gross_order is None or op_order is None:
            continue
        order = int(e.get("display_order") or 0)
        account = e.get("account_name") or ""
        if gross_order < order < op_order and "販売管理費" not in account and "販管費" not in account:
            details.append(e)
    return details


def _display_category_for_entry(entry: dict, sga_detail_ids: set) -> str:
    account = entry.get("account_name") or ""
    if entry.get("id") in sga_detail_ids:
        return "sga"
    if account in {"販売管理費 計", "販管費 計", "販売費及び一般管理費 計"}:
        return "sga_total"
    return entry.get("category") or "other"


def _pl_table(entries: list[dict]) -> pd.DataFrame:
    if not entries:
        return pd.DataFrame(columns=["区分", "科目", "金額"])
    rows = []
    grouped = defaultdict(int)
    order_map = {}
    cat_map = {}
    total_map = {}
    sga_detail_ids = {e.get("id") for e in _sga_detail_entries(entries)}
    for e in entries:
        display_category = _display_category_for_entry(e, sga_detail_ids)
        key = (display_category, e.get("account_name") or "")
        grouped[key] += int(e.get("amount") or 0)
        order_map[key] = min(order_map.get(key, 99999), int(e.get("display_order") or 0))
        cat_map[key] = display_category
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


def _revenue_composition_rows(entries: list[dict]) -> list[dict]:
    metrics = _metrics(entries)
    revenue = metrics["売上高"]
    grouped = defaultdict(int)
    order_map = {}
    for e in entries:
        if e.get("category") == "revenue" and not _is_total_row(e):
            account = e.get("account_name") or ""
            grouped[account] += int(e.get("amount") or 0)
            order_map[account] = min(order_map.get(account, 99999), int(e.get("display_order") or 0))
    rows = [
        {"科目": account, "金額": amount, "構成比": _pct(amount, revenue)}
        for account, amount in sorted(grouped.items(), key=lambda kv: order_map.get(kv[0], 99999))
        if amount
    ]
    if rows:
        rows.append({"科目": "【売上高 計】", "金額": revenue, "構成比": "100.0%"})
    return rows


def _sga_composition_rows(entries: list[dict]) -> list[dict]:
    metrics = _metrics(entries)
    revenue = metrics["売上高"]
    sga_total = metrics["販管費"]
    grouped = defaultdict(int)
    for e in _sga_detail_entries(entries):
        grouped[e.get("account_name") or ""] += int(e.get("amount") or 0)
    rows = [
        {
            "科目": account,
            "金額": amount,
            "販管費比": _pct(amount, sga_total),
            "対売上比": _pct(amount, revenue),
        }
        for account, amount in sorted(grouped.items(), key=lambda kv: -abs(kv[1]))
        if amount
    ]
    if rows:
        rows.append({
            "科目": "【販管費 計】",
            "金額": sga_total,
            "販管費比": "100.0%",
            "対売上比": _pct(sga_total, revenue),
        })
    return rows


def _bold_total_row(total_label: str):
    def _style(row):
        if row.iloc[0] == total_label:
            return ["background-color:#f1f5f9; font-weight:700"] * len(row)
        return [""] * len(row)
    return _style


def _composition_pdf_data(entries: list[dict]) -> tuple[list[tuple], list[tuple], list[tuple]]:
    rev_data = [(r["科目"], r["金額"], r["構成比"]) for r in _revenue_composition_rows(entries)]
    metrics_data = _ratio_metric_pdf_data(entries)
    sga_data = [
        (r["科目"], r["金額"], r["販管費比"], r["対売上比"])
        for r in _sga_composition_rows(entries)
    ]
    return rev_data, metrics_data, sga_data


def _build_composition_page_for(scope_label: str, year_months: list[str], scope: dict,
                                period_label: str):
    from lib import pdf_report as _pdf

    entries = _entries_for_target(year_months, scope)
    rev_data, metrics_data, sga_data = _composition_pdf_data(entries)
    return _pdf.build_composition_page(
        facility_label=scope_label,
        period_label=f"対象: {period_label}",
        rev_data=rev_data,
        metrics_data=metrics_data,
        sga_data=sga_data,
        footnote_text=(
            "※ 人件費 = 役員報酬・給与手当・出向料・法定福利費・賞与等。"
            " その他経費 = 販管費合計 - 人件費。"
            " 送迎コスト = 燃料費+車両費+保険料等。"
        ),
    )


def _scope_defs_for_groups(year_months: list[str]) -> list[dict]:
    available = _available_departments(year_months)
    return _comparison_defs(available)


def _scope_defs_for_subunits(year_months: list[str]) -> list[dict]:
    available = set(_available_departments(year_months))
    return [
        {**s, "display": s["label"], "kind": "subunit"}
        for s in PRIME_SUBUNITS
        if any(dep in available for dep in s["departments"])
    ]


def _csv_bytes_from_df(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def _pl_export_df(scope_label: str, year_months: list[str], scope: dict) -> pd.DataFrame:
    entries = _entries_for_target(year_months, scope)
    sga_detail_ids = {e.get("id") for e in _sga_detail_entries(entries)}
    rows = []
    for e in entries:
        rows.append({
            "対象": scope_label,
            "年月": e.get("year_month"),
            "部門": e.get("department_name"),
            "科目区分": _category_label(_display_category_for_entry(e, sga_detail_ids)),
            "科目": e.get("account_name"),
            "金額": int(e.get("amount") or 0),
            "集計行": "○" if _is_total_row(e) else "",
            "取込元": e.get("source_filename"),
        })
    return pd.DataFrame(rows, columns=["対象", "年月", "部門", "科目区分", "科目", "金額", "集計行", "取込元"])


def _build_pl_csv_zip(scopes: list[dict], year_months: list[str], period_label: str,
                      prefix: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for scope in scopes:
            label = scope.get("display") or scope.get("label") or scope.get("key")
            df = _pl_export_df(label, year_months, scope)
            filename = _safe_filename(f"PRIME_{prefix}_{label}_{period_label}_損益.csv")
            zf.writestr(filename, _csv_bytes_from_df(df))
    return buf.getvalue()


def _sga_account_amounts(entries: list[dict]) -> dict[str, int]:
    grouped = defaultdict(int)
    for e in _sga_detail_entries(entries):
        grouped[e.get("account_name") or ""] += int(e.get("amount") or 0)
    return dict(grouped)


def _sga_compare_rows(curr_entries: list[dict], prev_entries: list[dict],
                      prev_year_entries: list[dict], limit: int = 15) -> list[dict]:
    curr = _sga_account_amounts(curr_entries)
    prev = _sga_account_amounts(prev_entries)
    prev_year = _sga_account_amounts(prev_year_entries)
    accounts = sorted(
        set(curr) | set(prev) | set(prev_year),
        key=lambda account: -abs(curr.get(account, 0)),
    )[:limit]
    rows = []
    for account in accounts:
        c = curr.get(account, 0)
        p = prev.get(account, 0)
        y = prev_year.get(account, 0)
        rows.append({
            "科目": account,
            "当月": c,
            "前月": p,
            "前年": y,
            "前月比": c - p,
            "前月比%": _diff_pct(c, p),
            "前年比": c - y,
            "前年比%": _diff_pct(c, y),
        })
    return rows


def _color_profit_or_cost_change(row):
    styles = [""] * len(row)
    labels = list(row.index)
    for col in ("前月差", "前年差", "前月比", "前年比"):
        if col not in labels:
            continue
        idx = labels.index(col)
        try:
            value = int(row[col])
        except (TypeError, ValueError):
            continue
        is_good_when_up = row.get("項目") in ("売上高", "営業利益", "経常利益", "当期純利益")
        if value < 0:
            styles[idx] = "color:#dc2626; font-weight:700" if is_good_when_up else "color:#15803d; font-weight:700"
        elif value > 0:
            styles[idx] = "color:#15803d; font-weight:700" if is_good_when_up else "color:#dc2626; font-weight:700"
    return styles


def _to_k(value) -> int:
    try:
        return int(int(value) / 1000)
    except (TypeError, ValueError):
        return 0


def _meeting_row(item: dict, current_ym: str, prev_ym: str, prev_year_ym: str,
                 section_label: str = "全体") -> dict:
    curr = _metrics(_entries_for_departments([current_ym], tuple(item["departments"])))
    prev = _metrics(_entries_for_departments([prev_ym], tuple(item["departments"])))
    prev_year = _metrics(_entries_for_departments([prev_year_ym], tuple(item["departments"])))
    row = {
        "一覧": section_label,
        "種別": item.get("type_label") or "院",
        "対象": item.get("display") or item.get("label"),
    }
    for key, label in [
        ("売上高", "売上"),
        ("人件費", "人件費"),
        ("経費", "経費"),
        ("営業利益", "営業利益"),
    ]:
        row[f"{label} 実績"] = _to_k(curr[key])
        row[f"{label} 前月比"] = _to_k(curr[key] - prev[key])
        row[f"{label} 前年比"] = _to_k(curr[key] - prev_year[key])
    row["営業利益率"] = _pct(curr["営業利益"], curr["売上高"])
    row["人件費率"] = _pct(curr["人件費"], curr["売上高"])
    row["_has_value"] = any(curr.values()) or any(prev.values()) or any(prev_year.values())
    return row


def _meeting_rows(defs: list[dict], current_ym: str, section_label: str) -> list[dict]:
    prev_ym = _ym_add(current_ym, -1)
    prev_year_ym = _ym_add(current_ym, -12)
    rows = [
        _meeting_row(item, current_ym, prev_ym, prev_year_ym, section_label)
        for item in defs
    ]
    return [row for row in rows if row.pop("_has_value", False)]


def _style_meeting(row):
    styles = [""] * len(row)
    for idx, col in enumerate(row.index):
        if not (col.endswith("前月比") or col.endswith("前年比")):
            continue
        try:
            value = int(row[col])
        except (TypeError, ValueError):
            continue
        is_good_when_up = col.startswith("売上") or col.startswith("営業利益")
        if value < 0:
            styles[idx] = "color:#dc2626; font-weight:700" if is_good_when_up else "color:#15803d; font-weight:700"
        elif value > 0:
            styles[idx] = "color:#15803d; font-weight:700" if is_good_when_up else "color:#dc2626; font-weight:700"
    return styles


def _format_meeting_df(rows: list[dict]) -> pd.DataFrame:
    columns = [
        "種別", "対象",
        "売上 実績", "売上 前月比", "売上 前年比",
        "人件費 実績", "人件費 前月比", "人件費 前年比",
        "経費 実績", "経費 前月比", "経費 前年比",
        "営業利益 実績", "営業利益 前月比", "営業利益 前年比",
        "営業利益率", "人件費率",
    ]
    return pd.DataFrame(rows).drop(columns=["一覧"], errors="ignore").reindex(columns=columns)


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
    c3.metric("その他経費", _yen(metrics["経費"]), delta=f"経費比率 {_pct(metrics['経費'], metrics['売上高'])}", delta_color="off")
    c4.metric("営業利益", _yen(metrics["営業利益"]), delta=f"営業利益率 {_pct(metrics['営業利益'], metrics['売上高'])}")
    c5.metric("経常利益", _yen(metrics["経常利益"]), delta=f"経常利益率 {_pct(metrics['経常利益'], metrics['売上高'])}")


def _csv_bytes(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    pd.DataFrame(rows).to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8-sig")


existing_yms = db.list_prime_pl_year_months()
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


prime_can_import = auth.can_manage_prime()
if prime_can_import:
    tab_import, tab_summary, tab_ratio, tab_compare, tab_meeting, tab_report = st.tabs([
        "📥 CSV取込",
        "📊 サマリ",
        "🔍 構成比",
        "📅 比較",
        "🏛️ 業績会議",
        "📝 報告書提出",
    ])
else:
    tab_summary, tab_ratio, tab_compare, tab_meeting, tab_report = st.tabs([
        "📊 サマリ",
        "🔍 構成比",
        "📅 比較",
        "🏛️ 業績会議",
        "📝 報告書提出",
    ])


if prime_can_import:
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
        st.markdown("#### 試算表 取込履歴")
        imports = db.list_prime_pl_imports(limit=10)
        if imports:
            st.dataframe(pd.DataFrame(imports), hide_index=True, width="stretch")
        else:
            st.info("まだ取込履歴がありません。")


with tab_summary:
    if not existing_yms or target is None:
        st.info(_no_prime_data_message())
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
        st.info(_no_prime_data_message())
    else:
        ratio_yms, ratio_label = _select_period(
            existing_yms,
            "prime_ratio",
            start_month=fy_start_month,
            allow_fiscal=True,
        )
        entries = _entries_for_target(ratio_yms, target)
        metrics = _metrics(entries)

        st.markdown(
            f"<div style='font-size:14px; color:#475569; margin-top:8px;'>"
            f"対象: <b>{ratio_label}</b> ／ PRIME ／ {target_label}</div>",
            unsafe_allow_html=True,
        )

        col_r, col_s = st.columns(2)
        with col_r:
            st.markdown("##### 売上の構成")
            rev_rows = _revenue_composition_rows(entries)
            if rev_rows:
                df_rev = pd.DataFrame(rev_rows)
                st.dataframe(
                    df_rev.style.apply(_bold_total_row("【売上高 計】"), axis=1).format({"金額": "{:,.0f}"}),
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.info("売上データがありません。")

            st.markdown("##### 主要指標")
            metric_rows = _ratio_metric_rows(entries)
            df_metrics = pd.DataFrame([
                {k: v for k, v in row.items() if not k.startswith("_")}
                for row in metric_rows
            ])
            st.dataframe(
                df_metrics.style.apply(_style_ratio_metrics, axis=1),
                hide_index=True,
                width="stretch",
            )
            st.caption(
                "※ その他経費 = 販管費合計 - 人件費。"
                " 送迎コスト = 燃料費 + 車両費 + 保険料等。"
            )

        with col_s:
            st.markdown("##### 販管費の構成")
            sga_rows = _sga_composition_rows(entries)
            if sga_rows:
                df_sga = pd.DataFrame(sga_rows)
                st.dataframe(
                    df_sga.style.apply(_bold_total_row("【販管費 計】"), axis=1).format({"金額": "{:,.0f}"}),
                    hide_index=True,
                    width="stretch",
                    height=min(35 * (len(df_sga) + 1) + 3, 600),
                )
            else:
                st.info("販管費データがありません。")

        st.markdown("---")
        st.markdown("### 📄 A4 PDFレポート")
        st.caption(
            "1対象=1ページの構成比レポートを生成します。"
            "グループ／院別の個別PDFはZIPでダウンロードできます。"
        )
        from lib import pdf_report as _pdf

        pdf_cols = st.columns(3)
        with pdf_cols[0]:
            if st.button("📄 この対象のPDFレポート生成", key="prime_ratio_pdf_single_btn"):
                page = _build_composition_page_for(target_label, ratio_yms, target, ratio_label)
                st.session_state["prime_ratio_pdf_single"] = (_pdf.build_pdf([page], footer_label="PRIME"), target_label)
            if "prime_ratio_pdf_single" in st.session_state:
                pdf_bytes, label = st.session_state["prime_ratio_pdf_single"]
                st.download_button(
                    "⬇ 個別レポート (PDF)",
                    data=pdf_bytes,
                    file_name=f"PRIME_構成比_{_safe_filename(label)}_{_safe_filename(ratio_label)}.pdf",
                    mime="application/pdf",
                    key="prime_ratio_pdf_single_dl",
                )
        with pdf_cols[1]:
            if st.button("📁 全グループ 個別PDF (ZIP)", key="prime_ratio_pdf_groups_btn"):
                items = []
                for scope in _scope_defs_for_groups(ratio_yms):
                    label = scope.get("display") or scope.get("label")
                    items.append((label, _build_composition_page_for(label, ratio_yms, scope, ratio_label)))
                st.session_state["prime_ratio_pdf_groups"] = (
                    _build_pdfs_zip(items, ratio_label, "構成比"),
                    len(items),
                )
            if "prime_ratio_pdf_groups" in st.session_state:
                zip_bytes, count = st.session_state["prime_ratio_pdf_groups"]
                st.download_button(
                    f"⬇ グループ個別PDF×{count} (ZIP)",
                    data=zip_bytes,
                    file_name=f"PRIME_構成比_全グループ_{_safe_filename(ratio_label)}.zip",
                    mime="application/zip",
                    key="prime_ratio_pdf_groups_dl",
                )
        with pdf_cols[2]:
            if st.button("📁 全院 個別PDF (ZIP)", key="prime_ratio_pdf_subs_btn"):
                items = []
                for scope in _scope_defs_for_subunits(ratio_yms):
                    label = scope.get("display") or scope.get("label")
                    items.append((label, _build_composition_page_for(label, ratio_yms, scope, ratio_label)))
                st.session_state["prime_ratio_pdf_subs"] = (
                    _build_pdfs_zip(items, ratio_label, "構成比"),
                    len(items),
                )
            if "prime_ratio_pdf_subs" in st.session_state:
                zip_bytes, count = st.session_state["prime_ratio_pdf_subs"]
                st.download_button(
                    f"⬇ 院別PDF×{count} (ZIP)",
                    data=zip_bytes,
                    file_name=f"PRIME_構成比_全院_{_safe_filename(ratio_label)}.zip",
                    mime="application/zip",
                    key="prime_ratio_pdf_subs_dl",
                )

        st.markdown("### 📊 CSV出力")
        st.caption("損益CSVは、選択中の期間・対象範囲だけを切り出します。")
        csv_cols = st.columns(3)
        with csv_cols[0]:
            st.download_button(
                "⬇ この対象の損益CSV",
                data=_csv_bytes_from_df(_pl_export_df(target_label, ratio_yms, target)),
                file_name=f"PRIME_構成比_{_safe_filename(target_label)}_{_safe_filename(ratio_label)}_損益.csv",
                mime="text/csv",
                key="prime_ratio_pl_csv_dl",
            )
        with csv_cols[1]:
            group_scopes = _scope_defs_for_groups(ratio_yms)
            if group_scopes:
                st.download_button(
                    "📁 全グループ 個別CSV (ZIP)",
                    data=_build_pl_csv_zip(group_scopes, ratio_yms, ratio_label, "全グループ"),
                    file_name=f"PRIME_構成比_全グループ_{_safe_filename(ratio_label)}_CSV.zip",
                    mime="application/zip",
                    key="prime_ratio_group_csv_zip_dl",
                )
        with csv_cols[2]:
            subunit_scopes = _scope_defs_for_subunits(ratio_yms)
            if subunit_scopes:
                st.download_button(
                    "📁 全院 個別CSV (ZIP)",
                    data=_build_pl_csv_zip(subunit_scopes, ratio_yms, ratio_label, "全院"),
                    file_name=f"PRIME_構成比_全院_{_safe_filename(ratio_label)}_CSV.zip",
                    mime="application/zip",
                    key="prime_ratio_subunit_csv_zip_dl",
                )


with tab_compare:
    if not existing_yms or target is None:
        st.info(_no_prime_data_message())
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
                "当月": current,
                "前月": prev,
                "前年": prev_year,
                "前月比": current - prev,
                "前月比%": _diff_pct(current, prev),
                "前年比": current - prev_year,
                "前年比%": _diff_pct(current, prev_year),
            })
        st.caption(
            f"当月 {current_ym} ／ 前月 {prev_ym} ／ 前年 {prev_year_ym} ／ {target_label}"
        )
        df_compare = pd.DataFrame(rows)
        amount_cols = ["当月", "前月", "前年", "前月比", "前年比"]
        st.dataframe(
            df_compare.style.apply(_color_profit_or_cost_change, axis=1).format({c: "{:,.0f}" for c in amount_cols}),
            hide_index=True,
            width="stretch",
        )

        st.markdown("### 主要 販管費科目 比較")
        curr_entries = _entries_for_target([current_ym], target)
        prev_entries = _entries_for_target([prev_ym], target)
        prev_year_entries = _entries_for_target([prev_year_ym], target)
        sga_compare_rows = _sga_compare_rows(curr_entries, prev_entries, prev_year_entries)
        if sga_compare_rows:
            df_sga_compare = pd.DataFrame(sga_compare_rows)
            st.dataframe(
                df_sga_compare.style.apply(_color_profit_or_cost_change, axis=1).format({
                    "当月": "{:,.0f}",
                    "前月": "{:,.0f}",
                    "前年": "{:,.0f}",
                    "前月比": "{:,.0f}",
                    "前年比": "{:,.0f}",
                }),
                hide_index=True,
                width="stretch",
                height=min(38 * (len(df_sga_compare) + 1), 620),
            )
        else:
            st.info("比較できる販管費科目がありません。")

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
        st.info(_no_prime_data_message())
    else:
        meeting_ym = st.selectbox("対象月", existing_yms, index=0, key="prime_meeting_ym")
        meeting_prev_ym = _ym_add(meeting_ym, -1)
        meeting_prev_year_ym = _ym_add(meeting_ym, -12)
        available_for_meeting = _available_departments([
            meeting_ym, meeting_prev_ym, meeting_prev_year_ym,
        ])
        rows = _meeting_rows(
            _comparison_defs(available_for_meeting),
            meeting_ym,
            "グループ・エリア",
        )
        kirari_rows = _meeting_rows(
            _scope_defs_for_subunits([meeting_ym, meeting_prev_ym, meeting_prev_year_ym]),
            meeting_ym,
            "きらり院別",
        )
        st.markdown(f"### 業績会議（{meeting_ym} ／ 千円）")
        st.caption(
            f"前月: {meeting_prev_ym} ／ 前年同月: {meeting_prev_year_ym}。"
            " 売上・人件費・経費・営業利益は実績／前月比／前年比、人件費率・営業利益率は当月値です。"
        )
        if rows:
            df_meeting = _format_meeting_df(rows)
            amount_cols = [c for c in df_meeting.columns if c.endswith("実績") or c.endswith("前月比") or c.endswith("前年比")]
            st.dataframe(
                df_meeting.style.apply(_style_meeting, axis=1).format({c: "{:,.0f}" for c in amount_cols}),
                hide_index=True,
                width="stretch",
                height=min(38 * (len(df_meeting) + 1), 620),
            )
        else:
            st.info("対象月にデータがありません。")

        st.markdown("### きらり 院別")
        if kirari_rows:
            df_kirari = _format_meeting_df(kirari_rows)
            amount_cols = [c for c in df_kirari.columns if c.endswith("実績") or c.endswith("前月比") or c.endswith("前年比")]
            st.dataframe(
                df_kirari.style.apply(_style_meeting, axis=1).format({c: "{:,.0f}" for c in amount_cols}),
                hide_index=True,
                width="stretch",
                height=min(38 * (len(df_kirari) + 1), 620),
            )
        else:
            st.info("きらり院別のデータがありません。")

        csv_rows = rows + kirari_rows
        if csv_rows:
            st.download_button(
                "📥 業績会議CSV",
                data=_csv_bytes(csv_rows),
                file_name=f"PRIME_業績会議_{meeting_ym}.csv",
                mime="text/csv",
                key="prime_meeting_csv",
            )


with tab_report:
    if not existing_yms or target is None:
        st.info(_no_prime_data_message())
    else:
        st.markdown("##### 報告書提出 — 収支の振り返り・対策")
        current = auth.current_user() or {}
        current_email = current.get("email") or ""
        current_user_for_report = db.get_user_by_email(current_email) if current_email else None
        default_reporter_name = (
            (current_user_for_report or {}).get("name")
            or current.get("name")
            or ""
        )
        default_reporter_role = (
            (current_user_for_report or {}).get("position")
            or current.get("position")
            or "一般職"
        )
        if default_reporter_role not in REPORTER_ROLES:
            default_reporter_role = "一般職"
        if st.session_state.get("prime_report_prefill_user") != current_email:
            st.session_state.prime_report_name = default_reporter_name
            st.session_state.prime_report_role = default_reporter_role
            st.session_state.prime_report_prefill_user = current_email

        st.markdown(
            """
            <style>
            div[data-testid="stForm"] textarea {
                background: #ffffff !important;
                border: 1.5px solid #94a3b8 !important;
                border-radius: 8px !important;
                box-shadow: inset 0 0 0 1px rgba(148, 163, 184, 0.18) !important;
            }
            div[data-testid="stForm"] textarea:focus {
                border-color: #60a5fa !important;
                box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.22) !important;
            }
            div[data-testid="stForm"] textarea::placeholder {
                color: #94a3b8 !important;
                opacity: 1 !important;
            }
            .emifull-sad {
                margin: 12px 0 18px;
                padding: 22px 24px;
                border: 2px solid #fecaca;
                border-radius: 10px;
                background: #fef2f2;
                color: #b91c1c;
                font-size: 34px;
                font-weight: 800;
                text-align: center;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        if st.button("文章欄だけクリア", key="prime_report_clear_texts"):
            for key in (
                "prime_report_previous", "prime_report_issue",
                "prime_report_actions", "prime_report_other",
            ):
                st.session_state[key] = ""
            st.rerun()

        report_options = _target_options(_available_departments(existing_yms))
        report_option_by_label = {o["label"]: o for o in report_options}
        report_labels = list(report_option_by_label.keys())
        default_target_index = next(
            (
                i for i, option in enumerate(report_options)
                if option.get("key") == target.get("key") and _target_display(option) == target_label
            ),
            0,
        )
        month_idx = existing_yms.index(st.session_state.get("prime_report_month")) if st.session_state.get("prime_report_month") in existing_yms else 0

        with st.form("prime_report_form"):
            c1, c2 = st.columns(2)
            with c1:
                report_ym = st.selectbox(
                    "対象月", existing_yms, index=month_idx, key="prime_report_month"
                )
            with c2:
                report_label = st.selectbox(
                    "対象選択", report_labels, index=default_target_index, key="prime_report_target_label"
                )
            report_target = report_option_by_label[report_label]
            report_target_label = _target_display(report_target)
            report_entries = _entries_for_target([report_ym], report_target)
            report_metrics = _metrics(report_entries)

            st.markdown("###### 数値確認")
            _render_metric_cards(report_metrics)

            c3, c4 = st.columns([1, 2])
            with c3:
                reporter_role = st.selectbox(
                    "役職",
                    REPORTER_ROLES,
                    index=REPORTER_ROLES.index(default_reporter_role),
                    key="prime_report_role",
                )
            with c4:
                reporter_name = st.text_input("氏名", key="prime_report_name")

            previous_review = st.text_area(
                "① 前月の振り返り",
                height=140,
                key="prime_report_previous",
                placeholder="例：前月に決めた予約確認・欠席フォローを実施し、売上や利益は改善しました。一方で、現場負担や経費の課題が残りました。",
            )
            issue_review = st.text_area(
                "② 現在の課題",
                height=150,
                key="prime_report_issue",
                placeholder="例：売上の伸び悩み、人件費率の上昇、経費の増加、営業利益率の低下など、今月時点で利益や運営に影響している課題を記入してください。",
            )
            next_actions = st.text_area(
                "③ 次月以降の対策",
                height=150,
                key="prime_report_actions",
                placeholder="例：予約枠の見直し、シフト調整、経費の見直し、担当者・期限・確認方法などを会議で共有しやすい形で記入してください。",
            )
            other_notes = st.text_area(
                "④ その他（任意）",
                height=110,
                key="prime_report_other",
                placeholder="例：採用状況、設備修繕、関係機関連携、顧客動向など、会議で共有したいことがあれば記入してください。",
            )
            love = st.selectbox(
                "送信前の確認：EMIFULLは大好きですか？",
                ["", "はい", "もちろんです", "いいえ"],
                key="prime_report_love",
            )
            submitted = st.form_submit_button("報告書を提出", type="primary")

        if submitted:
            if not love:
                st.warning("EMIFULL愛の確認がまだです。ここだけは外せません。")
            elif love == "いいえ":
                st.markdown("<div class='emifull-sad'>悲しいです。</div>", unsafe_allow_html=True)
            elif not reporter_name.strip():
                st.warning("氏名を入力してください。")
            elif not previous_review.strip() or not issue_review.strip() or not next_actions.strip():
                st.warning("前月の振り返り、現在の課題、次月以降の対策を入力してください。")
            else:
                report_row = {
                    "提出日時": pd.Timestamp.now(tz="Asia/Tokyo").strftime("%Y-%m-%d %H:%M"),
                    "対象月": report_ym,
                    "対象": report_target_label,
                    "会計年度基準": fy_mode_label,
                    "役職": reporter_role,
                    "氏名": reporter_name.strip(),
                    "売上高": report_metrics["売上高"],
                    "人件費": report_metrics["人件費"],
                    "経費": report_metrics["経費"],
                    "営業利益": report_metrics["営業利益"],
                    "経常利益": report_metrics["経常利益"],
                    "営業利益率": _pct(report_metrics["営業利益"], report_metrics["売上高"]),
                    "人件費率": _pct(report_metrics["人件費"], report_metrics["売上高"]),
                    "前月の振り返り": previous_review.strip(),
                    "現在の課題": issue_review.strip(),
                    "次月以降の対策": next_actions.strip(),
                    "その他": other_notes.strip(),
                }
                submissions = list(st.session_state.get("prime_report_submissions", []))
                submissions.insert(0, report_row)
                st.session_state.prime_report_submissions = submissions
                st.session_state.prime_report_latest = report_row
                st.success("一緒に人生咲かそう")

        latest_report = st.session_state.get("prime_report_latest")
        if latest_report:
            st.download_button(
                "📥 この報告書CSV",
                data=_csv_bytes([latest_report]),
                file_name=f"PRIME_報告書_{latest_report['対象月']}_{_safe_filename(latest_report['対象'])}.csv",
                mime="text/csv",
                key="prime_report_latest_csv",
            )

        st.markdown("---")
        st.markdown("##### 自分の提出内容")
        submissions = list(st.session_state.get("prime_report_submissions", []))
        if submissions:
            st.dataframe(pd.DataFrame(submissions), hide_index=True, width="stretch")
            st.download_button(
                "📥 入力済み報告書CSV",
                data=_csv_bytes(submissions),
                file_name="PRIME_報告書_入力済み.csv",
                mime="text/csv",
                key="prime_report_all_csv",
            )
        else:
            st.info("まだこの画面で作成した報告書はありません。")

auth.render_sidebar_user_box()
