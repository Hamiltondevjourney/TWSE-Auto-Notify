import threading
import time
from tw_scrapers.mops_today_news import fetch_today_major_announcements

_sent_cache = {}
_notify_threads = {}

def start_periodic_notify(user_id, keyword, interval_min, push_func):
    key = (user_id, keyword)
    def job():
        while True:
            data = fetch_today_major_announcements(keyword)
            sent = _sent_cache.setdefault(user_id, set())
            new_msgs = []
            for row in data:
                msg = f"{row['name']} {row['subject']} {row['date_pub']}"
                if msg not in sent:
                    new_msgs.append(msg)
                    sent.add(msg)
            for msg in new_msgs:
                push_func(user_id, msg)
            # 檢查是否被要求停止
            if not _notify_threads.get(key, True):
                break
            time.sleep(interval_min * 60)
    # 標記為啟動
    _notify_threads[key] = True
    t = threading.Thread(target=job, daemon=True)
    t.start()

def stop_periodic_notify(user_id, keyword):
    key = (user_id, keyword)
    _notify_threads[key] = False