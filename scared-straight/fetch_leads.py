#!/usr/bin/env python3
"""スケアードストレート案件クローラー

公開情報（Google ニュース RSS + 自治体の入札公告・交通安全ページ）を巡回して
「スケアードストレートの募集・入札・実施」に関わる記事や公告を拾い、leads.json に追記する。

- 一度拾った案件は first_seen（初回検知日）を保持し、当日検知したものを is_new=true にする
- 巡回先は sources.json で管理（URL を足すだけで監視対象を増やせる）
- 個別サイトが落ちていてもスキップして続行する
"""

import json
import os
import re
import sys
import hashlib
import datetime
from html import unescape
from urllib.parse import urljoin, quote

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:  # bs4 が無くても Google ニュースだけは動かす
    BeautifulSoup = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(BASE_DIR, "sources.json")
OUTPUT_PATH = os.path.join(BASE_DIR, "leads.json")

TIMEOUT = 25
UA = "Mozilla/5.0 (compatible; ScaredStraightLeadBot/1.0; +research use)"
MAX_ITEMS = 400
KEEP_DAYS = 400

# 案件度合いのスコアリング用キーワード
KW_CORE = ["スケアード", "スケアード・ストレイト", "スケアードストレート"]
KW_METHOD = ["スタント", "事故再現", "交通事故を再現", "事故を再現"]
KW_DEAL = ["入札", "公告", "業務委託", "委託", "公募", "プロポーザル", "見積", "契約", "募集", "参加者募集", "実施校"]
KW_THEME = ["自転車", "交通安全教室", "交通安全教育", "安全利用教室", "交通安全"]

PREFS = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県", "茨城県", "栃木県", "群馬県",
    "埼玉県", "千葉県", "東京都", "神奈川県", "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県",
    "岐阜県", "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県", "奈良県", "和歌山県",
    "鳥取県", "島根県", "岡山県", "広島県", "山口県", "徳島県", "香川県", "愛媛県", "高知県", "福岡県",
    "佐賀県", "長崎県", "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]
PREF_ALIAS = {
    "北海道": ["北海道", "道警", "札幌", "旭川", "函館", "帯広", "十勝", "釧路", "置戸", "陸別", "石狩"],
    "宮城県": ["宮城", "仙台", "石巻"],
    "東京都": ["東京", "都内", "警視庁", "多摩"],
    "大阪府": ["大阪", "堺市", "箕面", "泉大津"],
    "京都府": ["京都", "木津川"],
    "福岡県": ["福岡", "北九州", "春日市", "久留米"],
}


def today():
    return datetime.date.today().isoformat()


def norm(text):
    return re.sub(r"\s+", " ", unescape(text or "")).strip()


def score_of(text):
    """案件としての有望度を 0-100 で採点する。"""
    s = 0
    if any(k in text for k in KW_CORE):
        s += 50
    if any(k in text for k in KW_METHOD):
        s += 15
    if any(k in text for k in KW_THEME):
        s += 10
    hit_deal = [k for k in KW_DEAL if k in text]
    if hit_deal:
        s += 25 if any(k in text for k in ["入札", "公告", "業務委託", "公募", "プロポーザル"]) else 12
    return min(s, 100)


def kind_of(text):
    if any(k in text for k in ["入札", "公告", "プロポーザル", "業務委託", "公募", "見積合わせ"]):
        return "入札・公募"
    if any(k in text for k in ["募集", "実施校", "参加者"]):
        return "募集"
    if any(k in text for k in ["契約", "落札", "結果"]):
        return "契約・結果"
    return "実施情報"


def pref_of(text):
    """タイトル等から都道府県を推定する（分からなければ空）。"""
    for p in PREFS:
        if p in text or p.rstrip("県都府道") in text:
            return p
    for pref, words in PREF_ALIAS.items():
        if any(w in text for w in words):
            return pref
    return ""


def is_relevant(text):
    """リンクテキストが案件として拾う価値があるか判定する。"""
    if any(k in text for k in KW_CORE + KW_METHOD):
        return True
    return any(k in text for k in KW_THEME) and any(k in text for k in KW_DEAL)


def make_id(url, title):
    return hashlib.sha1((url + "|" + title).encode("utf-8")).hexdigest()[:16]


def fetch(url):
    res = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA})
    res.raise_for_status()
    if not res.encoding or res.encoding.lower() == "iso-8859-1":
        res.encoding = res.apparent_encoding
    return res.text


