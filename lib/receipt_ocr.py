"""レシート画像 OCR モジュール (Claude Vision API)

役割:
  - HEIC / JPG / PNG / PDF のレシート画像を Claude Vision に投げて構造化抽出
  - デビットExcelに追記しやすい形 (date / vendor / amount / items / 推定勘定科目 等) で返す

主要API:
  analyze_receipt(image_bytes, ext, facility_hint=None) -> dict
  is_available() -> bool   # API キー設定済みか

設定:
  ANTHROPIC_API_KEY 環境変数、Streamlit Secrets、または
  config/anthropic.json の {"api_key": "..."}
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "anthropic.json"

# 画像長辺の上限 (Claude API の制約に合わせた縮小)
MAX_IMAGE_SIDE = 1568

# PDFは全ページをOCR対象にする。多すぎる場合は費用・精度面から手動確認へ回す。
MAX_PDF_PAGES = 20

# モデル選定
DEFAULT_MODEL = "claude-sonnet-4-5"


def _friendly_api_error(error: Exception) -> str:
    text = str(error)
    low = text.lower()
    if "organization has been disabled" in low:
        return (
            "Anthropic APIキーの組織が無効化されています。"
            "config/anthropic.json の api_key を有効なキーに差し替えてから再実行してください。"
        )
    if "invalid x-api-key" in low or "authentication" in low or "api key" in low:
        return (
            "Anthropic APIキーが無効です。"
            "config/anthropic.json の api_key を確認してください。"
        )
    if "credit balance" in low or "billing" in low:
        return (
            "Anthropic APIの請求/残高設定に問題があります。"
            "Claude Console の Billing と使用制限を確認してください。"
        )
    return f"API呼び出し失敗: {text}"


# 想定される勘定科目 / 税区分 (ユーザの実Excelデータより抽出)
KNOWN_ACCOUNTS = [
    "食材費", "消耗品費", "燃料費", "旅費交通費",
    "会議費", "福利厚生費", "事務用品費", "通信費",
    "雑費", "車両費", "修繕費",
]
KNOWN_TAX_CLASSES = [
    "課対仕入10%",
    "課対仕入8%（軽）",
]


def _load_api_key_from_config() -> str | None:
    if CONFIG_PATH.exists():
        try:
            obj = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            v = obj.get("api_key")
            if v:
                return str(v).strip()
        except Exception:
            return None
    return None


def api_key_source() -> str | None:
    """Return the OCR API key source without exposing the key."""
    if _load_api_key_from_config():
        return "config/anthropic.json"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "environment"
    if _load_api_key_from_streamlit_secrets():
        return "streamlit secrets"
    return None


def _load_api_key_from_streamlit_secrets() -> str | None:
    try:
        import streamlit as st
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        return None
    if key:
        return str(key).strip()
    return None


def _load_api_key() -> str | None:
    """Load OCR API key. Prefer config over env, then Streamlit Secrets."""
    key = _load_api_key_from_config()
    if key:
        return key
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    key = _load_api_key_from_streamlit_secrets()
    if key:
        return key
    return None


def is_available() -> bool:
    return _load_api_key() is not None


def _heic_to_jpeg(image_bytes: bytes) -> bytes:
    """HEIC/HEIF → JPEG 変換。pillow-heif 必須。"""
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except Exception:
        pass
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":
            im = im.convert("RGB")
        if max(im.size) > MAX_IMAGE_SIDE:
            im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()


def _generic_to_jpeg(image_bytes: bytes) -> bytes:
    """任意画像 → JPEG (大きすぎたら縮小)。"""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if max(im.size) > MAX_IMAGE_SIDE:
            im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88, optimize=True)
        return buf.getvalue()


def _pdf_pages_to_jpegs(pdf_bytes: bytes) -> list[bytes]:
    """PDF全ページをJPEGバイト列へ変換する。"""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count == 0:
            raise ValueError("PDFにページがありません")
        if doc.page_count > MAX_PDF_PAGES:
            raise ValueError(
                f"PDFのページ数が多すぎます: {doc.page_count}ページ "
                f"(上限 {MAX_PDF_PAGES}ページ)"
            )

        pages: list[bytes] = []
        for idx in range(doc.page_count):
            page = doc.load_page(idx)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            pages.append(_generic_to_jpeg(pix.tobytes("png")))
        return pages
    except Exception as exc:
        raise NotImplementedError(
            f"PDF レシートの画像化に失敗しました: {exc}"
        ) from exc


def _normalize_images(image_bytes: bytes, ext: str) -> list[tuple[bytes, str]]:
    """API 投入用に画像を正規化し、[(bytes, media_type), ...] を返す。

    PDFの場合は先頭だけでなく全ページを返す。
    """
    e = ext.lower()
    if e in (".pdf",):
        return [(page_bytes, "image/jpeg") for page_bytes in _pdf_pages_to_jpegs(image_bytes)]
    normalized, media_type = _normalize_image(image_bytes, ext)
    return [(normalized, media_type)]


def _normalize_image(image_bytes: bytes, ext: str) -> tuple[bytes, str]:
    """API 投入用に JPEG/PNG に正規化し、(bytes, media_type) を返す。

    - HEIC/HEIF: JPEG に変換
    - PNG/JPG/WEBP/BMP/TIFF: 必要なら縮小して JPEG 化
    - PDF: 全ページ対応は _normalize_images() を使用
    """
    e = ext.lower()
    if e in (".heic", ".heif"):
        return _heic_to_jpeg(image_bytes), "image/jpeg"
    if e in (".pdf",):
        pages = _pdf_pages_to_jpegs(image_bytes)
        return pages[0], "image/jpeg"
    # 標準画像
    return _generic_to_jpeg(image_bytes), "image/jpeg"


def _build_prompt(facility_hint: str | None, pass_no: int = 1) -> str:
    """Pass 1/2 で観点を変えたプロンプトを生成し、独立した抽出結果を得る。

    画像に **複数のレシートが写っている場合は、全てを別個に抽出**する。
    例: スーパー2枚 + 薬局1枚 が並んでいる画像 → 3件の receipt エントリ。
    """
    accounts = " / ".join(KNOWN_ACCOUNTS)
    tax_classes = " / ".join(KNOWN_TAX_CLASSES)
    hint_block = ""
    if facility_hint:
        hint_block = (
            f"\n[参考情報] このレシートは「{facility_hint}」フォルダに保存されています。"
            "用途の推定にこの施設名(部門名)を考慮してください。\n"
        )

    # Pass ごとに観点をずらす
    if pass_no == 2:
        focus_block = (
            "[今回の観点] 文字を一文字ずつ慎重に読んでください。\n"
            "- 店舗名/事業者名は「領収書」「レシート」のヘッダー直下や"
            "電話番号/住所の近くに小さく書かれていることが多い。\n"
            "- 「○○株式会社」「㈱○○」「(株)○○」「合同会社○○」などの法人形態を見落とさない。\n"
            "- 大きく目立つ文字 (例: ロゴ画像周辺、商品名) を vendor と勘違いしない。\n"
            "- 取引総額は「合計」「お会計」「クレジット」「カード支払」の行を最優先。\n"
            "- 日付は「ご利用日」「取扱日」「発行年月日」の値を採用。\n"
            "- 複数のレシートが横並び/重ね撮りされている場合は、**それぞれ別のレシートとして全件抽出**する。\n"
            "- 店舗ロゴ (例: 「ジャパン」「スギ薬局」「ダイソー」「フレッシュ石守」「イオン」など) を見て、"
            "  どのレシートのどの店舗かを正確に対応付ける。隣のレシートと取り違えない。\n"
        )
    else:  # pass 1
        focus_block = (
            "[今回の観点] 画像に写っている **全てのレシートを別個に** 抽出してください。\n"
            "1. 写っているレシートの枚数を数える (横並び/重ね撮りに注意)\n"
            "2. 各レシートについて発行元/日付/合計/品目を独立して読み取る\n"
            "3. 1枚の画像でも、レシートが N 枚あれば receipts 配列に N 要素を出力\n"
            "4. ロゴと合計金額のペアが、左右のレシートで入れ違いにならないよう注意\n"
        )

    return (
        "あなたは日本のレシート/領収書を分析する経理担当者です。"
        "画像に写っている **全てのレシート** から取引情報を抽出し、純粋なJSONで返してください。"
        "\n\n"
        f"{focus_block}\n"
        "[抽出ルール - 各レシートごとに適用]\n"
        "- 1枚の画像に複数レシート (重ね撮り/横並び) が写っている場合は全件抽出する。**「最も大きい1枚だけ」では絶対にない**。\n"
        "- 金額は税込合計 (整数、円)。クレジット決済の最終金額を採用。\n"
        "- 日付はレシートの「ご利用日」「発行日」を YYYY-MM-DD で。和暦は西暦に変換。読めない場合は null。\n"
        "- vendor は店舗名/事業者名 (例: フレッシュ石守、ジャパン、ダイソー、㈱吉田石油、日本郵便株式会社)。\n"
        "  ロゴや背景の大文字テキストではなく、レシート上部の発行元として明記された名称を採用。\n"
        "  クレジット明細 (加盟店 / カード会社) の名前と、実店舗名を混同しない。\n"
        "- items は主要な品目を最大10件まで [{name, price}] で。明細が見えなければ空配列。\n"
        f"- suggested_account は次のいずれかを推定: {accounts}\n"
        f"- suggested_tax_class は次のいずれかを推定: {tax_classes}\n"
        "  食料品(軽減税率対象) は '課対仕入8%（軽）'、それ以外は '課対仕入10%' が原則。\n"
        "- purpose は支出の用途を簡潔に (例: 'ガソリン代', '事務消耗品', '食材', '郵便切手代')。\n"
        "- confidence は high / medium / low。読めない箇所が多いほど低くする。\n"
        "- notes は読み取れなかった項目や注意点を1-2行で。なければ null。\n"
        f"{hint_block}\n"
        "[出力JSONスキーマ - receipts 配列で複数件返す]\n"
        "{\n"
        '  "receipts": [\n'
        "    {\n"
        '      "date": "YYYY-MM-DD" | null,\n'
        '      "vendor": "..." | null,\n'
        '      "amount": 整数 | null,\n'
        '      "items": [{"name": "...", "price": 整数}],\n'
        '      "purpose": "..." | null,\n'
        '      "suggested_account": "..." | null,\n'
        '      "suggested_tax_class": "..." | null,\n'
        '      "confidence": "high" | "medium" | "low",\n'
        '      "notes": "..." | null\n'
        "    },\n"
        "    ...\n"
        "  ],\n"
        '  "total_receipts": 整数  (receipts 配列の長さ)\n'
        "}\n\n"
        "出力は純粋なJSONのみ。コードブロック (```json) や前置き/後置きの説明文は不要です。"
    )


def _build_arbitration_prompt(
    facility_hint: str | None,
    pass1: dict,
    pass2: dict,
) -> str:
    """Pass 3: Pass 1とPass 2の結果を提示し、画像と照合して最終判定するプロンプト。"""
    accounts = " / ".join(KNOWN_ACCOUNTS)
    tax_classes = " / ".join(KNOWN_TAX_CLASSES)
    hint = ""
    if facility_hint:
        hint = f"\n施設名 (部門): 「{facility_hint}」\n"
    return (
        "あなたは日本のレシート/領収書の最終チェック担当者です。\n"
        "下記は同じ画像に対して別々に解析された **2件の独立した抽出結果** です。"
        "矛盾があるはずです。\n\n"
        f"{hint}\n"
        f"[Pass 1 の結果]\n{json.dumps(pass1, ensure_ascii=False, indent=2)}\n\n"
        f"[Pass 2 の結果]\n{json.dumps(pass2, ensure_ascii=False, indent=2)}\n\n"
        "[あなたのタスク]\n"
        "画像をもう一度しっかり見直して、**写っているレシートの正確な枚数と、各レシートの正しい値**を確定してください。\n"
        "- レシート枚数: Pass 1 と Pass 2 で異なる場合、画像を再確認して正しい枚数を採用する。\n"
        "- 各レシートで vendor (店舗名) を慎重に判定。法人格 (「株式会社」「㈱」「(株)」など) を含む正式名を採用。\n"
        "- 隣り合うレシートで、ロゴと合計金額の対応が間違っていないか確認 (例: ジャパン¥981 / フレッシュ石守¥16,414 が入れ違いになっていないか)。\n"
        "- 金額は領収書最下部の「合計」「お会計」「クレジット」の値を最優先。\n"
        "- 不一致が多いなら各レシートの confidence は medium 以下に。\n\n"
        f"- suggested_account は次のいずれかから選ぶ: {accounts}\n"
        f"- suggested_tax_class は次のいずれかから選ぶ: {tax_classes}\n\n"
        "[出力JSONスキーマ - 全レシートを receipts 配列で返す]\n"
        "{\n"
        '  "receipts": [\n'
        "    {\n"
        '      "date": "YYYY-MM-DD" | null,\n'
        '      "vendor": "..." | null,\n'
        '      "amount": 整数 | null,\n'
        '      "items": [{"name": "...", "price": 整数}],\n'
        '      "purpose": "..." | null,\n'
        '      "suggested_account": "..." | null,\n'
        '      "suggested_tax_class": "..." | null,\n'
        '      "confidence": "high" | "medium" | "low",\n'
        '      "notes": "Pass間の不一致点と判定根拠を簡潔に記載" | null\n'
        "    },\n"
        "    ...\n"
        "  ],\n"
        '  "total_receipts": 整数,\n'
        '  "agreement": {\n'
        '    "count_match": true|false,\n'
        '    "vendors_match": true|false,\n'
        '    "amounts_match": true|false\n'
        '  }\n'
        "}\n\n"
        "出力は純粋なJSONのみ。"
    )


def _extract_json(text: str) -> dict[str, Any]:
    """モデル出力テキストからJSONを抽出。コードブロック/前置きを許容。"""
    s = (text or "").strip()
    # ```json ... ``` 形式を剥がす
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
    if m:
        s = m.group(1)
    else:
        # 最初の '{' 〜 最後の '}' を抽出
        i = s.find("{")
        j = s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j + 1]
    return json.loads(s)


def _call_claude_vision(
    client,
    model: str,
    images: list[tuple[str, str]],
    prompt: str,
) -> dict:
    """Claude Vision API を1回だけ叩いてJSON抽出する内部ヘルパ。"""
    content = []
    if len(images) > 1:
        content.append({
            "type": "text",
            "text": (
                f"以下は同じPDFの全{len(images)}ページです。"
                "1ページ目だけでなく、2ページ目以降も必ず確認してください。"
            ),
        })
    for idx, (media_type, b64_image) in enumerate(images, start=1):
        if len(images) > 1:
            content.append({"type": "text", "text": f"[PDF {idx}ページ目]"})
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64_image,
            },
        })
    content.append({"type": "text", "text": prompt})

    try:
        msg = client.messages.create(
            model=model,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": content,
            }],
        )
    except Exception as e:
        return {"ok": False, "error": _friendly_api_error(e)}

    text_chunks = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
    raw_text = "\n".join(text_chunks).strip()
    try:
        data = _extract_json(raw_text)
    except Exception as e:
        return {"ok": False, "error": f"JSON解析失敗: {e}", "raw_text": raw_text}

    return {
        "ok": True,
        "data": _post_process(data),
        "model": getattr(msg, "model", model),
        "source_page_count": len(images),
        "usage": {
            "input_tokens": getattr(msg.usage, "input_tokens", 0),
            "output_tokens": getattr(msg.usage, "output_tokens", 0),
        },
        "raw_text": raw_text,
    }


def analyze_receipt(
    image_bytes: bytes,
    ext: str,
    facility_hint: str | None = None,
    model: str = DEFAULT_MODEL,
    triple_check: bool = True,
) -> dict[str, Any]:
    """レシート画像を OCR 解析し、構造化結果を返す。

    triple_check=True (デフォルト) の場合は3パス方式:
      Pass 1: 標準プロンプトで抽出
      Pass 2: 文字を慎重に読む観点で再抽出
      Pass 3: Pass 1 と Pass 2 を提示し、最終判定 (アービトレーション)
    triple_check=False の場合は Pass 1 のみ実行 (高速・低コスト)。

    Returns:
        {
          'ok': True,
          'data': { date, vendor, amount, items, purpose,
                    suggested_account, suggested_tax_class,
                    confidence, notes },
          'model': 'claude-...',
          'usage': {input_tokens, output_tokens},
          'triple_check': {pass1, pass2, agreement},  # triple_check時のみ
        }
        失敗時は {'ok': False, 'error': '...'}
    """
    api_key = _load_api_key()
    if not api_key:
        return {"ok": False, "error": "ANTHROPIC_API_KEY が未設定です"}

    try:
        normalized_images = _normalize_images(image_bytes, ext)
    except NotImplementedError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": f"画像変換失敗: {e}"}

    try:
        import anthropic
    except ImportError:
        return {"ok": False, "error": "anthropic SDK が未インストールです"}

    client = anthropic.Anthropic(api_key=api_key)
    images = [
        (media_type, base64.standard_b64encode(img).decode("ascii"))
        for img, media_type in normalized_images
    ]

    # ---- Pass 1 ----
    res1 = _call_claude_vision(
        client, model, images,
        _build_prompt(facility_hint, pass_no=1),
    )
    if not res1.get("ok"):
        return res1

    if not triple_check:
        return res1

    # ---- Pass 2 ----
    res2 = _call_claude_vision(
        client, model, images,
        _build_prompt(facility_hint, pass_no=2),
    )
    if not res2.get("ok"):
        # Pass 2 が失敗したら Pass 1 の結果を返す (劣化対応)
        return {
            **res1,
            "triple_check": {
                "pass1": res1["data"],
                "pass2_error": res2.get("error"),
            },
        }

    # ---- Pass 3 (アービトレーション) ----
    arb = _call_claude_vision(
        client, model, images,
        _build_arbitration_prompt(facility_hint, res1["data"], res2["data"]),
    )
    if not arb.get("ok"):
        return {
            **res1,
            "triple_check": {
                "pass1": res1["data"],
                "pass2": res2["data"],
                "pass3_error": arb.get("error"),
            },
        }

    final_data = arb["data"]

    # 一致状況をフラグとして残す
    agreement = final_data.get("agreement") or {
        "vendor_match": (res1["data"].get("vendor") == res2["data"].get("vendor")),
        "amount_match": (res1["data"].get("amount") == res2["data"].get("amount")),
        "date_match": (res1["data"].get("date") == res2["data"].get("date")),
    }
    # 出力データから agreement を取り除く (純粋な抽出データのみに)
    final_data.pop("agreement", None)

    # トークン使用量を集計
    total_in = (
        res1["usage"]["input_tokens"]
        + res2["usage"]["input_tokens"]
        + arb["usage"]["input_tokens"]
    )
    total_out = (
        res1["usage"]["output_tokens"]
        + res2["usage"]["output_tokens"]
        + arb["usage"]["output_tokens"]
    )

    return {
        "ok": True,
        "data": final_data,
        "model": arb.get("model", model),
        "source_page_count": len(images),
        "usage": {"input_tokens": total_in, "output_tokens": total_out},
        "raw_text": arb.get("raw_text"),
        "triple_check": {
            "pass1": res1["data"],
            "pass2": res2["data"],
            "agreement": agreement,
        },
    }


def _normalize_single_receipt(rec: dict) -> dict:
    """1件のレシート dict を後処理 (型変換 / 値域正規化)。"""
    out = dict(rec)

    # amount: int に統一
    a = out.get("amount")
    if isinstance(a, str):
        s = a.replace(",", "").replace("¥", "").replace("円", "").strip()
        try:
            out["amount"] = int(float(s))
        except ValueError:
            out["amount"] = None
    elif isinstance(a, float):
        out["amount"] = int(a)

    # items: list[dict] であることを保証
    items = out.get("items") or []
    cleaned_items: list[dict] = []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            price_v = it.get("price")
            price = 0
            if isinstance(price_v, (int, float)):
                price = int(price_v)
            elif isinstance(price_v, str):
                ps = price_v.replace(",", "").replace("¥", "").replace("円", "").strip()
                try:
                    price = int(float(ps))
                except ValueError:
                    price = 0
            if name:
                cleaned_items.append({"name": name, "price": price})
    out["items"] = cleaned_items

    # 勘定科目 / 税区分は既知リストにマッチしなければそのまま (ユーザ編集前提)
    return out


def _post_process(data: dict) -> dict:
    """全体データを後処理。新しいschemaでは receipts: [...] が主要フィールド。

    互換性:
      - 入力に receipts 配列があればそれを正規化
      - 入力が旧 schema (date/vendor/amount をトップレベル) ならラップして receipts: [data]
      - 出力は常に {receipts: [normalized,...], total_receipts: N, ...}
    """
    out = dict(data)

    # receipts 配列の取得 / 互換ラッピング
    raw_receipts = out.get("receipts")
    if not isinstance(raw_receipts, list):
        # 旧 schema をラップ: トップレベルの単一レシート → receipts[0]
        single = {
            k: out.get(k) for k in (
                "date", "vendor", "amount", "items",
                "purpose", "suggested_account", "suggested_tax_class",
                "confidence", "notes",
            ) if k in out
        }
        raw_receipts = [single] if single else []

    receipts: list[dict] = []
    for r in raw_receipts:
        if isinstance(r, dict):
            receipts.append(_normalize_single_receipt(r))
    out["receipts"] = receipts
    out["total_receipts"] = len(receipts)

    # 後方互換: 1件目の主要フィールドをトップレベルにもミラー
    if receipts:
        first = receipts[0]
        for k in (
            "date", "vendor", "amount", "items", "purpose",
            "suggested_account", "suggested_tax_class",
            "confidence", "notes",
        ):
            out[k] = first.get(k)
    return out


def items_to_text(items: list[dict]) -> str:
    """items リスト → 摘要表記 (例: 'ポテコ¥1,746, ｺﾞﾐ袋¥330') 。"""
    parts = []
    for it in items:
        name = (it.get("name") or "").strip()
        price = it.get("price") or 0
        if not name:
            continue
        if price:
            parts.append(f"{name}¥{int(price):,}")
        else:
            parts.append(name)
    return ", ".join(parts)
