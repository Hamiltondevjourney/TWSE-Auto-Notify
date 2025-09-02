"""tw_stock_service.py
封裝對外服務介面，提供股票代號與名稱的查詢功能。"""

from tw_scrapers.twstocks import TwStock

def get_stocks_list() -> list[dict]:
    """Return the full list of all stocks (上市+上櫃).

    Each item contains:
        - code (str): 股票代號
        - name (str): 公司名稱
        - short (str): 公司簡稱 (可能為空)

    Returns:
        list[dict]: Stock list with code, name, short.
    """
    stock_util = TwStock()
    return stock_util.all_items


def get_stock_name_by_code(code: str, clean: bool = True) -> str | None:
    """Get the company name by stock code.

    Args:
        code (str): 股票代號.
        clean (bool, optional): 是否去掉尾綴『股份有限公司／有限公司』. Defaults to True.

    Returns:
        str | None: 公司名稱或簡稱，若找不到則回傳 None.
    """
    stock_util = TwStock()
    return stock_util.get_name(code, clean)


def get_stock_code_by_name(name: str) -> str | list[dict] | None:
    """Get the stock code by company name (簡稱優先 → 全名 → 模糊比對).

    搜尋順序：
      1. 精確比對簡稱
      2. 精確比對全名
      3. 模糊比對（名稱或簡稱包含關鍵字）
         - 多筆結果 → 回傳候選清單 (最多 10 筆)
         - 單筆結果 → 直接回傳代號

    Args:
        name (str): 公司名稱、簡稱或關鍵字.

    Returns:
        str | list[dict] | None:
            - str: 單一股票代號
            - list[dict]: 多筆候選，包含 code/name/short
            - None: 找不到
    """
    stock_util = TwStock()

    # 1) 精確比對簡稱
    for it in stock_util.all_items:
        if it.get("short") == name:
            return it["code"]

    # 2) 精確比對全名
    code = stock_util.get_code(name)
    if code:
        return code

    # 3) 模糊比對 (名稱 or 簡稱包含關鍵字)
    matches = stock_util.search_by_name(name, clean=False)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]["code"]

    # 多筆候選 → 回傳前 10 筆，提醒使用者不唯一
    return matches

def refresh_stocks() -> list[dict]:
    """強制刷新股票清單，忽略快取 TTL。

    Returns:
        list[dict]: 最新股票清單
    """
    stock_util = TwStock()
    stock_util.refresh(force=True)
    return stock_util.all_items



if __name__ == "__main__":
    print("Stocks", len(TwStock().all_items))
    print("Sample stocks:", get_stocks_list()[:5])
    print("Stock name for code '2330':", get_stock_name_by_code("2330"))
    print("Stock code for name '台積電':", get_stock_code_by_name("台積電"))
    print("Stock code for short name '鴻海':", get_stock_code_by_name("鴻海"))
    print(f"Stock code for keyword '科技':", len(get_stock_code_by_name("科技")), "candidates")
    print(get_stock_code_by_name("元大"))
