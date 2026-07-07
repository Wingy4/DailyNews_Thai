# -*- coding: utf-8 -*-
"""
Утренний дайджест по Таиланду и ЮВА.
Основа: английский Google News ( с приоритетом на Bangkok Post, Nation Thailand и т.д).
Контроль охвата: тайский Google News.
Общественный интерес: Google Trends по реальной выборке, с пояснениями
через связанные новости, которые сама лента отдаёт вместе с каждым трендом.
Пересказ по-русски через выбранную модель. Запуск раз в сутки по расписанию Railway.
"""

import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

import feedparser
import requests
from openai import OpenAI

# ── Настройки из переменных окружения Railway ──────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.environ.get("TELEGRAM_CHANNEL_ID", "")
MODEL = os.environ.get("MODEL", "gpt-5.4-mini")
TEST_MODE = os.environ.get("TEST_MODE", "false").lower() == "true"

HOURS_BACK = 30
MAX_PER_FEED = 12
MAX_THAI_PER_FEED = 12
MAX_TRENDS = 15

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

# Англоязычная аналитика — основа дайджеста
ENGLISH_FEEDS = [
    ("Bangkok Post — Таиланд", "https://www.bangkokpost.com/rss/data/thailand.xml"),
    ("Bangkok Post — Бизнес", "https://www.bangkokpost.com/rss/data/business.xml"),
    ("Bangkok Post — Мир", "https://www.bangkokpost.com/rss/data/world.xml"),
    ("Политика (Таиланд)", "https://news.google.com/rss/search?q=Thailand+politics&hl=en-US&gl=TH&ceid=TH:en"),
    ("Экономика (Таиланд)", "https://news.google.com/rss/search?q=Thailand+economy&hl=en-US&gl=TH&ceid=TH:en"),
    ("Международные (Таиланд)", "https://news.google.com/rss/search?q=Thailand+foreign+relations&hl=en-US&gl=TH&ceid=TH:en"),
    ("АСЕАН", "https://news.google.com/rss/search?q=ASEAN&hl=en-US&gl=SG&ceid=SG:en"),
]

# Тайский Google News — контроль охвата
THAI_FEEDS = [
    ("การเมือง", "https://news.google.com/rss/search?q=%E0%B8%81%E0%B8%B2%E0%B8%A3%E0%B9%80%E0%B8%A1%E0%B8%B7%E0%B8%AD%E0%B8%87+%E0%B9%84%E0%B8%97%E0%B8%A2&hl=th&gl=TH&ceid=TH:th"),
    ("เศรษฐกิจ", "https://news.google.com/rss/search?q=%E0%B9%80%E0%B8%A8%E0%B8%A3%E0%B8%A9%E0%B8%90%E0%B8%81%E0%B8%B4%E0%B8%88+%E0%B9%84%E0%B8%97%E0%B8%A2&hl=th&gl=TH&ceid=TH:th"),
    ("ต่างประเทศ", "https://news.google.com/rss/search?q=%E0%B8%95%E0%B9%88%E0%B8%B2%E0%B8%87%E0%B8%9B%E0%B8%A3%E0%B8%B0%E0%B9%80%E0%B8%97%E0%B8%A8+%E0%B9%84%E0%B8%97%E0%B8%A2&hl=th&gl=TH&ceid=TH:th"),
    ("อาเซียน", "https://news.google.com/rss/search?q=%E0%B8%AD%E0%B8%B2%E0%B9%80%E0%B8%8B%E0%B8%B5%E0%B8%A2%E0%B8%99&hl=th&gl=TH&ceid=TH:th"),
]

TRENDS_FEED = "https://trends.google.com/trending/rss?geo=TH"

