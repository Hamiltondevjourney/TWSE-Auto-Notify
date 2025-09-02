""""mops_service.py
封裝對外服務介面，整合今日重大訊息與歷史重大訊息（ezsearch_query）。
"""

from tw_scrapers.mops_today_news import fetch_today_major_announcements
from tw_scrapers.mops_historical_news import fetch_ezsearch


def get_today_major_announcements(keyword: str = "") -> list[dict]:
    """取得「今日」重大訊息（僅當日有資料；歷史請改用 ezsearch）。

    Args:
        keyword (str, optional): 主旨或公司名稱需包含此關鍵字才保留（空字串=不過濾）。

    Returns:
        list[dict]: 每筆含 {co_id, name, date_pub, date_say, subject}
    """
    return fetch_today_major_announcements(keyword)


def get_historical_announcements(
    sdate: str,
    edate: str,
    subject: str = "",
    typek: str = "sii",
    co_id: str = "",
    pro_item: str = "",
    *,
    mode: str = "full",
) -> list[dict]:
    """
    取得歷史重大訊息，回傳統一欄位格式：
    {co_id, name, date_pub, date_say, subject, url}

    參數：
    - sdate (str): 起始日期（格式 yyyy/mm/dd）
    - edate (str): 結束日期（格式 yyyy/mm/dd）
    - subject (str): 主旨關鍵字（可選）
    - typek (str): 公司類型，預設 "sii"（上市），也可用 "otc"（上櫃）、"rotc"（興櫃）
    - co_id (str): 公司代號（可選）
    - pro_item (str): 公告項目類別（可選）
    - mode (str): 模式選項，預設 "full"

    回傳：
    - list[dict]：包含標準化欄位的歷史公告資料
    """
    raw_rows = fetch_ezsearch(
        sdate=sdate,
        edate=edate,
        subject=subject,
        typek=typek,
        co_id=co_id,
        pro_item=pro_item,
        mode=mode,
    )

    normalized = []
    for r in raw_rows:
        normalized.append({
            "co_id": r.get("代號"),
            "name": r.get("簡稱"),
            "date_pub": r.get("日期"),
            "date_say": r.get("時間"),
            "subject": r.get("主旨"),
            "url": r.get("連結"),
        })
    return normalized



if __name__ == "__main__":
    import time

    # 快速模式（最多約 1000 筆）
    t0 = time.time()
    fast_rows = get_historical_announcements("111/05/30", "112/08/30", subject="股息", mode="fast")
    print(f"[FAST] 筆數={len(fast_rows)}，耗時={time.time()-t0:.2f} 秒；示例：", fast_rows[:2])

    # 完整模式（自動切分，可超過 1000 筆）
    t1 = time.time()
    full_rows = get_historical_announcements("111/05/30", "112/08/30", subject="股息", mode="full")
    print(f"[FULL] 筆數={len(full_rows)}，耗時={time.time()-t1:.2f} 秒；示例：", full_rows[:2])

    print(f"兩者筆數相同？{len(fast_rows) == len(full_rows)}")
