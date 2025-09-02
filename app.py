
import os, re
import json, time, uuid
from datetime import datetime, timedelta, timezone
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# ====== 快取區（會隨 Render 睡眠清空）======
TRACKS_CACHE = {}
LAST_SHEET_LOAD_TIME = 0
CACHE_TTL_SECONDS = 86400  # 每天更新一次

from services import (
    get_today_major_announcements,
    get_historical_announcements,
    get_bookbuilding_announcements,
    get_stock_name_by_code,
    get_stock_code_by_name,
)

# ===== Flask / LINE 初始化 =====
app = Flask(__name__)

def _getenv_required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val

CHANNEL_ACCESS_TOKEN = _getenv_required("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = _getenv_required("LINE_CHANNEL_SECRET")

line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ====== 維護模式 & 冷啟動 ======
MAINTENANCE_MODE = os.environ.get("MAINTENANCE_MODE", "false").lower() == "true"
BOOT_ID = os.environ.get("BOOT_ID") or str(uuid.uuid4())
START_TS = time.time()

def uptime_seconds() -> float:
    return time.time() - START_TS

def is_cold_start(threshold: float = 30.0) -> bool:
    return uptime_seconds() < threshold

# ====== 追蹤清單（JSON 僅存代號） ======
import gspread
from oauth2client.service_account import ServiceAccountCredentials

SHEET_ID = "1guRGoBrCtqcbqZq4Z4nxyCHmYTNYjnrXrgGCKL8xdn0"  

def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("/etc/secrets/conductive-coil-441304-n8-ccb680eb2dda.json", scope)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1

def load_tracks(force_reload=False) -> dict:
    global TRACKS_CACHE, LAST_SHEET_LOAD_TIME
    now = time.time()
    if TRACKS_CACHE and not force_reload and (now - LAST_SHEET_LOAD_TIME < CACHE_TTL_SECONDS):
        return TRACKS_CACHE

    try:
        sheet = get_sheet()
        rows = sheet.get_all_values()
    except Exception as e:
        print(f"⚠️ GSheet 調用失敗，使用快取：{e}")
        return TRACKS_CACHE

    result = {}
    header = rows[0]
    idx_user = header.index("user_id")
    idx_name = header.index("stock_code")  # column name 其實還是叫 stock_code 也沒差

    for row in rows[1:]:
        uid = row[idx_user]
        name = str(row[idx_name]).strip()
        result.setdefault(uid, set()).add(name)

    TRACKS_CACHE = {uid: list(names) for uid, names in result.items()}
    LAST_SHEET_LOAD_TIME = now
    return TRACKS_CACHE






def save_tracks(data: dict, user_id: str, new_names: list[str]):
    try:
        sheet = get_sheet()
        for name in new_names:
            sheet.append_row([user_id, name], value_input_option="RAW")
    except Exception as e:
        print(f"⚠️ GSheet append_row 寫入失敗：{e}")
    global TRACKS_CACHE, LAST_SHEET_LOAD_TIME
    TRACKS_CACHE[user_id] = list(set(TRACKS_CACHE.get(user_id, []) + new_names))
    LAST_SHEET_LOAD_TIME = time.time()








# ====== 工具 ======
def _parse_roc_date(roc_str: str) -> datetime:
    """將 '113/09/03' 格式轉為 datetime 物件"""
    parts = [int(p) for p in roc_str.strip().split("/")]
    if len(parts) != 3:
        raise ValueError("格式錯誤")
    y, m, d = parts
    return datetime(year=y + 1911, month=m, day=d)

def _remove_user_rows(user_id: str):
    try:
        sheet = get_sheet()
        all_rows = sheet.get_all_values()
        header = all_rows[0]
        idx_user = header.index("user_id")

        # 過濾掉目標使用者的紀錄
        filtered_rows = [row for row in all_rows[1:] if row[idx_user] != user_id]

        # 重新寫入表頭 + 剩下的紀錄
        sheet.clear()
        sheet.append_row(header)
        if filtered_rows:
            sheet.append_rows(filtered_rows, value_input_option="RAW")
    except Exception as e:
        print(f"⚠️ 清除 GSheet 使用者資料失敗：{e}")


def _split_symbols(s: str) -> list[str]:
    """
    把使用者輸入切成多個 token：
    支援空白、逗號、全形逗號、頓號、分號、換行等。
    例：'2330 台積電,0050；0056\n聯發科' -> ['2330','台積電','0050','0056','聯發科']
    """
    return [t.strip() for t in re.split(r"[,\s，、；;]+", s) if t.strip()]

def _ensure_text(s) -> str:
    try:
        s = ("" if s is None else str(s)).strip()
    except Exception:
        s = "（查無資料或發生未知錯誤）"
    return s or "（查無資料或發生未知錯誤）"

def _owner_id(event: MessageEvent) -> str:
    """在 1:1 / 群組 / 聊天室下都能得到一個穩定 key。"""
    src = event.source
    if getattr(src, "user_id", None):
        return f"user:{src.user_id}"
    if getattr(src, "group_id", None):
        return f"group:{src.group_id}"
    if getattr(src, "room_id", None):
        return f"room:{src.room_id}"
    return "unknown"

def _fmt_rows(rows: list[dict], max_chars: int = 4800) -> tuple[str, bool]:
    """
    將公告格式化為多筆訊息，每則格式如下：
    【公司名稱】主旨
    📅 公告日：xxxx/xx/xx

    max_chars：限制最大字數（避免超過 LINE 限制）
    回傳 tuple: (格式化後字串, 是否有被截斷)
    """
    out = []
    total_len = 0
    for x in rows:
        name = _ensure_text(x.get("name"))
        subject = _ensure_text(x.get("subject"))
        date_pub = _ensure_text(x.get("date_pub"))
        msg = f"【{name}】{subject}\n📅 公告日：{date_pub}"
        if total_len + len(msg) + 2 > max_chars:  # +2 是換行符號
            return ("\n\n".join(out), True)
        out.append(msg)
        total_len += len(msg) + 2
    return ("\n\n".join(out), False)

def _fmt_bookbuild_rows(rows: list[dict], max_chars: int = 4800) -> tuple[str, bool]:
    out = []
    total_len = 0
    truncated = False

    for r in rows:
        seq = _ensure_text(r.get("序號"))
        company = _ensure_text(r.get("發行公司"))
        period = _ensure_text(r.get("圈購期間"))
        price = _ensure_text(r.get("價格"))

        line = f"📌 {seq} {company}\n📅 圈購期間：{period}\n💰 價格區間：{price}"
        if total_len + len(line) + 2 > max_chars:
            truncated = True
            break
        out.append(line)
        total_len += len(line) + 2

    return "\n\n".join(out), truncated

_TPE = timezone(timedelta(hours=8))
def _roc_date(d: datetime.date) -> str:
    y = d.year - 1911
    return f"{y:03d}/{d.month:02d}/{d.day:02d}"

def _taipei_today():
    return datetime.now(tz=_TPE).date()

def resolve_stock_name(token: str) -> str | None:
    if not token: return None
    token = token.strip().lstrip("'")

    if token.isdigit():  # 輸入是代號，要幫他查公司名稱
        return get_stock_name_by_code(token)
    return token  # 輸入是公司名稱就直接回傳



# ====== 預熱 ======
@app.before_first_request
def warm_up():
    print("🧠 預熱 TRACKS_CACHE")
    load_tracks(force_reload=True)

# ====== 健康檢查 ======
@app.get("/meta")
def meta():
    tracks = load_tracks()
    return jsonify({
        "boot_id": BOOT_ID,
        "uptime_sec": round(uptime_seconds(), 3),
        "cold_start_guess": is_cold_start(),
        "tracks_size": sum(len(v) for v in tracks.values()) if isinstance(tracks, dict) else 0,
        "python_version": os.sys.version,
    }), 200

@app.get("/")
def health():
    return "ok", 200

@app.get("/healthz")
def healthz():
    return "ok", 200

# ====== LINE Webhook ======
@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK", 200

# ====== 處理文字訊息 ======
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event: MessageEvent):
    owner = _owner_id(event)
    t = (event.message.text or "").strip()

    def reply(msg: str):
        try:
            if is_cold_start():
                msg = (msg or "") + "\n\n（伺服器剛醒來，回覆可能稍慢）"
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=_ensure_text(msg))
            )
        except LineBotApiError as le:
            try:
                line_bot_api.reply_message(
                    event.reply_token,
                    TextSendMessage(text=_ensure_text(f"回覆失敗：{getattr(le, 'message', str(le))[:300]}"))
                )
            except Exception:
                pass

    # === 維護模式 ===
    if MAINTENANCE_MODE:
        reply("現在正在維護中，敬請期待 ")
        return

    try:
        # === 追蹤清單：add（支援多個） ===
        if t.lower().startswith("add "):
            raw = t[4:].strip()
            items = _split_symbols(raw)
            if not items:
                reply("用法：add 2330 台積電 0050（可一次多個）")
                return

            tracks = load_tracks()
            my_stocks = list(tracks.get(owner, []))
            added, skipped, unknown = [], [], []

            for tok in items:
                name = resolve_stock_name(tok)
                if not name:
                    unknown.append(tok)
                    continue
                if name in my_stocks or name in added:
                    skipped.append(name)
                    continue
                my_stocks.append(name)
                added.append(name)

            tracks[owner] = my_stocks
            parts = []
            if added:
                save_tracks(tracks, owner, added)
            if skipped:
                parts.append("↪️ 已在清單：\n" + "\n".join(f"• {x}" for x in skipped))
            if unknown:
                parts.append("❓ 未辨識：\n" + "\n".join(f"• {x}" for x in unknown))
            reply("\n\n".join(parts) or "沒有可加入的項目")
            return



        # === 追蹤清單：del（支援多個） ===
        if t.lower().startswith("del "):
            raw = t[4:].strip()
            items = _split_symbols(raw)
            if not items:
                reply("用法：del 2330 台積電 0050（可一次多個）")
                return

            tracks = load_tracks()
            my_stocks = list(tracks.get(owner, []))
            removed, notfound = [], []

            for tok in items:
                name = resolve_stock_name(tok)
                if not name or name not in my_stocks:
                    notfound.append(tok)
                    continue
                my_stocks.remove(name)
                removed.append(name)

            tracks[owner] = my_stocks
            TRACKS_CACHE[owner] = my_stocks
            LAST_SHEET_LOAD_TIME = time.time()

            parts = []
            if removed:
                parts.append("🗑 已刪除：\n" + "\n".join(f"• {x}" for x in removed))
            if notfound:
                parts.append("🔍 清單中沒有/無法辨識：\n" + "\n".join(f"• {x}" for x in notfound))
            reply("\n\n".join(parts) or "沒有可刪除的項目")
            return


        # === 追蹤清單：ls ===
        if t.lower() == "ls":
            tracks = load_tracks()
            my_stocks = list(tracks.get(owner, []))
            if not my_stocks:
                reply("你的追蹤清單為空。\n用法：add 台積電 或 add 2330")
            else:
                reply("你的追蹤清單：\n" + "\n".join(f"• {name}" for name in my_stocks))
            return



        # === 追蹤清單：clear ===
        if t.lower() == "clear":
            tracks = load_tracks()
            tracks[owner] = []
            TRACKS_CACHE[owner] = []
            LAST_SHEET_LOAD_TIME = time.time()

            # ✅ 同步刪除 GSheet 上的資料
            _remove_user_rows(owner)

            reply("已清空你的追蹤清單（包含雲端紀錄）。")
            return

        # === 公告查詢（今日） ===
        if t == "爬取今日數據":
            tracks = load_tracks()
            my_stocks = list(tracks.get(owner, []))
            if not my_stocks:
                reply("清單是空的。先用：add 台積電 或 add 2330")
                return

            blocks = []
            for name in my_stocks:
                rows = get_today_major_announcements(name)
                block_text, truncated = _fmt_rows(rows)
                if truncated:
                    block_text += "\n\n📎 更多公告請參考公開資訊觀測站：\n🔗 https://mops.twse.com.tw"
                blocks.append(block_text)

            reply("📣 今日公告：\n" + "\n\n".join(blocks))
            return



        # === 公告查詢（昨日） ===
        if t == "爬取昨日數據":
            tracks = load_tracks()
            my_stocks = list(tracks.get(owner, []))
            if not my_stocks:
                reply("清單是空的。先用：add 台積電 或 add 2330")
                return

            y = _taipei_today() - timedelta(days=1)
            s = _roc_date(y)
            all_rows = []

            for name in my_stocks:
                rows = get_historical_announcements(s, s, subject=name)
                all_rows.extend(rows)

            msg, truncated = _fmt_rows(all_rows, max_chars=4800)
            if truncated:
                msg += "\n\n📎 顯示不完，請至公開資訊觀測站查閱：\n🔗 https://mops.twse.com.tw"

            reply("🗓 昨日公告：\n\n" + msg)
            return



        # === 原本的指令 ===
        if t.startswith("mops today"):
            kw = t.replace("mops today", "", 1).strip()
            rows = get_today_major_announcements(kw)
            msg, truncated = _fmt_rows(rows, max_chars=4800)
            if not msg.strip():
                msg = "今日查無資料"
            elif truncated:
                msg += "\n\n📎 更多公告請參考公開資訊觀測站：\n🔗 https://mops.twse.com.tw"
            reply(msg)
            return
        
        if t.startswith("mops range"):
            parts = t.split()
            if len(parts) >= 4:
                sdate, edate = parts[2], parts[3]
                subject = " ".join(parts[4:]) if len(parts) > 4 else ""

                if not subject:
                    reply("請提供查詢關鍵字，例如公司名稱或主旨內容\n用法：mops range 114/08/01 114/08/31 台積電")
                    return

                # 限制區間
                try:
                    start = _parse_roc_date(sdate)
                    end = _parse_roc_date(edate)
                    if (end - start).days > 90:
                        reply("查詢區間最多支援 90 天，請縮短日期範圍")
                        return
                except:
                    reply("日期格式錯誤，請用 114/08/01 格式")
                    return

                rows = get_historical_announcements(sdate, edate, subject=subject)
                msg, truncated = _fmt_rows(rows, max_chars=4800)
                if truncated:
                    msg += "\n\n📎 顯示不完，請至公開資訊觀測站查閱：\n🔗 https://mops.twse.com.tw"
                reply(msg or "無資料")
            else:
                reply("用法：mops range 114/08/01 114/08/31 [關鍵字]")
            return

        if t.startswith("book"):
            rows = get_bookbuilding_announcements()
            msg, truncated = _fmt_bookbuild_rows(rows)
            if not msg.strip():
                msg = "查無詢圈公告"
            elif truncated:
                msg += "\n\n📎 更多請參考公開資訊觀測站：\n🔗 https://mops.twse.com.tw"
            reply(f"📦 詢圈資訊：\n\n{msg}")
            return

        if t.startswith("stock name"):
            code = t.replace("stock name", "", 1).strip()
            reply(get_stock_name_by_code(code) or "查無此代號")
            return

        if t.startswith("stock code"):
            name = t.replace("stock code", "", 1).strip()
            reply(get_stock_code_by_name(name) or "查無此名稱")
            return

        # === Help ===
        reply(
            "可用指令：\n"
            "1) add <股票代號或名稱>\n"
            "2) del <股票代號或名稱>\n"
            "3) ls\n"
            "4) clear\n"
            "5) 爬取今日數據\n"
            "6) 爬取昨日數據\n"
            "其他：\n"
            "• mops today [關鍵字]\n"
            "• mops range 114/08/01 114/08/31 [關鍵字]\n"
            "• book\n"
            "• stock name 2330 / stock code 台積電"
        )

    except Exception as e:
        reply(f"發生錯誤：{e}")