YOUTUBE_FEEDS = [
    ("Ch7HD News", "https://www.youtube.com/feeds/videos.xml?channel_id=UC2OtDM92rLjt4mm43ED1Q-w"),
    ("Workpoint News 23", "https://www.youtube.com/feeds/videos.xml?channel_id=UC3WyfUir0HD8sFI4AVAl6SQ"),
    ("ThaiPBS News", "https://www.youtube.com/feeds/videos.xml?channel_id=UCOFvLl4bKwCIZg0r4EBQLug"),
    ("THE STANDARD", "https://www.youtube.com/feeds/videos.xml?channel_id=UCk1v3FzlMu3r34LYgoHpH2w"),
    ("Thairath News", "https://www.youtube.com/feeds/videos.xml?channel_id=UCrFDdD-EE05N7gjwZho2wqw"),
    ("Nation Online", "https://www.youtube.com/feeds/videos.xml?channel_id=UCIoFfVIOrRRbI-WVdDhTTwg"),
]

# Этот канал не фильтруем по ключевым словам — берём все его ролики как есть
YOUTUBE_FEEDS_NO_FILTER = [
    ("โหนกระแส", "https://www.youtube.com/feeds/videos.xml?channel_id=UCXm0bpjlfB0AF-ZdPhT0K1A"),
]

# Слова-маркеры аналитических программ и обсуждений в заголовках роликов
ANALYSIS_KEYWORDS = [
    "เจาะประเด็น", "วิเคราะห์", "ถกประเด็น", "เจาะลึก",
    "มุมมอง", "สนทนา", "เปิดประเด็น", "ดีเบต", "พูดคุย",
    "โหนกระแส", "ติ่งข่าว",
]

def is_analysis(title):
    """Проверяет, похож ли заголовок ролика на аналитику или обсуждение."""
    return any(word in title for word in ANALYSIS_KEYWORDS)

