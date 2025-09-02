"""抓取臺灣證券商業同業公會之「詢圈公告」資料。"""
from __future__ import annotations
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
import requests

URL = "https://web.twsa.org.tw/edoc2/default.aspx"
HEADERS = {
    "User-Agent": "mops-bot/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}

def _val(soup: BeautifulSoup, name: str) -> Optional[str]:
    tag = soup.find("input", {"name": name})
    return tag.get("value") if tag else None

def fetch_bookbuilding(year: str = "114") -> List[Dict]:
    try:
        resp1 = requests.get(URL, headers=HEADERS, verify=False)
        resp1.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"GET warmup failed: {e}")

    soup1 = BeautifulSoup(resp1.text, "html.parser")

    vs  = _val(soup1, "__VIEWSTATE")
    vsg = _val(soup1, "__VIEWSTATEGENERATOR")
    ev  = _val(soup1, "__EVENTVALIDATION")
    if not (vs and vsg and ev):
        return []

    form = {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": vs,
        "__VIEWSTATEGENERATOR": vsg,
        "__EVENTVALIDATION": ev,
        "ctl00$cphMain$txtYear": str(year),
        "ctl00$cphMain$rblReportType": "BookBuilding",
        "ctl00$cphMain$btnQuery": "查詢",
    }

    try:
        resp2 = requests.post(
            URL,
            data=form,
            headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            verify=False
        )
        resp2.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"POST query failed: {e}")

    soup2 = BeautifulSoup(resp2.text, "html.parser")
    table = soup2.find("table", {"id": "ctl00_cphMain_gvResult"})
    if not table:
        return []

    rows: List[Dict] = []
    trs = table.find_all("tr")
    for tr in trs[1:]:
        tds = [td.get_text(strip=True) for td in tr.find_all("td")]
        if len(tds) < 8:
            continue
        rows.append({
            "序號": tds[0],
            "發行公司": tds[1],
            "主辦承銷商": tds[2],
            "發行性質": tds[3],
            "承銷股數": tds[4],
            "詢圈銷售股數": tds[5],
            "圈購期間": tds[6],
            "價格": tds[7],
        })

    return rows
