# -*- coding: utf-8 -*-
"""Claude Vision API でレシート画像を解析して構造化データを返す。

戻り値は「行」のリスト(税区分が混在するレシートは複数行に分割)。
各行の dict キー:
  date(YYYY-MM-DD), store, facility_handwritten, facility_code(マスター照合後),
  facility_corp(EMI/のじ), account, tax, amount, summary, raw_total, confidence,
  needs_review(bool), notes
"""
from __future__ import annotations
import base64
import io
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from PIL import Image

from master_data import FacilityMaster, AccountMaster

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
with open(CONFIG_DIR / "settings.json", encoding="utf-8") as f:
    SETTINGS = json.load(f)

PRIMARY_MODEL = SETTINGS.get("anthropic_model", "claude-opus-4-7")
FALLBACK_MODEL = SETTINGS.get("anthropic_model_fallback", "claude-sonnet-4-6")
MAX_LONG_EDGE = int(SETTINGS.get("max_image_long_edge_px", 2000))

PROMPT_TEMPLATE = """日本のレシート画像を解析してください。1枚の写真に複数のレシートが写っている場合があります。各レシートを別オブジェクトとして抽出してください。

【施設名の手書き読み取り(超重要)】
- 施設名は「平仮名・カタカナ・漢字・ローマ字」のいずれでも書かれます。書き手によって表記が大きくゆれます。
- 例えば次は全部「UMIEてんり」を意図します:
   「UMIEてんり」「ウミエてんり」「うみえてんり」「UMIE天理」「ウミエ天理」「umie tenri」「umietenri」
- 「カラダキッズてんり」を意図する書き方の例:
   「カラダキッズてんり」「からだきっずてんり」「Karada Kids 天理」「KarakaKids天理」(誤記混じり) など
- 「のじぎく高砂」「ノジギク高砂」「野路菊高砂」「nojigiku takasago」も全て同じ施設。
- 多少の誤字・崩し字・くせ字があっても意図を汲み取って『手書き原文』をそのまま `facility_handwritten` に転記してください(後段システムが正規化します)。
- 単一施設なら `allocation: null`、`facility_handwritten` のみ設定。

【按分の判定(超重要)】
- 余白に「複数施設名 + 按分(あんぶん)を示す指示」が書かれていれば按分扱い。
- 「按分」の表記ゆれ・漢字誤りは全部受け入れてください。下記いずれも按分の意図とみなす:
   漢字: 「按分」「按文」「案分」「案文」「庵分」「庵文」「安分」「安文」「按份」(按が崩れた字も含む)
   ひらがな: 「あんぶん」「あんふん」「あんぶ」「あんぷん」
   カタカナ(全角・半角): 「アンブン」「アンプン」「ｱﾝﾌﾞﾝ」「アンブ」
   その他の同義表現: 「均等割」「均等」「等分」「割る」「分ける」「分割」「シェア」「共通」「共有」「共」「split」「share」「合算」「割勘」
- 数の書き方も全部受け入れる: 「3按分」「3あんぶん」「3均等」「3施設で按分」「3個」「三按分」「3 way split」など。
- 区切り文字も自由: 「、」「，」「,」「・」「/」「と」「&」「+」「\\n(改行)」など。
- 例えば次は全て「3施設按分」:
   「SORATOてんり、UMIEてんり、カラダキッズてんり 3按分」
   「SORATO天理／UMIE天理／カラダキッズ天理 3あんぶん」
   「ソラトてんり・ウミエてんり・カラダキッズ 3個で按分」
- 按分の場合は `allocation = {"facilities": [手書き原文1, 手書き原文2, ...], "count": N}` を設定し、`facility_handwritten` は null にしてください。
- 数 N と facilities の長さが一致しないときは、明示された数 N を優先 (count=N、facilitiesはそのまま全部記載)。
- 「○○用」「○○分」のような単発の用途明記は按分ではない。単一施設扱い。

【レシート本体】
- 各レシートに対して、税区分(8%軽減 / 10%標準 / 0=対象外)ごとに行を分けて返してください。
  - AEONレシートに「外税8%対象額¥7,352, 合計¥7,940」→ 軽減税率8%で7,940円の1行
  - 8%対象と10%対象が混在する場合は2行に分割し、各税区分の合計金額を返す
  - 「対象外(非課税取引)」の代表例(これは tax_rate=0 として返す):
      ・収入印紙 / 印紙税
      ・切手の購入そのもの(郵便局カウンターで「切手代」)も会計処理上は対象外扱いにする会社が多いが、運用方針による。原則は『印紙の購入のみ tax_rate=0、切手は10%』として返す。
      ・慶弔費(現金支給の香典・祝儀)
      ・給与・賃金の支払い
      ・税金・公共料金の一部(自動車税、固定資産税、保険料等)
      ・寄付金
      ・海外取引
  - 同じレシート内に課税品目と対象外品目が混在する場合は、それぞれ別の line として返してください
    例: 郵便局で「切手 500円 + 収入印紙 200円」→ {tax_rate:10, amount:500} と {tax_rate:0, amount:200} の2行
- 日付は精算日。和暦/西暦どちらでも `YYYY-MM-DD` に変換。
- レシートに精算時刻が印字されていれば `time` に "HH:MM" 形式で入れる。
  - 駐車場: 「精算日時 18:07」 → time="18:07"
  - 食料品: 「2026/5/1(金) 9:45」 → time="09:45"
  - 時刻が読み取れない場合は null。
- 店舗名(store)はレシート上部の正式名称。「株式会社」「㈱」等の法人格は省いてOK。
- amount は税込合計(円・整数)。
- 駐車場レシート(タイムズ等)は通常1行のみ。
- ガソリンスタンド(吉田石油・エネオス等)は燃料費。

【購入商品の明細抽出(超重要)】
各税区分行(line)に対して、その区分に属する購入商品を `top_items` として返してください。
- 商品を「金額の高い順」に並べる(同額の場合は並び順任意)
- 最低 5 件、可能なら全商品を返す(レシート全体が5件未満ならその全部)
- 各商品は {"name": "商品名", "price": 単価合計(整数円)}
- 商品名はレシート印字を基本とするが、略称・略号・誤字に見える文字は『推測して正式名称に補完』してOK:
  - 「ＴＶサクチョコチクッキ」→「TVサクサクチョコクッキー」
  - 「カントリーバニラココア」→「カントリーマアム バニラ」(意図が読み取れる場合)
  - 「BPスティックゼリー」→「ブルボン プチスティックゼリー」(BP=ブルボンプチの省略推測)
  - 不明確な場合は印字のまま残す。価格は印字のまま正確に。
- 価格は1点あたりの単価ではなく、その品目の支払額(数量×単価)を返してください。
  - 例「(@100×3個) ¥300」→ price=300
- レシートで「※」印のある商品は軽減税率(8%)対象、無印は10%対象。商品の振り分けに使ってください。

【絶対ルール】
- 必ず JSON のみを返してください(コードブロックや解説不要)。

【出力形式】
{
  "facility_handwritten": "手書きの施設名(単一の場合。按分の場合はnull)",
  "allocation": null または {"facilities": ["手書き原文1","手書き原文2","..."], "count": 3},
  "receipts": [
    {
      "store": "店舗名",
      "date": "YYYY-MM-DD",
      "time": "HH:MM",  // 印字時刻(なければnull)
      "lines": [
        {
          "tax_rate": 8,
          "amount": 7940,
          "category_hint": "食材",
          "top_items": [
            {"name": "東ハトポテコうましお味", "price": 1746},
            {"name": "どうぶつえんゼリー", "price": 1576},
            {"name": "とろけるスライス", "price": 1071},
            {"name": "すいぞくかんゼリー", "price": 788},
            {"name": "カントリーバニラココア", "price": 771}
          ]
        }
      ],
      "confidence": "high"
    }
  ]
}
"""