MONTHS_RU = ["января", "февраля", "марта", "апреля", "мая", "июня",
             "июля", "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]


def format_date_header():
    """Строка с сегодняшней датой по Бангкоку, для самой первой строки дайджеста."""
    now = datetime.now(timezone(timedelta(hours=7)))
    weekday = WEEKDAYS_RU[now.weekday()]
    month = MONTHS_RU[now.month - 1]
    return f"<b>{now.day} {month} {now.year}, {weekday}</b>"

def strip_html(text):
    """Убирает html-теги и лишние пробелы из описания."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def is_fresh(entry, hours=HOURS_BACK):
    t = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not t:
        return True
    pub = datetime(*t[:6], tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - pub <= timedelta(hours=hours)


def entry_source(entry):
    src = getattr(entry, "source", None)
    if src and getattr(src, "title", None):
        return src.title
    return ""


def fetch_feed_items(feeds, limit, with_desc):
    """Собирает свежие записи из списка лент."""
    items = []
    for origin, url in feeds:
        try:
            feed = feedparser.parse(url, agent=HEADERS["User-Agent"])
        except Exception as e:
            print(f"[warn] лента '{origin}' не загрузилась: {e}", file=sys.stderr)
            continue
        count = 0
        for entry in feed.entries:
            if count >= limit:
                break
            if not is_fresh(entry):
                continue
            item = {
                "origin": origin,
                "title": getattr(entry, "title", "").strip(),
                "link": getattr(entry, "link", "").strip(),
                "source": entry_source(entry),
            }
            if with_desc:
                desc = strip_html(getattr(entry, "summary", "") or getattr(entry, "description", ""))
                item["desc"] = desc[:600]
            items.append(item)
            count += 1
        print(f"  {origin}: {count}", file=sys.stderr)
    return items


def fetch_trends():
    """
    Тянет тренды Google Trends вместе со связанными новостями.
    Разбор идёт напрямую по xml, чтобы не потерять несколько новостей на тренд
    (feedparser склеивает повторяющиеся элементы и оставляет только последний).
    """
    trends = []
    try:
        r = requests.get(TRENDS_FEED, headers=HEADERS, timeout=30)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"[warn] тренды не загрузились: {e}", file=sys.stderr)
        return trends

    def local(tag):
        return tag.split("}")[-1]

    for item in root.iter():
        if local(item.tag) != "item":
            continue
        term, traffic, related = "", "", []
        for child in item:
            name = local(child.tag)
            if name == "title":
                term = (child.text or "").strip()
            elif name == "approx_traffic":
                traffic = (child.text or "").strip()
            elif name == "news_item":
                nt = nu = nsrc = ""
                for sub in child:
                    sname = local(sub.tag)
                    if sname == "news_item_title":
                        nt = (sub.text or "").strip()
                    elif sname == "news_item_url":
                        nu = (sub.text or "").strip()
                    elif sname == "news_item_source":
                        nsrc = (sub.text or "").strip()
                if nt or nu:
                    related.append({"title": nt, "url": nu, "source": nsrc})
        if term:
            trends.append({"term": term, "traffic": traffic, "related": related})
        if len(trends) >= MAX_TRENDS:
            break
    return trends

def fetch_youtube_items():
    """Собирает ролики новостных каналов, помечая аналитику отдельно."""
    items = []

    # Обычные каналы: фильтруем по ключевым словам, но не выбрасываем остальное
    for origin, url in YOUTUBE_FEEDS:
        try:
            feed = feedparser.parse(url, agent=HEADERS["User-Agent"])
        except Exception as e:
            print(f"[warn] лента '{origin}' не загрузилась: {e}", file=sys.stderr)
            continue
        count = 0
        for entry in feed.entries:
            if count >= MAX_PER_FEED:
                break
            if not is_fresh(entry):
                continue
            title = getattr(entry, "title", "").strip()
            items.append({
                "origin": origin,
                "title": title,
                "link": getattr(entry, "link", "").strip(),
                "tag": "аналитика/обсуждение" if is_analysis(title) else "обычный сюжет",
            })
            count += 1
        print(f"  {origin}: {count}", file=sys.stderr)

    # โหนกระแส: берём все ролики без фильтра
    for origin, url in YOUTUBE_FEEDS_NO_FILTER:
        try:
            feed = feedparser.parse(url, agent=HEADERS["User-Agent"])
        except Exception as e:
            print(f"[warn] лента '{origin}' не загрузилась: {e}", file=sys.stderr)
            continue
        count = 0
        for entry in feed.entries:
            if count >= MAX_PER_FEED:
                break
            if not is_fresh(entry):
                continue
            items.append({
                "origin": origin,
                "title": getattr(entry, "title", "").strip(),
                "link": getattr(entry, "link", "").strip(),
                "tag": "аналитика/обсуждение",
            })
            count += 1
        print(f"  {origin}: {count}", file=sys.stderr)

    return items

def build_user_message(english, thai, trends):
    en_lines = []
    for i, n in enumerate(english, 1):
        desc = f"\n   суть: {n['desc']}" if n.get("desc") else ""
        en_lines.append(f"{i}. [{n['origin']}] {n['title']}{desc}\n   ссылка: {n['link']}")
    en_block = "\n".join(en_lines) if en_lines else "(нет данных)"

    th_lines = []
    for i, n in enumerate(thai, 1):
        src = f" | {n['source']}" if n["source"] else ""
        th_lines.append(f"{i}. [{n['origin']}] {n['title']}{src}\n   ссылка: {n['link']}")
    th_block = "\n".join(th_lines) if th_lines else "(нет данных)"

    tr_lines = []
    for i, t in enumerate(trends, 1):
        tr = f" (~{t['traffic']})" if t["traffic"] else ""
        head = f"{i}. {t['term']}{tr}"
        if t["related"]:
            for rn in t["related"][:3]:
                src = f" [{rn['source']}]" if rn["source"] else ""
                head += f"\n   связано: {rn['title']}{src} {rn['url']}"
        else:
            head += "\n   связано: (связанных новостей нет)"
        tr_lines.append(head)
    tr_block = "\n".join(tr_lines) if tr_lines else "(нет данных)"

    today = datetime.now(timezone(timedelta(hours=7))).strftime("%d.%m.%Y")
    return (
        f"Дата дайджеста: {today} (утро по Бангкоку).\n\n"
        f"=== АНГЛОЯЗЫЧНАЯ АНАЛИТИКА (основа дайджеста) ===\n{en_block}\n\n"
        f"=== ТАЙСКАЯ ЛЕНТА (контроль охвата) ===\n{th_block}\n\n"
        f"=== ТРЕНДЫ GOOGLE СО СВЯЗАННЫМИ НОВОСТЯМИ ===\n{tr_block}\n\n"
        f"Собери дайджест по правилам из системной инструкции."
    )


SYSTEM_PROMPT = """Ты — редактор утреннего дайджеста о Таиланде и Юго-Восточной Азии для русскоязычного читателя. Пишешь НА РУССКОМ.

Тебе дают три блока: англоязычную аналитику (основа), тайскую ленту Google News (для контроля охвата) и поисковые тренды Google со связанными новостями.

ПРАВИЛА ДАЙДЖЕСТА:

1. Основную часть строй по англоязычной аналитике. У этих новостей есть поле «суть» с фактурой — опирайся на неё, приводи конкретику (имена, цифры, суммы, места), а не общие слова.
2. Группируй в сюжеты. Если одно событие освещают несколько источников, объедини и дай приоритет: ставь такой сюжет выше и раскрывай подробнее, все ссылки перечисли вместе.
3. Раздели на разделы: <b>Политика</b>, <b>Экономика</b>, <b>Международные отношения</b>. Каждый пункт — пересказ СВОИМИ СЛОВАМИ (2–4 предложения), без копирования заголовков. КАЖДЫЙ факт, который ты берёшь из входных данных, должен быть подкреплён ссылкой на источник сразу после него, в формате <a href="URL">издание</a>. Если пункт собран из нескольких новостей, проставь ссылку на каждую из них, а не одну общую в конце. Если для какого-то факта в исходных данных нет ссылки, либо не включай этот факт, либо явно пометь его как не подтверждённый источником.
4. Затем раздел <b>Чего нет в англоязычных СМИ</b>: пройдись по тайской ленте и коротко назови сюжеты, которые есть там, но которых нет в англоязычной части. Это показывает, что внутренняя тайская повестка освещает, а международные издания пропускают. Если таких расхождений нет, честно напиши, что повестки совпали.
5. Раздел <b>Что ищут в Таиланде</b> по трендам. Бери РЕАЛЬНЫЕ запросы как есть, ничего не приглаживая. Поясняй каждый тренд ТОЛЬКО через связанные новости, которые к нему приложены. Если связанных новостей нет или смысл запроса неясен, так и напиши («повод неясен»), НЕ придумывай объяснение. Раскидай тренды по категориям (политика, экономика, спорт, шоу-бизнес, погода, другое). Если общество массово ищет футбол или сериалы, пиши это прямо, без стеснения.

ОФОРМЛЕНИЕ (Telegram HTML):
- Теги: <b>жирный</b> для заголовков разделов, <a href="">ссылки</a>. Другие теги не используй.
- Кавычки — только ёлочки «вот такие».
- Тире не используй как знак паузы или выделения. Ставь только там, где этого требует грамматика.
- Не выдумывай факты, которых нет во входных данных. Пиши живым языком, без канцелярита."""


def generate_digest(english, thai, trends):
    user_msg = build_user_message(english, thai, trends)
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

    print("Собираю англоязычную аналитику...")
    english = fetch_feed_items(ENGLISH_FEEDS, MAX_PER_FEED, with_desc=True)
    print(f"Англоязычных новостей: {len(english)}")

    print("Собираю тайскую ленту для контроля...")
    thai = fetch_feed_items(THAI_FEEDS, MAX_THAI_PER_FEED, with_desc=False)
    print(f"Тайских новостей: {len(thai)}")

    print("Собираю тренды...")
    trends = fetch_trends()
    print(f"Трендов: {len(trends)}")

    if not english and not thai and not trends:
        print("[warn] нет данных, дайджест не отправлен")
        return

    print(f"Пишу дайджест на модели {MODEL}...")
    digest = generate_digest(english, thai, trends)
    digest = f"{format_date_header()}\n\n{digest}"

    if TEST_MODE:
        print("=== ТЕСТОВЫЙ РЕЖИМ, В TELEGRAM НЕ ОТПРАВЛЯЮ ===")
        print(digest)
    else:
        print("Отправляю в Telegram...")
        send_to_telegram(digest)
    print("Готово.")


if __name__ == "__main__":
    main()
