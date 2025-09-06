"""抓取「公開資訊觀測站」歷史重大訊息（ezsearch_query API）"""
from __future__ import annotations
import json
from datetime import datetime, timedelta
from typing import List, Dict
import requests

URL = "https://mopsov.twse.com.tw/mops/web/ezsearch_query"
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://mopsov.twse.com.tw",
    "Referer": "https://mopsov.twse.com.tw/mops/web/ezsearch",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

def _normalize_typek(typek: str) -> str:
    """將 typek 參數標準化（上市:sii、上櫃:otc、興櫃:rotc、公開發行:pub）"""
    typek = (typek or "").lower()
    if typek in ("sii", "上市"): return "sii"
    if typek in ("otc", "上櫃"): return "otc"
    if typek in ("rotc", "興櫃"): return "rotc"
    if typek in ("pub", "公開發行"): return "pub"
    return "sii"  # 預設上市

def _roc_to_date(roc: str) -> datetime:
    y, m, d = roc.split("/")
    return datetime(int(y) + 1911, int(m), int(d))

def _date_to_roc(dt: datetime) -> str:
    return f"{dt.year - 1911:03d}/{dt.month:02d}/{dt.day:02d}"

def _normalize(data: List[Dict]) -> List[Dict]:
    rows = []
    for row in data:
        rows.append({
            "日期": row.get("CDATE"),
            "時間": row.get("CTIME"),
            "市場": row.get("TYPEK"),
            "產業": row.get("CODE_NAME"),
            "代號": row.get("CO_ID") or row.get("STOCK_ID") or row.get("COMPANY_ID"),
            "簡稱": row.get("COMPANY_NAME"),
            "項目代碼": row.get("AN_CODE"),
            "項目": row.get("AN_NAME"),
            "主旨": row.get("SUBJECT"),
            "說明": row.get("DESCRIPTION") or "",  # 新增說明欄位
            "連結": row.get("HYPERLINK"),
        })
    return rows

def _post_once(sdate: str, edate: str, *, subject: str, typek: str, co_id: str, pro_item: str) -> List[Dict]:
    form = {
        "step": "00",
        "RADIO_CM": "1",
        "TYPEK": _normalize_typek(typek),
        "CO_MARKET": "",
        "CO_ID": co_id,
        "PRO_ITEM": pro_item,
        "SUBJECT": subject,
        "SDATE": sdate,
        "EDATE": edate,
        "lang": "TW",
        "AN": "",
    }

    # 暖機（可有可無）
    requests.get("https://mopsov.twse.com.tw/mops/web/ezsearch", verify=False)

    try:
        resp = requests.post(URL, data=form, headers=HEADERS, verify=False)
        resp.raise_for_status()
        text = resp.text
    except Exception as e:
        raise RuntimeError(f"ezsearch_query HTTP error: {e}")

    cleaned = text.lstrip("\ufeff \n\r\t")
    i = cleaned.find("{")
    if i < 0: return []
    payload = json.loads(cleaned[i:])
    return payload.get("data", []) or []

def fetch_ezsearch(
    sdate: str,
    edate: str,
    subject: str = "",
    typek: str = "sii",
    co_id: str = "",
    pro_item: str = "",
    *, mode: str = "full",
) -> List[Dict]:
    if mode not in ("fast", "full"):
        raise ValueError("mode 必須是 'fast' 或 'full'")
    start_dt, end_dt = _roc_to_date(sdate), _roc_to_date(edate)
    if end_dt < start_dt:
        raise ValueError("edate 需晚於或等於 sdate")

    if mode == "fast":
        data = _post_once(_date_to_roc(start_dt), _date_to_roc(end_dt),
                          subject=subject, typek=typek, co_id=co_id, pro_item=pro_item)
        rows = _normalize(data)
    else:
        def _fetch_chunk(a: datetime, b: datetime) -> List[Dict]:
            raw = _post_once(_date_to_roc(a), _date_to_roc(b),
                             subject=subject, typek=typek, co_id=co_id, pro_item=pro_item)
            if len(raw) >= 1000 and (b - a).days >= 1:
                mid = a + (b - a) // 2
                return _fetch_chunk(a, mid) + _fetch_chunk(mid + timedelta(days=1), b)
            return _normalize(raw)

        rows_all: List[Dict] = []
        cur = start_dt
        while cur <= end_dt:
            to = min(cur + timedelta(days=30), end_dt)
            rows_all.extend(_fetch_chunk(cur, to))
            cur = to + timedelta(days=1)

        seen, rows = set(), []
        for r in rows_all:
            key = (r.get("日期"), r.get("時間"), r.get("主旨"), r.get("連結"))
            if key in seen: continue
            seen.add(key); rows.append(r)

    rows.sort(key=lambda r: ((r.get("日期") or ""), (r.get("時間") or "")))
    # 新增：查詢後再做一次關鍵字過濾
    if subject:
        keyword = subject
        rows = [
            r for r in rows
            if keyword in (r.get("主旨") or "")
            or keyword in (r.get("說明") or "")
            or keyword in (r.get("項目") or "")
            or keyword in (r.get("簡稱") or "")
        ]
    return rows