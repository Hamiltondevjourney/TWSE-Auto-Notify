
import os, re
import json, time, uuid
from datetime import datetime, timedelta, timezone
from flask import Flask, request, abort, jsonify
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# 服務邏輯（你專案裡的 services 模組）
from services import (
    # MOPS
    get_today_major_announcements,
    get_historical_announcements,
    # Bookbuilding
    get_bookbuilding_announcements,
    # Stocks
    get_stocks_list,
    get_stock_name_by_code,
    get_stock_code_by_name,
    refresh_stocks,
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
TRACK_FILE = os.environ.get("TRACK_FILE", "/tmp/tracks.json")

def load_tracks() -> dict:
    try:
        with open(TRACK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_tracks(data: dict) -> None:
    os.makedirs(os.path.dirname(TRACK_FILE), exist_ok=True)
    tmp = TRACK_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, TRACK_FILE)

# ====== 工具 ======
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

def _fmt_rows(rows: list[dict], limit: int = 5) -> str:
    return "（無）" if not rows else "\n".join(
        f"• {(_ensure_text(x.get('date_pub')))} {(_ensure_text(x.get('name')))}：{_ensure_text(x.get('subject'))}"
        for x in rows[:limit]
    )

_TPE = timezone(timedelta(hours=8))
def _roc_date(d: datetime.date) -> str:
    y = d.year - 1911
    return f"{y:03d}/{d.month:02d}/{d.day:02d}"

def _taipei_today():
    return datetime.now(tz=_TPE).date()

def resolve_to_code_and_name(token: str) -> tuple[str | None, str | None]:
    token = (token or "").strip()
    if not token:
        return None, None
    if token.isdigit():
        name = get_stock_name_by_code(token)
        return (token, name) if name else (None, None)
    code = get_stock_code_by_name(token)
    if not code:
        return None, None
    name = get_stock_name_by_code(code) or token
    return code, name

# ====== 健康檢查 ======
@app.get("/")
def health():
    return "ok", 200

@app.get("/healthz")
def healthz():
    return "ok", 200

@app.get("/meta")
def meta():
    tracks = load_tracks()
    return jsonify({
        "boot_id": BOOT_ID,
        "uptime_sec": round(uptime_seconds(), 3),
        "cold_start_guess": is_cold_start(),
        "tracks_size": sum(len(v) for v in tracks.values()) if isinstance(tracks, dict) else 0,
        "certifi_path": _ca,
        "python_version": os.sys.version,
    }), 200

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
        reply("現在正在維護中，敬請期待 🙏")
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
            my_codes = list(tracks.get(owner, []))

            added, skipped, unknown = [], [], []

            for tok in items:
                code, name = resolve_to_code_and_name(tok)
                if not code:
                    unknown.append(tok)
                    continue
                display = f"{code} {name or ''}".strip()
                if code in my_codes or display in added:  # 已在清單或同批重覆
                    skipped.append(display)
                    continue
                my_codes.append(code)
                added.append(display)

            tracks[owner] = my_codes
            save_tracks(tracks)

            parts = []
            if added:
                parts.append("✅ 已加入：\n" + "\n".join(f"• {x}" for x in added))
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
            my_codes = list(tracks.get(owner, []))

            removed, notfound = [], []

            for tok in items:
                code, name = resolve_to_code_and_name(tok)
                if not code:
                    notfound.append(tok)
                    continue
                display = f"{code} {name or ''}".strip()
                if code in my_codes:
                    my_codes = [c for c in my_codes if c != code]
                    removed.append(display)
                else:
                    notfound.append(display)

            tracks[owner] = my_codes
            save_tracks(tracks)

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
            my_codes = list(tracks.get(owner, []))
            if not my_codes:
                reply("你的追蹤清單為空。\n用法：add 台積電 或 add 2330")
            else:
                lines = []
                for code in my_codes:
                    name = get_stock_name_by_code(code) or "（未知名稱）"
                    lines.append(f"{code} {name}")
                reply("追蹤清單：\n" + "\n".join(lines))
            return

        # === 追蹤清單：clear ===
        if t.lower() == "clear":
            tracks = load_tracks()
            tracks[owner] = []
            save_tracks(tracks)
            reply("已清空你的追蹤清單。")
            return

        # === 公告查詢（今日） ===
        if t == "爬取今日數據":
            tracks = load_tracks()
            my_codes = list(tracks.get(owner, []))
            if not my_codes:
                reply("清單是空的。先用：add 台積電 或 add 2330")
                return
            blocks = []
            for code in my_codes:
                name = get_stock_name_by_code(code) or code
                rows = get_today_major_announcements(name)
                blocks.append(f"\n{_fmt_rows(rows)}")
            reply("📣 今日公告：\n" + "\n\n".join(blocks))
            return

        # === 公告查詢（昨日） ===
        if t == "爬取昨日數據":
            tracks = load_tracks()
            my_codes = list(tracks.get(owner, []))
            if not my_codes:
                reply("清單是空的。先用：add 台積電 或 add 2330")
                return
            y = _taipei_today() - timedelta(days=1)
            s = _roc_date(y)
            blocks = []
            for code in my_codes:
                name = get_stock_name_by_code(code) or code
                rows = get_historical_announcements(s, s, subject=name)
                blocks.append(f"\n{_fmt_rows(rows)}")
            reply("🗓 昨日公告：\n" + "\n\n".join(blocks))
            return

        # === 原本的指令 ===
        if t.startswith("mops today"):
            kw = t.replace("mops today", "", 1).strip()
            rows = get_today_major_announcements(kw)
            msg = "\n".join(f"{_ensure_text(x.get('date_pub'))} {_ensure_text(x.get('name'))}：{_ensure_text(x.get('subject'))}" for x in rows[:5]) or "今日查無資料"
            reply(msg)
            return

        if t.startswith("mops range"):
            parts = t.split()
            if len(parts) >= 4:
                sdate, edate = parts[2], parts[3]
                subject = " ".join(parts[4:]) if len(parts) > 4 else ""
                rows = get_historical_announcements(sdate, edate, subject=subject)[:5]
                msg = "\n".join(f"{_ensure_text(x.get('date_pub'))} {_ensure_text(x.get('name'))}：{_ensure_text(x.get('subject'))}" for x in rows) or "無資料"
                reply(msg)
            else:
                reply("用法：mops range 114/08/01 114/08/31 [關鍵字]")
            return

        if t.startswith("book"):
            rows = get_bookbuilding_announcements()
            msg = "\n".join(
                f"{_ensure_text(r.get('序號'))} {r.get('發行公司')} | {r.get('圈購期間')} | {r.get('價格')}"
                for r in rows
            ) or "查無詢圈公告"
            reply(msg)
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