def parse_rss(xml):
    """外部ライブラリなしで RSS の item を最低限パースする。"""
    items = []
    for block in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def pick(tag):
            m = re.search(r"<%s>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</%s>" % (tag, tag), block, re.S)
            return norm(re.sub(r"<[^>]+>", "", m.group(1))) if m else ""
        title = pick("title")
        link = pick("link")
        pub = pick("pubDate")
        source = pick("source")
        if title and link:
            items.append({"title": title, "url": link, "pub": pub, "source_name": source})
    return items


def rss_date(pub):
    """RFC822 形式の pubDate を YYYY-MM-DD に変換する。"""
    m = re.search(r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})", pub or "")
    if not m:
        return ""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return "%s-%02d-%02d" % (m.group(3), months.index(m.group(2)) + 1, int(m.group(1)))


def collect_google_news(queries):
    out = []
    for q in queries:
        url = ("https://news.google.com/rss/search?q=%s&hl=ja&gl=JP&ceid=JP:ja" % quote(q))
        try:
            xml = fetch(url)
        except Exception as e:
            print("  [skip] Google News '%s': %s" % (q, e), file=sys.stderr)
            continue
        for it in parse_rss(xml):
            title = it["title"]
            if not any(k in title for k in KW_CORE + KW_METHOD):
                continue
            # 「タイトル - 媒体名」形式から媒体名を分離
            org = it["source_name"] or (title.rsplit(" - ", 1)[1] if " - " in title else "")
            out.append({
                "title": title.rsplit(" - ", 1)[0] if " - " in title else title,
                "url": it["url"],
                "org": org,
                "date": rss_date(it["pub"]),
                "source": "Google ニュース",
                "query": q,
            })
        print("  [ok] Google News '%s'" % q)
    return out


def collect_scan_page(entry):
    """自治体ページ内のリンクテキストをキーワードで走査する。"""
    out = []
    try:
        html = fetch(entry["url"])
    except Exception as e:
        print("  [skip] %s: %s" % (entry["org"], e), file=sys.stderr)
        return out

    links = []
    if BeautifulSoup:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            links.append((norm(a.get_text()), a["href"]))
    else:
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
            links.append((norm(re.sub(r"<[^>]+>", "", m.group(2))), m.group(1)))

    for text, href in links:
        if len(text) < 6 or not is_relevant(text):
            continue
        out.append({
            "title": text,
            "url": urljoin(entry["url"], href),
            "org": entry["org"],
            "date": "",
            "source": "%s / %s" % (entry["org"], entry.get("label", "サイト巡回")),
            "query": entry["url"],
        })
    print("  [ok] %s (%d件ヒット)" % (entry["org"], len(out)))
    return out


def load_existing():
    if not os.path.exists(OUTPUT_PATH):
        return {}
    try:
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {it["id"]: it for it in data.get("items", [])}
    except Exception:
        return {}


def main():
    with open(SOURCES_PATH, encoding="utf-8") as f:
        sources = json.load(f)

    print("Google ニュース RSS を巡回します")
    raw = collect_google_news(sources.get("google_news_queries", []))

    print("自治体ページを巡回します")
    for entry in sources.get("scan_pages", []):
        raw += collect_scan_page(entry)

    existing = load_existing()
    now = today()
    merged = dict(existing)
    new_count = 0

    for r in raw:
        rid = make_id(r["url"], r["title"])
        blob = r["title"] + " " + r.get("org", "")
        item = {
            "id": rid,
            "title": r["title"],
            "url": r["url"],
            "org": r.get("org", ""),
            "pref": pref_of(blob),
            "date": r.get("date", ""),
            "source": r.get("source", ""),
            "kind": kind_of(blob),
            "score": score_of(blob),
        }
        if rid in merged:
            item["first_seen"] = merged[rid].get("first_seen", now)
        else:
            item["first_seen"] = now
            new_count += 1
        item["is_new"] = item["first_seen"] == now
        merged[rid] = item

    # 古すぎるものは落とす
    limit = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    items = [it for it in merged.values() if it.get("first_seen", now) >= limit]
    items.sort(key=lambda x: (x.get("date") or x.get("first_seen", ""), x.get("score", 0)), reverse=True)
    items = items[:MAX_ITEMS]

    payload = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "generated_date": now,
        "new_count": sum(1 for it in items if it.get("is_new")),
        "total": len(items),
        "items": items,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print("完了: 全%d件 / 新規%d件 -> %s" % (payload["total"], payload["new_count"], OUTPUT_PATH))


if __name__ == "__main__":
    main()