def _format_summary_with_items(base_summary: str, top_items: list[dict], min_items: int = 5) -> str:
    """店舗+用途の摘要末尾に、購入商品の高額順リストを追加する。
    base_summary 例: 'イオンビッグ㈱　おやつ代'
    top_items: [{'name':'ハッピーターン','price':628}, ...] (既に高額順)
    出力例: 'イオンビッグ㈱　おやつ代 / ハッピーターン¥628, 雪の宿サラダ¥334, ...'
    商品が min_items 未満なら全部、min_items 以上なら全部入れる(切り捨てしない)。
    """
    if not top_items:
        return base_summary
    parts = []
    for it in top_items:
        nm = str(it.get("name", "")).strip()
        pr = it.get("price")
        if not nm:
            continue
        if pr is not None:
            try:
                parts.append(f"{nm}¥{int(pr):,}")
            except (TypeError, ValueError):
                parts.append(nm)
        else:
            parts.append(nm)
    if not parts:
        return base_summary
    items_str = ", ".join(parts)
    return f"{base_summary} / {items_str}"


def _shrink_image_b64(image_path: Path) -> tuple[str, str]:
    """画像を long_edge=MAX_LONG_EDGE まで縮小してbase64化。(b64, media_type) を返す。"""
    img = Image.open(image_path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")
    w, h = img.size
    long_edge = max(w, h)
    if long_edge > MAX_LONG_EDGE:
        scale = MAX_LONG_EDGE / long_edge
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    return b64, "image/jpeg"


def _call_claude(image_path: Path) -> dict:
    import anthropic
    client = anthropic.Anthropic()
    b64, media_type = _shrink_image_b64(image_path)

    last_error = None
    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64",
                                                     "media_type": media_type, "data": b64}},
                        {"type": "text", "text": PROMPT_TEMPLATE},
                    ],
                }],
            )
            text = resp.content[0].text.strip()
            # コードブロック除去
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
            return json.loads(text)
        except Exception as e:
            last_error = e
            continue
    raise RuntimeError(f"Claude API failed for {image_path}: {last_error}")


