#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
อัปเดต data/news.json จาก RSS ของสำนักข่าวไทย (ไม่ต้องพึ่งเซิร์ฟเวอร์)
ใช้กับ GitHub Actions (ดู .github/workflows/news.yml) หรือรันเองก็ได้:

    python3 tools/fetch_news.py
"""
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "news.json")
UA = "Mozilla/5.0 (compatible; FamAiBot/1.0; +https://github.com)"

FEEDS = [
    {"id": "khaosod",     "name": "ข่าวสด",          "url": "https://www.khaosod.co.th/feed"},
    {"id": "matichon",    "name": "มติชน",           "url": "https://www.matichon.co.th/feed"},
    {"id": "prachachat",  "name": "ประชาชาติธุรกิจ", "url": "https://www.prachachat.net/feed"},
    {"id": "thaipublica", "name": "ไทยพับลิก้า",     "url": "https://thaipublica.org/feed/"},
    {"id": "blognone",    "name": "Blognone",        "url": "https://www.blognone.com/node/feed"},
    {"id": "thaipost",    "name": "ไทยโพสต์",        "url": "https://www.thaipost.net/feed/"},
    {"id": "ejan",        "name": "อีจัน",           "url": "https://www.ejan.co/feed"},
    {"id": "techsauce",   "name": "Techsauce",       "url": "https://www.techsauce.co/feed"},
    {"id": "google",      "name": "Google News",     "url": "https://news.google.com/rss?hl=th&gl=TH&ceid=TH:th"},
]

TOPIC_RULES = [
    ("กีฬา", ["กีฬา", "ฟุตบอล", "บอล", "มวย", "โอลิมปิก", "วอลเลย์", "บาส", "เทนนิส", "ufc", "เนชันส์"]),
    ("เศรษฐกิจ", ["เศรษฐกิจ", "หุ้น", "ธุรกิจ", "การเงิน", "ตลาด", "ลงทุน", "เงินบาท", "ภาษี", "ธนาคาร", "ส่งออก", "ค้าปลีก", "อสังหา", "พลังงาน", "น้ำมัน"]),
    ("เทคโนโลยี", ["เทคโนโลยี", "ไอที", "ai", "เอไอ", "มือถือ", "เกม", "คอมพิว", "ซอฟต์แวร์", "สตาร์ทอัพ", "ดิจิทัล", "หุ่นยนต์", "อวกาศ", "tech"]),
    ("การเมือง", ["การเมือง", "รัฐบาล", "เลือกตั้ง", "สภาฯ", "นายก", "ครม", "พรรค", "รัฐสภา", "รัฐมนตรี"]),
    ("ต่างประเทศ", ["ต่างประเทศ", "ต่างชาติ", "โลก", "อาเซียน", "สหรัฐ", "จีน", "ยูโรป", "ตะวันออกกลาง", "รัสเซีย", "ยูเครน", "อิสราเอล"]),
    ("บันเทิง", ["บันเทิง", "ดารา", "ซีรีส์", "เพลง", "ภาพยนตร์", "ละคร", "คอนเสิร์ต", "ศิลปิน", "netflix"]),
    ("สุขภาพ", ["สุขภาพ", "แพทย์", "อนามัย", "วัคซีน", "โรงพยาบาล", "อาหารเสริม"]),
    ("ท่องเที่ยว", ["ท่องเที่ยว", "เที่ยว", "โรงแรม", "สายการบิน", "อุทยาน"]),
    ("ยานยนต์", ["ยานยนต์", "รถยนต์", "รถไฟฟ้า", "ev", "มอเตอร์ไซค์", "รถ"]),
    ("อาชญากรรม", ["อาชญากรรม", "ตำรวจ", "จับกุม", "คดี", "ฆ่า", "ยาเสพติด", "อุบัติเหตุ", "ไฟไหม้", "โจร"]),
]

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")


def local(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def strip_html(text):
    if not text:
        return ""
    text = re.sub(r"(?is)<(script|style|iframe).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\[\s*(?:…|\.\.\.|&#8230;)\s*\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"(อ่านข่าวต้นฉบับ|อ่านต่อ|ที่มา\s*:).*$", "", text).strip()


def topic_of(categories, title):
    hay = (" ".join(categories) + " " + (title or "")).lower()
    for topic, keys in TOPIC_RULES:
        if any(k in hay for k in keys):
            return topic
    return "ข่าวทั่วไป"


def parse_date(raw):
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def image_of(el, html_blobs):
    best = ""
    for node in el.iter():
        name = local(node.tag).lower()
        url = node.attrib.get("url", "")
        if not url:
            continue
        typ = node.attrib.get("type", "")
        if name in ("content", "thumbnail", "enclosure") and (
                typ.startswith("image") or url.lower().split("?")[0].endswith(IMG_EXT)):
            if url.startswith("//"):
                url = "https:" + url
            if url.startswith("http"):
                best = url
                break
    if not best:
        for blob in html_blobs:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', blob or "", re.I)
            if m:
                url = m.group(1).strip()
                if url.startswith("//"):
                    url = "https:" + url
                if url.startswith("http"):
                    best = url
                    break
    return best


def fetch_feed(feed):
    try:
        req = urllib.request.Request(feed["url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            root = ET.fromstring(resp.read())
    except Exception as exc:  # noqa: BLE001
        print("  ! %-16s %s" % (feed["name"], exc))
        return []

    items = []
    for el in root.iter():
        if local(el.tag) != "item":
            continue
        title = link = desc_html = content_html = date_raw = ""
        cats = []
        for ch in el:
            name = local(ch.tag).lower()
            text = (ch.text or "").strip()
            if name == "title":
                title = strip_html(text)
            elif name == "link":
                link = text
            elif name == "description":
                desc_html = text
            elif name == "encoded":
                content_html = text
            elif name == "category" and text:
                cats.append(strip_html(text))
            elif name in ("pubdate", "date"):
                date_raw = text
        if not title or not link:
            continue

        source, final_title = feed["name"], title
        if feed["id"] == "google":
            m = re.search(r"\s+-\s+([^-]{3,40})$", title)
            if m:
                source, final_title = m.group(1).strip(), title[:m.start()].strip()

        dt = parse_date(date_raw)
        items.append({
            "id": "n" + re.sub(r"\W", "", link)[-12:],
            "title": final_title,
            "desc": strip_html(desc_html)[:240],
            "topic": topic_of(cats, final_title),
            "source": source,
            "link": link,
            "image": image_of(el, (desc_html, content_html)),
            "published": dt.timestamp(),
        })
    print("  ✓ %-16s %d ข่าว" % (feed["name"], len(items)))
    return items


def main():
    print("กำลังดึงข่าว…")
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as pool:
        chunks = list(pool.map(fetch_feed, FEEDS))
    items = [it for chunk in chunks for it in chunk]
    items.sort(key=lambda x: x["published"], reverse=True)

    seen_t, seen_l, out = set(), set(), []
    for it in items:
        key = re.sub(r"[\s\W_]+", "", it["title"])[:60]
        if key in seen_t or it["link"] in seen_l:
            continue
        seen_t.add(key)
        seen_l.add(it["link"])
        out.append(it)
        if len(out) >= 60:
            break

    payload = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "count": len(out),
        "items": out,
    }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print("เขียน %s (%d ข่าว)" % (os.path.relpath(OUT, ROOT), len(out)))
    return 0 if out else 1


if __name__ == "__main__":
    sys.exit(main())
