# -*- coding: utf-8 -*-
"""
Утренний новостной дайджест по Таиланду и ЮВА.
Собирает новости (Google News RSS) и общественные интересы (Google Trends RSS),
пересказывает по-русски через GPT-5.4 Nano и отправляет в телеграм-канал.
Запускается один раз в сутки по расписанию Railway.
"""

import os
import sys
import time
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from openai import OpenAI

# ── Настройки приходят из переменных окружения Railway ──────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
MODEL = os.environ.get("MODEL", "gpt-5.4-nano")

HOURS_BACK = 30  # какие новости считать свежими

NEWS_FEEDS = {
    "Политика": "https://news.google.com/rss/search?q=%E0%B8%81%E0%B8%B2%E0%B8%A3%E0%B9%80%E0%B8%A1%E0%B8%B7%E0%B8%AD%E0%B8%87+%E0%B9%84%E0%B8%97%E0%B8%A2&hl=th&gl=TH&ceid=TH:th",
    "Экономика": "https://news.google.com/rss/search?q=%E0%B9%80%E0%B8%A8%E0%B8%A3%E0%B8%A9%E0%B8%90%E0%B8%81%E0%B8%B4%E0%B8%88+%E0%B9%84%E0%B8%97%E0%B8%A2&hl=th&gl=TH&ceid=TH:th",
    "Международные": "https://news.google.com/rss/search?q=%E0%B8%95%E0%B9%88%E0%B8%B2%E0%B8%87%E0%B8%9B%E0%B8%A3%E0%B8%B0%E0%B9%80%E0%B8%97%E0%B8%A8+%E0%B9%84%E0%B8%97%E0%B8%A2&hl=th&gl=TH&ceid=TH:th",
    "АСЕАН": "https://news.google.com/rss/search?q=%E0%B8%AD%E0%B8%B2%E0%B9%80%E0%B8%8B%E0%B8%B5%E0%B8%A2%E0%B8%99&hl=th&gl=TH&ceid=TH:th",
}

TRENDS_FEED = "https://trends.google.com/trending/rss?geo=TH"

MAX_PER_FEED = 15
MAX_TRENDS = 15


def is_fresh(entry, hours=HOURS_BACK):
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return True
    pub = datetime(*t[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - pub <= timedelta(hours=hours)


def clean_source(entry):
    src = getattr(entry, "source", None)
    if src and getattr(src, "title", None):
        return src.title
    return ""


def fetch_news():
    items = []
    for topic, url in NEWS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[warn] лента '{topic}' не загрузилась: {e}", file=sys.stderr)
            continue
        count = 0
        for entry in feed.entries:
            if count >= MAX_PER_FEED:
                break
            if not is_fresh(entry):
                continue
            items.append({
                "topic": topic,
                "title": getattr(entry, "title", "").strip(),
                "link": getattr(entry, "link", "").strip(),
                "source": clean_source(entry),
            })
            count += 1
    return items


def fetch_trends():
    trends = []
    try:
        feed = feedparser.parse(TRENDS_FEED)
    except Exception as e:
        print(f"[warn] тренды не загрузились: {e}", file=sys.stderr)
        return trends
    for entry in feed.entries[:MAX_TRENDS]:
        traffic = ""
        for key in ("ht_approx_traffic", "approx_traffic"):
            if hasattr(entry, key):
                traffic = getattr(entry, key)
                break
        trends.append({
            "title": getattr(entry, "title", "").strip(),
            "traffic": traffic,
        })
    return trends


def build_prompt(news, trends):
    news_lines = []
    for i, n in enumerate(news, 1):
        src = f" | источник: {n['source']}" if n["source"] else ""
        news_lines.append(f"{i}. [{n['topic']}] {n['title']}{src}\n   ссылка: {n['link']}")
    news_block = "\n".join(news_lines) if news_lines else "(новостей нет)"

    trend_lines = []
    for i, t in enumerate(trends, 1):
        tr = f" (~{t['traffic']})" if t["traffic"] else ""
        trend_lines.append(f"{i}. {t['title']}{tr}")
    trends_block = "\n".join(trend_lines) if trend_lines else "(трендов нет)"
    return news_block, trends_block


SYSTEM_PROMPT = """Ты — редактор утреннего новостного дайджеста о Таиланде и Юго-Восточной Азии для русскоязычного читателя.

Тебе дают сырой список тайских новостей (заголовки на тайском со ссылками, сгруппированные по темам) и список поисковых трендов Таиланда.

Собери связный дайджест НА РУССКОМ ЯЗЫКЕ по правилам:

1. Сгруппируй новости в сюжеты. Если несколько заголовков об одном событии — объедини в один пункт, а все ссылки перечисли вместе.
2. ПРИОРИТЕТ — сюжетам, подтверждённым несколькими источниками: ставь их выше и раскрывай подробнее.
3. Раздели дайджест на разделы: Политика, Экономика, Международные отношения. Всё, что не относится к этим темам (спорт, шоу-бизнес, происшествия), в новостную часть не включай.
4. Каждый пункт — краткий пересказ СВОИМИ СЛОВАМИ (2–4 предложения), НЕ копируй заголовки дословно. К каждому пункту дай ссылки в формате HTML: <a href="URL">издание</a>.
5. В конце — отдельный блок «Что ищут в Таиланде» на основе трендов. Раздели тренды по категориям (политика, экономика, спорт, шоу-бизнес, другое) и коротко поясни каждый. Пиши честно, даже если общество смотрит футбол или сериалы.

Оформление для Telegram:
- HTML-теги: <b>жирный</b> для заголовков разделов, <a href="">ссылки</a>.
- Кавычки — только ёлочки «вот такие».
- Тире не используй как знак паузы или выделения.
- Не выдумывай факты, которых нет в заголовках.
- Пиши живым человеческим языком, без канцелярита."""


def generate_digest(news, trends):
    news_block, trends_block = build_prompt(news, trends)
    today = datetime.now(timezone(timedelta(hours=7))).strftime("%d.%m.%Y")
    user_msg = (
        f"Дата дайджеста: {today} (утро по Бангкоку).\n\n"
        f"=== НОВОСТИ ===\n{news_block}\n\n"
        f"=== ПОИСКОВЫЕ ТРЕНДЫ ТАИЛАНДА ===\n{trends_block}\n\n"
        f"Собери дайджест по правилам."
    )
    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    return resp.choices[0].message.content.strip()


def split_message(text, limit=3800):
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > limit:
            if current:
                parts.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    return parts


def send_to_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    for part in split_message(text):
        r = requests.post(url, data={
            "chat_id": TELEGRAM_CHANNEL_ID,
            "text": part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=30)
        if not r.ok:
            print(f"[error] Telegram ответил: {r.status_code} {r.text}", file=sys.stderr)
        time.sleep(1)


def main():
    missing = [name for name, val in [
        ("OPENAI_API_KEY", OPENAI_API_KEY),
        ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
        ("TELEGRAM_CHANNEL_ID", TELEGRAM_CHANNEL_ID),
    ] if not val]
    if missing:
        print(f"[error] не заданы переменные: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    print("Собираю новости...")
    news = fetch_news()
    print(f"Найдено новостей: {len(news)}")

    print("Собираю тренды...")
    trends = fetch_trends()
    print(f"Найдено трендов: {len(trends)}")

    if not news and not trends:
        print("[warn] нет данных, дайджест не отправлен")
        return

    print("Пишу дайджест...")
    digest = generate_digest(news, trends)

    print("Отправляю в Telegram...")
    send_to_telegram(digest)
    print("Готово.")


if __name__ == "__main__":
    main()
