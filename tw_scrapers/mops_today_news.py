"""抓取「公開資訊觀測站」當日重大訊息（上市/上櫃），支援關鍵字過濾。"""

import re
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

API = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"

def _http_json(url: str) -> List[Dict[str, Any]]:
    """使用 requests 抓取 JSON 資料，失敗時拋出例外。"""
    try:
        resp = requests.get(url, timeout=15, verify=False)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        raise RuntimeError(f"HTTP JSON fetch failed: {e}")

def _today_tokens_tpe() -> set[str]:
    """產生當天可能出現的日期格式 Token，用於過濾資料日期。"""
    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz)
    y, m, d = now.year, now.month, now.day
    roc_slash = f"{y-1911:03d}/{m:02d}/{d:02d}"
    roc_comp  = f"{y-1911:03d}{m:02d}{d:02d}"
    iso_slash = f"{y:04d}/{m:02d}/{d:02d}"
    iso_comp  = f"{y:04d}{m:02d}{d:02d}"
    tokens = {roc_slash, roc_comp, iso_slash, iso_comp}
    tokens |= {re.sub(r"\D", "", t) for t in tokens}
    return tokens

def _compact(s: str | None) -> str | None:
    """將日期字串中的非數字移除。"""
    return re.sub(r"\D", "", s.strip()) if s else None

def fetch_today_major_announcements(keyword: str = "") -> list[dict]:
    """
    抓取今日的重大訊息公告，若提供關鍵字則進行篩選。
    
    :param keyword: 關鍵字（可為公司名稱或主旨的一部分）
    :return: 篩選後的公告資料列表
    """
    today_tokens = _today_tokens_tpe()
    data = _http_json(API)

    out: list[dict] = []
    for row in data:
        code = row.get("公司代號") or row.get("co_id") or row.get("Code")
        name = row.get("公司名稱") or row.get("name") or row.get("Name")
        subject = (row.get("主旨") if "主旨" in row else row.get("主旨 ") or
                   row.get("subject") or row.get("標題"))
        if not (code and name and subject):
            continue

        date_pub = row.get("出表日期") or row.get("公告日期") or row.get("date")
        date_say = row.get("發言日期") or row.get("日期")
        date_any = row.get("事實發生日") or row.get("發生日")
        dates = [date_pub, date_say, date_any]

        if not any(
            (d and d.strip() in today_tokens) or (_compact(d) in today_tokens)
            for d in dates
        ):
            continue

        if keyword and (keyword not in subject and keyword not in name):
            continue

        out.append({
            "co_id": str(code).strip(),
            "name": str(name).strip(),
            "date_pub": date_pub,
            "date_say": date_say,
            "subject": str(subject).strip(),
        })
    return out
