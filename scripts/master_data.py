# -*- coding: utf-8 -*-
"""施設マスター・店舗マスターの読み込みと名前→コード変換。"""
from __future__ import annotations
import difflib
import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _katakana_to_hiragana(s: str) -> str:
    """カタカナ→ひらがなに変換(手書き表記ゆれ吸収用)。"""
    out = []
    for ch in s:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _normalize(s: str) -> str:
    """全角半角・記号・空白を吸収して比較用に正規化。カナはひらがなに統一。"""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = s.replace("　", "").replace(" ", "").replace("・", "").replace(".", "").replace("．", "")
    s = re.sub(r"[（(].*?[)）]", "", s)
    s = _katakana_to_hiragana(s)
    return s.lower()


class FacilityMaster:
    """法人ごとの施設マスター。手書き名→正式コードを引く。"""

    def __init__(self, path: Path = CONFIG_DIR / "facilities.json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.corps: dict[str, dict] = {
            k: v for k, v in data.items()
            if not k.startswith("_") and k != "folder_overrides"
        }
        self.folder_overrides: dict = data.get("folder_overrides", {})
        # 逆引きインデックス: 正規化エイリアス → (法人キー, code)
        self._index: dict[str, tuple[str, str]] = {}
        for corp_key, corp in self.corps.items():
            for fac in corp["facilities"]:
                code = fac["code"]
                names = [code] + fac.get("aliases", [])
                for name in names:
                    self._index[_normalize(name)] = (corp_key, code)
                # コード末尾の施設名部分（数字以外）も登録
                tail = re.sub(r"^[\d.\s　]+", "", code)
                if tail:
                    self._index.setdefault(_normalize(tail), (corp_key, code))

    def lookup(self, handwritten: str, prefer_corp: Optional[str] = None) -> Optional[tuple[str, str]]:
        """手書き施設名から (法人キー, 正式コード) を返す。見つからなければ None。"""
        if not handwritten:
            return None
        key = _normalize(handwritten)
        # 完全一致
        if key in self._index:
            corp, code = self._index[key]
            if prefer_corp is None or prefer_corp == corp:
                return corp, code
        # 部分一致（手書きが短いとき / 余計な文字が混じったとき）
        candidates = []
        for k, (corp, code) in self._index.items():
            if not k:
                continue
            if k in key or key in k:
                if prefer_corp is None or prefer_corp == corp:
                    candidates.append((len(k), corp, code))
        if candidates:
            # 一致長が最大のものを採用
            candidates.sort(reverse=True)
            return candidates[0][1], candidates[0][2]
        # ファジー一致(編集距離ベース): 手書き誤読・表記揺れを吸収
        keys = [k for k in self._index.keys() if k]
        # 法人ヒントがある場合は同法人のキーに絞る(誤マッチ防止)
        if prefer_corp:
            keys = [k for k in keys if self._index[k][0] == prefer_corp]
        # cutoffを緩めて寛容に。短い手書きでも拾えるようにする
        close = difflib.get_close_matches(key, keys, n=3, cutoff=0.6)
        if close:
            corp, code = self._index[close[0]]
            return corp, code
        return None

    def excel_filename(self, corp_key: str) -> str:
        return self.corps[corp_key]["_excel_filename"]

    def corp_name(self, corp_key: str) -> str:
        return self.corps[corp_key]["_corp_name"]

    def resolve_folder_override(self, folder_name: str) -> Optional[dict]:
        """Dropboxフォルダ名 → {to_facility_code, summary_suffix} を返す。
        該当ルールがあれば、別施設として記録 + 摘要に追記する指示。"""
        return self.folder_overrides.get(folder_name)

    def corp_of_code(self, code: str) -> Optional[str]:
        """施設コードから法人キーを逆引き。"""
        for corp_key, corp_data in self.corps.items():
            for fac in corp_data["facilities"]:
                if fac["code"] == code:
                    return corp_key
        return None


class AccountMaster:
    """店舗名→勘定科目・税区分・摘要テンプレートのマッピング。"""

    def __init__(self, path: Path = CONFIG_DIR / "account_mapping.json"):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.rules: list[dict] = data["rules"]
        self.default: dict = data["default"]

    def classify(self, store: str, hint_account: Optional[str] = None) -> dict:
        """店舗名から {account, tax, summary, matched, rule_index} を返す。
        hint_account がある場合、ルールに一致しても hint と矛盾しなければ採用。
        """
        if not store:
            return self._build_default(store, matched=False)
        norm_store = _normalize(store)
        for i, rule in enumerate(self.rules):
            for kw in rule["match"]:
                if _normalize(kw) in norm_store:
                    summary = rule["summary_template"].replace("{store}", store)
                    return {
                        "account": rule["account"],
                        "tax": rule["tax"],
                        "summary": summary,
                        "matched": True,
                        "rule_index": i,
                    }
        return self._build_default(store, matched=False)

    def _build_default(self, store: str, matched: bool) -> dict:
        return {
            "account": self.default["account"],
            "tax": self.default["tax"],
            "summary": self.default["summary_template"].replace("{store}", store or ""),
            "matched": matched,
            "rule_index": -1,
        }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    fm = FacilityMaster()
    am = AccountMaster()
    test_facilities = ["UMIEてんり", "SORATOてんり", "カラダキッズてんり", "のじぎく高砂", "うみえてんり"]
    for t in test_facilities:
        print(f"  {t!r} → {fm.lookup(t)}")
    test_stores = ["イオンビッグ株式会社", "ダイソー イオンタウン天理店", "株式会社吉田石油店", "大阪市大阪駅前地下駐車場"]
    for s in test_stores:
        r = am.classify(s)
        print(f"  {s!r} → {r['account']} / {r['tax']} / {r['summary']}")
