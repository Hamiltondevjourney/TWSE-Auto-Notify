# bookbuilding_service.py
"""
Service 介面：封裝存取「詢圈公告」資料的 API。

資料來源：
    - 臺灣證券商業同業公會「詢圈公告」
    - 網址：https://web.twsa.org.tw/edoc2/default.aspx
    - 由 tw_scrapers.bookbuilding.fetch_bookbuilding() 實作

功能：
    - 對外提供簡單函式介面
    - 使用者只需傳入民國年份，即可取得該年度的詢圈公告
"""

import time
from tw_scrapers.bookbuilding import fetch_bookbuilding


def get_bookbuilding_announcements(year: str = "114") -> list[dict]:
    """取得指定年度的詢圈公告資料。

    Args:
        year (str, optional): 民國年 (例如 '114')，預設 '114'。

    Returns:
        list[dict]: 每筆公告包含：
            - 序號 (str): 公告流水號
            - 發行公司 (str): 發行人公司名稱
            - 主辦承銷商 (str): 主辦券商名稱
            - 發行性質 (str): 發行方式或性質（例：現增、可轉債）
            - 承銷股數 (str): 承銷總股數
            - 詢圈銷售股數 (str): 詢圈可供銷售的股數
            - 圈購期間 (str): 詢圈日期區間（民國年月日）
            - 價格 (str): 詢圈價格或區間
    """
    return fetch_bookbuilding(year)


if __name__ == "__main__":
    # 簡單測試：附上計時
    start = time.time()
    rows = get_bookbuilding_announcements("114")
    end = time.time()

    print("共", len(rows), "筆，耗時", f"{end-start:.2f} 秒")
    print("前五筆：")
    for r in rows[:5]:
        print(f"{r['序號']} {r['發行公司']} | {r['圈購期間']} | {r['價格']}")