def analyze(image_path: Path, expected_corp: Optional[str] = None,
            forced_facility_code: Optional[str] = None) -> dict:
    """レシート画像を解析し、{rows:[...], allocation:{...}|None} を返す。

    rows: Excel書き込み用の行リスト(税区分ごとに分割済み)
    allocation: 按分指定がある場合 {"facility_codes":[...], "count":N, "raw_handwritten":[...]} を返す。
                按分なしの場合は None。
    forced_facility_code が指定された場合(フォルダから施設特定)、単一施設として扱い、
    按分情報があってもそれは無視される(按分は手書きベースの機能なので)。
    """
    fm = FacilityMaster()
    am = AccountMaster()
    raw = _call_claude(image_path)
    handwritten = raw.get("facility_handwritten")
    allocation_raw = raw.get("allocation")

    # フォルダから施設が確定している場合: forcedを優先
    if forced_facility_code:
        # forced_facility_code から法人を逆引き
        forced_corp = None
        for ck, cv in fm.corps.items():
            for fac in cv["facilities"]:
                if fac["code"] == forced_facility_code:
                    forced_corp = ck
                    break
            if forced_corp:
                break
        corp_key = forced_corp or expected_corp or "EMI"
        code = forced_facility_code
        fac_lookup = (corp_key, code)
        # 手書きと食い違ったら警告ログ用に保存
        if handwritten:
            hw_lookup = fm.lookup(handwritten, prefer_corp=corp_key)
            if hw_lookup and hw_lookup[1] != code:
                # フォルダ vs 手書き が不一致 → フォルダ優先だが要確認
                pass  # 後段で needs_review 判定に反映
    else:
        fac_lookup = fm.lookup(handwritten or "", prefer_corp=expected_corp)
        if fac_lookup:
            corp_key, code = fac_lookup
        else:
            corp_key, code = (expected_corp or "EMI"), "(要確認)"

    rows: list[dict] = []
    for r in raw.get("receipts", []):
        store = r.get("store", "")
        d_str = r.get("date")
        t_str = r.get("time")  # "HH:MM" 形式
        try:
            d = datetime.fromisoformat(d_str).date() if d_str else None
        except Exception:
            d = None
        # 時刻の正規化("9:45"→"09:45", "18:7"→"18:07")
        normalized_time = None
        if t_str:
            m = re.match(r"^\s*(\d{1,2})[:：](\d{1,2})", str(t_str))
            if m:
                hh, mm = int(m.group(1)), int(m.group(2))
                if 0 <= hh < 24 and 0 <= mm < 60:
                    normalized_time = f"{hh:02d}:{mm:02d}"
        confidence = r.get("confidence", "medium")

        for line in r.get("lines", []):
            tax_rate = line.get("tax_rate")
            amount = int(line.get("amount", 0))
            top_items = line.get("top_items") or []
            # 商品リストを高額順にソート(API側でやってるはずだが念のため)
            top_items_sorted = sorted(
                [it for it in top_items if it.get("name")],
                key=lambda it: (-int(it.get("price") or 0))
            )
            classification = am.classify(store)
            account = classification["account"]
            tax = classification["tax"]
            base_summary = classification["summary"]
            # 商品リストを摘要末尾に追加
            summary = _format_summary_with_items(base_summary, top_items_sorted, min_items=5)
            # 税率不一致時の補正
            try:
                tr_int = int(tax_rate) if tax_rate is not None else None
            except (TypeError, ValueError):
                tr_int = None
            if tr_int == 0:
                tax = "対象外"
            elif tr_int == 8 and "8%" not in tax:
                tax = "課対仕入8%（軽）"
            elif tr_int == 10 and "10%" not in tax:
                tax = "課対仕入10%"
            needs_review = (
                d is None
                or amount <= 0
                or fac_lookup is None
                or not classification["matched"]
                or confidence == "low"
            )
            # フォルダ vs 手書き の不一致は要確認(混入リスク)
            if forced_facility_code and handwritten:
                hw_check = fm.lookup(handwritten, prefer_corp=corp_key)
                if hw_check and hw_check[1] != forced_facility_code:
                    needs_review = True
            rows.append({
                "date": d,
                "time": normalized_time,
                "store": store,
                "facility_handwritten": handwritten,
                "facility_code": code,
                "facility_corp": corp_key,
                "account": account,
                "tax": tax,
                "amount": amount,
                "summary": summary,
                "raw_total": amount,
                "confidence": confidence,
                "needs_review": needs_review,
                "notes": "" if not needs_review else "要確認",
            })

    # 按分情報を構造化(forced施設指定がない場合のみ採用)
    allocation_info = None
    if not forced_facility_code and allocation_raw and isinstance(allocation_raw, dict):
        raw_facilities = allocation_raw.get("facilities", []) or []
        count = int(allocation_raw.get("count") or len(raw_facilities) or 0)
        codes: list[str] = []
        unresolved: list[str] = []
        first_corp = None
        for nm in raw_facilities:
            lk = fm.lookup(nm)
            if lk:
                codes.append(lk[1])
                if first_corp is None:
                    first_corp = lk[0]
            else:
                unresolved.append(nm)
        # count と facilities の数が違う場合、count を優先せず実際の解決済施設数で動かす
        if codes and (count == 0 or count != len(codes)):
            count = len(codes)
        if codes:
            allocation_info = {
                "facility_codes": codes,
                "count": count,
                "raw_handwritten": raw_facilities,
                "unresolved": unresolved,
            }

    return {"rows": rows, "allocation": allocation_info}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("Usage: python analyze_receipt.py <image_path> [EMI|のじ]")
        sys.exit(1)
    p = Path(sys.argv[1])
    expected = sys.argv[2] if len(sys.argv) > 2 else None
    result = analyze(p, expected_corp=expected)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
