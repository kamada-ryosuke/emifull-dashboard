r"""Dropbox 採用者一覧/退職届 スキャナ

Dropbox の以下の階層を読み込んで、職員レコードを抽出する：

  Dropbox\障がい事業部\08.採用 雇用 退職\
    01.採用者一覧\
       YYMMDD【施設】氏名 追加メモ\         ← 1人=1フォルダ
          履歴書.pdf
          各種雇用契約書.docx, .xlsx ...
    02.退職届\
       YYYY年度\
       YYMMDD【退職届】氏名（施設）※理由.pdf
       260430【退職届】林口里穂（カレッジかこがわ）...

責務:
  1. 採用者フォルダ名 → {入社日, 施設, 氏名, 備考}
  2. 退職届ファイル名 → {退職日, 氏名, 施設, 理由}
  3. 履歴書PDF（最初の画像）→ アバター画像として保存
  4. 全部まとめて data/dropbox_staff/staff_index.json に書き出す

時間がかかるためスキャンはオフラインで（scripts/build_dropbox_staff_cache.py）。
画像抽出は PyMuPDF を使用。
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

# ============================================================
# パス
# ============================================================

DROPBOX_BASE = Path(
    r"C:\Users\user01\株式会社ＥＭＩＦＵＬＬ Dropbox\障がい事業部\08.採用 雇用 退職"
)
HIRE_DIR = DROPBOX_BASE / "01.採用者一覧"
LEAVE_DIR = DROPBOX_BASE / "02.退職届"

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "dropbox_staff"
PHOTO_DIR = DATA_DIR / "photos"
INDEX_PATH = DATA_DIR / "staff_index.json"


# ============================================================
# 命名規則パーサ
# ============================================================

# 採用者フォルダ: "260401【てんり】北野愛香 保育士"
HIRE_FOLDER_RE = re.compile(
    r"^(?P<ymd>\d{6})"
    r"\s*【(?P<facility>[^】]+)】"
    r"\s*(?P<rest>.+?)\s*$"
)

# 退職届ファイル: "250425【退職届】大石久理子（天理）※管理者へ暴言.pdf"
LEAVE_FILE_RE = re.compile(
    r"^(?P<ymd>\d{6})"
    r"\s*【(?:退職届|退職願|退職)】"
    r"\s*(?P<rest>.+?)"
    r"(?:\.pdf|\.docx?|\.xlsx?)?$"
)


def _yymmdd_to_date(yymmdd: str) -> Optional[date]:
    """YYMMDD (例: 260401) → date(2026, 4, 1)。年は YY<50→20YY, else 19YY。"""
    if len(yymmdd) != 6 or not yymmdd.isdigit():
        return None
    yy = int(yymmdd[:2])
    mm = int(yymmdd[2:4])
    dd = int(yymmdd[4:6])
    year = 2000 + yy if yy < 50 else 1900 + yy
    try:
        return date(year, mm, dd)
    except ValueError:
        return None


def _split_rest(rest: str) -> tuple[str, str]:
    """『北野愛香 保育士』『鎌田亮介 次長職』のような『氏名 + 残り』を分離。

    日本語姓名は通常2〜4文字。最初の空白（半角/全角）の前を氏名とみなす。
    空白がなければ全体を氏名扱い。
    """
    s = rest.strip()
    s_norm = unicodedata.normalize("NFKC", s)
    # 全角/半角空白の最初で区切る
    m = re.search(r"[\s　]", s_norm)
    if m:
        idx = m.start()
        return s_norm[:idx].strip(), s_norm[idx:].strip()
    return s_norm, ""


def _normalize_name(name: str) -> str:
    """氏名の比較用正規化：NFKC + 空白除去 + 一般的な敬称除去。"""
    if not name:
        return ""
    s = unicodedata.normalize("NFKC", name)
    s = re.sub(r"\s+", "", s)
    # 末尾の様/さん/くん/ちゃん を除去
    s = re.sub(r"(様|さん|くん|ちゃん)$", "", s)
    return s


# ============================================================
# データ構造
# ============================================================

@dataclass
class DropboxStaff:
    name: str                           # 氏名（生）
    name_normalized: str                # 比較用
    facility: str = ""                  # 施設（生）
    hire_date: Optional[str] = None     # YYYY-MM-DD
    leave_date: Optional[str] = None    # YYYY-MM-DD
    folder_name: str = ""               # 採用者フォルダ名
    folder_path: str = ""               # 絶対パス
    note: str = ""                      # 採用フォルダの追加メモ部
    leave_note: str = ""                # 退職理由（※以降）
    leave_file: str = ""                # 退職届ファイル名
    photo_path: str = ""                # 抽出した顔写真の保存先（相対）
    resume_pdf: str = ""                # 履歴書PDFの絶対パス（リンク用）

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 採用者フォルダのスキャン
# ============================================================

def iter_hire_folders() -> Iterable[Path]:
    if not HIRE_DIR.exists():
        return []
    return sorted([p for p in HIRE_DIR.iterdir() if p.is_dir()])


def parse_hire_folder(folder: Path) -> Optional[DropboxStaff]:
    """1フォルダ → DropboxStaff（マッチしなければ None）"""
    name_part = folder.name
    m = HIRE_FOLDER_RE.match(name_part)
    if not m:
        return None
    hire = _yymmdd_to_date(m.group("ymd"))
    if hire is None:
        return None
    fac = m.group("facility").strip()
    nm, note = _split_rest(m.group("rest"))
    return DropboxStaff(
        name=nm,
        name_normalized=_normalize_name(nm),
        facility=fac,
        hire_date=hire.isoformat(),
        folder_name=name_part,
        folder_path=str(folder),
        note=note,
    )


# ============================================================
# 履歴書 PDF/画像 の特定 と 写真抽出
# ============================================================

# 履歴書である可能性が高いキーワード（優先順）
RESUME_KEYWORDS = ["履歴書", "リレキ", "Rireki", "rireki"]


def find_resume_file(folder: Path) -> Optional[Path]:
    """フォルダ内から「履歴書」を含むPDFを探す（ファイル名で判定）"""
    if not folder.exists():
        return None
    candidates = []
    for f in folder.iterdir():
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in (".pdf", ".png", ".jpg", ".jpeg"):
            continue
        for kw in RESUME_KEYWORDS:
            if kw in f.name:
                candidates.append((kw, f))
                break
    if candidates:
        # 最も短いファイル名（≒余計な接頭辞が少ない）を優先
        candidates.sort(key=lambda x: (RESUME_KEYWORDS.index(x[0]), len(x[1].name)))
        return candidates[0][1]
    # キーワードが無ければ、フォルダ内最大のPDFを返す（履歴書は写真込みで重い傾向）
    pdfs = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() == ".pdf"]
    if pdfs:
        pdfs.sort(key=lambda p: p.stat().st_size, reverse=True)
        return pdfs[0]
    return None


def extract_face_photo(
    pdf_path: Path,
    out_path: Path,
    max_pixels: int = 400,
) -> bool:
    """履歴書PDFの1ページ目から証明写真を抽出して保存。

    日本の履歴書は JIS 規格で右上、または左上に 4cm×3cm（縦:横≒4:3）の証明写真。
    PDFが純然たる画像スキャンの場合は1ページ全体が1画像なので、右上をクロップする。
    """
    try:
        import fitz  # PyMuPDF
        from PIL import Image
    except ImportError:
        return False
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return False
        page = doc[0]
        images = page.get_images(full=True)

        candidates = []  # (score, area, base_image)
        for img_info in images:
            xref = img_info[0]
            try:
                base_image = doc.extract_image(xref)
            except Exception:
                continue
            w = base_image.get("width", 0)
            h = base_image.get("height", 0)
            if w < 80 or h < 80:
                continue
            ratio = w / h if h else 0
            # 証明写真らしい縦横比 (3:4 = 0.75)
            if 0.68 <= ratio <= 0.85:
                score = 100
            elif 0.85 <= ratio <= 1.05:
                score = 70  # 正方形気味
            elif 0.65 <= ratio <= 0.78:
                score = 30  # A4ページスキャンの可能性大
            else:
                score = 20
            candidates.append((score, w * h, base_image))

        chosen = None
        if candidates:
            candidates.sort(key=lambda x: (-x[0], -x[1]))
            chosen = candidates[0][2]

        # 候補の判定：本物の証明写真サイズか、それとも A4 全体スキャンか
        if chosen and chosen.get("width", 0) > 1200 and chosen.get("height", 0) > 1500:
            # 大きすぎる → A4 ページ画像と推定 → クロップ
            ok = _crop_face_from_page_image(chosen, out_path, max_pixels)
            doc.close()
            return ok

        if chosen:
            raw = chosen["image"]
            ext = chosen.get("ext", "png")
            tmp_path = out_path.with_suffix(f".raw.{ext}")
            tmp_path.write_bytes(raw)
            try:
                img = Image.open(tmp_path)
                img.thumbnail((max_pixels, max_pixels))
                if img.mode in ("RGBA", "P", "L"):
                    img = img.convert("RGB")
                img.save(out_path, "JPEG", quality=85)
                tmp_path.unlink(missing_ok=True)
                doc.close()
                return out_path.exists()
            except Exception:
                tmp_path.unlink(missing_ok=True)

        # 埋め込み画像なし or 失敗 → ページレンダリングして右上クロップ
        doc.close()
        return _render_and_crop_face(pdf_path, out_path, max_pixels)
    except Exception:
        return False


def _crop_face_from_page_image(base_image: dict, out_path: Path, max_pixels: int) -> bool:
    """履歴書の1ページ画像から証明写真領域（右上 / 左上）をクロップ。"""
    try:
        from PIL import Image
        from io import BytesIO
        raw = base_image["image"]
        img = Image.open(BytesIO(raw))
        if img.mode in ("RGBA", "P", "L"):
            img = img.convert("RGB")
        W, H = img.size
        # JIS 履歴書: 右上 (or 左上) に証明写真
        # 4cm × 3cm の領域 ≒ ページ全体の縦×横比に対し 0.16 × 0.21 程度
        # 余裕を持って 上 0% - 25%, 横は端から 30% を切る
        # まず右上を試す
        candidates = [
            ("upperRight", img.crop((int(W * 0.66), int(H * 0.02), int(W * 0.96), int(H * 0.24)))),
            ("upperLeft",  img.crop((int(W * 0.04), int(H * 0.02), int(W * 0.34), int(H * 0.24)))),
        ]
        # 暗すぎ/明るすぎ（白紙）でない方を採用：標準偏差が大きい方
        chosen = None
        best_score = -1
        for label, crop in candidates:
            stat = crop.convert("L")
            pixels = list(stat.getdata())
            mean = sum(pixels) / len(pixels)
            # 白紙近似（mean > 240）は除外
            if mean > 240:
                continue
            # 分散
            var = sum((p - mean) ** 2 for p in pixels) / len(pixels)
            if var > best_score:
                best_score = var
                chosen = crop
        if chosen is None:
            chosen = candidates[0][1]  # 仕方なく右上
        chosen.thumbnail((max_pixels, max_pixels))
        chosen.save(out_path, "JPEG", quality=85)
        return out_path.exists()
    except Exception:
        return False


def _render_and_crop_face(pdf_path: Path, out_path: Path, max_pixels: int) -> bool:
    """ベクターPDFや画像取得失敗時：ページをレンダして右上をクロップ。"""
    try:
        import fitz
        from PIL import Image
        doc = fitz.open(pdf_path)
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        tmp_path = out_path.with_suffix(".tmp.png")
        pix.save(str(tmp_path))
        img = Image.open(tmp_path)
        if img.mode in ("RGBA", "P", "L"):
            img = img.convert("RGB")
        # ダミー base_image dict を渡す形で再利用
        from io import BytesIO
        buf = BytesIO()
        img.save(buf, "PNG")
        ok = _crop_face_from_page_image({"image": buf.getvalue()}, out_path, max_pixels)
        tmp_path.unlink(missing_ok=True)
        doc.close()
        return ok
    except Exception:
        return False


# ============================================================
# 退職届のスキャン
# ============================================================

def iter_leave_files() -> Iterable[Path]:
    """02.退職届/ 直下と各「YYYY年度」フォルダ配下の PDF を列挙。"""
    if not LEAVE_DIR.exists():
        return []
    out = []
    for p in LEAVE_DIR.iterdir():
        if p.is_file() and p.suffix.lower() == ".pdf":
            out.append(p)
        elif p.is_dir():
            for f in p.iterdir():
                if f.is_file() and f.suffix.lower() == ".pdf":
                    out.append(f)
    return sorted(out)


def parse_leave_file(path: Path) -> Optional[dict]:
    """退職届ファイル名 → {leave_date, name, facility, leave_note, file}"""
    stem = path.stem  # 拡張子なし
    m = LEAVE_FILE_RE.match(stem + ".pdf")
    if not m:
        # 「YYMMDD 【退職届】...」のスペース有無パターンにも対応
        m = re.match(
            r"^(?P<ymd>\d{6})\s*【(?:退職届|退職願|退職)】(?P<rest>.+)$",
            stem,
        )
        if not m:
            return None
    leave = _yymmdd_to_date(m.group("ymd"))
    if leave is None:
        return None
    rest = m.group("rest").strip()

    # 例: "大石久理子（天理）※管理者へ暴言"
    fac = ""
    note = ""
    # 全角/半角の括弧両対応
    fac_re = re.search(r"[（\(]([^）\)]+)[）\)]", rest)
    if fac_re:
        fac = fac_re.group(1).strip()
        rest_no_fac = rest[: fac_re.start()] + rest[fac_re.end():]
    else:
        rest_no_fac = rest

    # ※ または ≒ の後ろを理由とする
    note_re = re.search(r"[※\*](.+)$", rest_no_fac)
    if note_re:
        note = note_re.group(1).strip()
        name_part = rest_no_fac[: note_re.start()].strip()
    else:
        name_part = rest_no_fac.strip()

    nm = _normalize_name(name_part)
    if not nm:
        return None
    return {
        "leave_date": leave.isoformat(),
        "name": name_part.strip(),
        "name_normalized": nm,
        "facility": fac,
        "leave_note": note,
        "leave_file": path.name,
        "leave_path": str(path),
    }


# ============================================================
# 統合スキャン
# ============================================================

def scan_all(extract_photos: bool = True, verbose: bool = True) -> list[DropboxStaff]:
    """全フォルダ＋退職届をスキャンして DropboxStaff のリストを返す。"""
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 採用フォルダ
    staffs: dict[str, DropboxStaff] = {}  # key = name_normalized + hire_date
    folders = list(iter_hire_folders())
    if verbose:
        print(f"[scan] 採用者フォルダ: {len(folders)} 件")
    for i, f in enumerate(folders, 1):
        s = parse_hire_folder(f)
        if not s:
            if verbose:
                print(f"  ⚠️ パース不能: {f.name}")
            continue
        # 履歴書探索＋写真抽出
        resume = find_resume_file(f)
        if resume:
            s.resume_pdf = str(resume)
            if extract_photos and resume.suffix.lower() == ".pdf":
                photo_out = PHOTO_DIR / f"{s.name_normalized}_{s.hire_date}.jpg"
                if not photo_out.exists():
                    ok = extract_face_photo(resume, photo_out)
                    if ok and photo_out.exists():
                        s.photo_path = str(
                            photo_out.relative_to(DATA_DIR.parent.parent)
                        ).replace("\\", "/")
                else:
                    s.photo_path = str(
                        photo_out.relative_to(DATA_DIR.parent.parent)
                    ).replace("\\", "/")
        key = f"{s.name_normalized}|{s.hire_date}"
        staffs[key] = s
        if verbose and i % 25 == 0:
            print(f"  [{i}/{len(folders)}] {s.name} ({s.facility})")

    # 2) 退職届
    leave_files = list(iter_leave_files())
    if verbose:
        print(f"[scan] 退職届ファイル: {len(leave_files)} 件")
    leaves = []
    for f in leave_files:
        d = parse_leave_file(f)
        if d:
            leaves.append(d)
        elif verbose:
            print(f"  ⚠️ 退職届パース不能: {f.name}")

    # 3) 退職情報を採用レコードに反映（氏名で突合、入社日が古いものに付ける）
    for d in leaves:
        cands = [s for s in staffs.values() if s.name_normalized == d["name_normalized"]]
        if not cands:
            # 採用フォルダなしの退職者：仮レコード追加
            ph = DropboxStaff(
                name=d["name"],
                name_normalized=d["name_normalized"],
                facility=d["facility"],
                hire_date=None,
                leave_date=d["leave_date"],
                folder_name="",
                folder_path="",
                leave_note=d["leave_note"],
                leave_file=d["leave_file"],
            )
            staffs[f"_leaveonly_{d['name_normalized']}_{d['leave_date']}"] = ph
            continue
        # 入社日が一番古い人（複数該当時）に退職を紐付け
        cands.sort(key=lambda s: s.hire_date or "9999")
        target = cands[0]
        # 既に退職日がセットされていたら、より古い方を残す（複数退職届ある場合は新しい方）
        if not target.leave_date or d["leave_date"] > target.leave_date:
            target.leave_date = d["leave_date"]
            target.leave_note = d["leave_note"]
            target.leave_file = d["leave_file"]

    return list(staffs.values())


def save_index(staffs: list[DropboxStaff]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps([s.to_dict() for s in staffs], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return INDEX_PATH


def load_index() -> list[dict]:
    if not INDEX_PATH.exists():
        return []
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


# ============================================================
# 月単位 在職者フィルタ
# ============================================================

def is_active_in_month(rec: dict, target_ym: str) -> bool:
    """rec の入社日/退職日と target_ym (YYYY-MM) を比較。

    在職判定: 入社日 ≤ 月末 AND (退職日 IS NULL OR 退職日 ≥ 月初)
    入社日不明の場合は 退職日のみで判定（退職が当該月以降ならその月は在職）。
    """
    try:
        y, m = int(target_ym[:4]), int(target_ym[5:7])
        from calendar import monthrange
        first = date(y, m, 1)
        last = date(y, m, monthrange(y, m)[1])
    except Exception:
        return True

    hire_s = rec.get("hire_date") or rec.get("入社日")
    leave_s = rec.get("leave_date") or rec.get("退職日")

    hire_d = None
    leave_d = None
    if hire_s:
        try:
            hire_d = datetime.strptime(hire_s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    if leave_s:
        try:
            leave_d = datetime.strptime(leave_s[:10], "%Y-%m-%d").date()
        except ValueError:
            pass

    if hire_d and hire_d > last:
        return False
    if leave_d and leave_d < first:
        return False
    return True
