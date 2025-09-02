import csv, io, requests
from typing import List, Dict, Optional
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 資料來源定義 ---
TWSE_JSON_L = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
MOPS_CSV_L = "https://mopsfin.twse.com.tw/opendata/t187ap03_L.csv"
MOPS_CSV_O = "https://mopsfin.twse.com.tw/opendata/t187ap03_O.csv"
MOPS_CSV_R = "https://mopsfin.twse.com.tw/opendata/t187ap03_R.csv"

# --- 資料解析 ---
def _parse_csv_bytes(b: bytes) -> List[Dict[str, str]]:
    text = b.decode("utf-8-sig", errors="ignore")
    sio = io.StringIO(text)
    rdr = csv.DictReader(sio)
    out = []
    for r in rdr:
        code = (r.get("公司代號") or "").strip().replace("/", "")
        name = (r.get("公司名稱") or "").strip()
        short = (r.get("公司簡稱") or "").strip()
        if code.isdigit() and 4 <= len(code) <= 5 and name:
            out.append({"code": code, "name": name, "short": short})
    return out

def _clean_name(s: str) -> str:
    return s.rstrip("股份有限公司有限公司").strip() if s else s

# --- 資料抓取 ---
def _fetch_all_sources() -> List[Dict[str, str]]:
    items = []
    for url in [TWSE_JSON_L, MOPS_CSV_L, MOPS_CSV_O, MOPS_CSV_R]:
        try:
            resp = requests.get(url, timeout=10, verify=False)
            resp.raise_for_status()

            if url.endswith(".json"):
                data = resp.json()
                if isinstance(data, list):
                    for x in data:
                        code = (x.get("公司代號") or "").strip()
                        name = (x.get("公司名稱") or "").strip()
                        short = (x.get("公司簡稱") or "").strip()
                        if code and name:
                            items.append({"code": code, "name": name, "short": short})
            else:
                items.extend(_parse_csv_bytes(resp.content))

        except Exception as e:
            print(f"[資料抓取失敗] {url}：{e}")
            continue

    dedup = {x["code"]: x for x in items if x.get("code")}
    return sorted(dedup.values(), key=lambda x: x["code"])

# --- 主類別 ---
class TwStock:
    def __init__(self):
        self.all_items = []
        self.code2item = {}
        self.name2code = {}
        self.source = None
        self.refresh(force=True)

    def _apply_items(self, items: List[Dict[str, str]]):
        self.all_items = items
        self.code2item = {x["code"]: x for x in items}
        self.name2code = {x["name"]: x["code"] for x in items}

    def refresh(self, force: bool = False):
        items = _fetch_all_sources()
        self._apply_items(items)
        self.source = "network"

    def get_name(self, code: str, clean: bool = True, prefer_short: bool = True) -> Optional[str]:
        it = self.code2item.get(code)
        if not it:
            return self.convert_numb_to_stock_name(code)
        return it.get("short") if prefer_short and it.get("short") else _clean_name(it["name"]) if clean else it["name"]

    def get_code(self, name: str) -> Optional[str]:
        return self.name2code.get(name)

    def search_by_name(self, substr: str, clean: bool = True) -> List[Dict[str, str]]:
        q = substr.strip()
        return [
            {
                "code": it["code"],
                "name": it["name"],
                "short": it["short"],
                "clean_name": _clean_name(it["name"]) if clean else it["name"]
            }
            for it in self.all_items
            if q and (q in it["name"] or q in it.get("short", ""))
        ]

    def convert_numb_to_stock_name(self, stock_code: str) -> Optional[str]:
        """查即時股價 API 取得代號對應名稱（含 ETF）"""
        url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{stock_code}.tw"
        try:
            res = requests.get(url, timeout=5,verify=False)
            data = res.json()
            if "msgArray" in data and data["msgArray"]:
                return data["msgArray"][0]["n"]
        except Exception as e:
            print(f"[查詢失敗] {stock_code}：{e}")
        return None
