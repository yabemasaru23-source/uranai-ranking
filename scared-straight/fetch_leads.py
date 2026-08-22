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
MAX_DETAIL_FETCH = 40  # 1回の実行で締切を読みに行く詳細ページ数の上限

# 案件度合いのスコアリング用キーワード
KW_CORE = ["スケアード", "スケアード・ストレイト", "スケアードストレート"]
KW_METHOD = ["スタント", "事故再現", "交通事故を再現", "事故を再現"]
KW_DEAL = ["入札", "公告", "業務委託", "委託", "公募", "プロポーザル", "見積", "契約", "募集", "参加者募集", "実施校"]
KW_DEAL_STRONG = ["入札", "公告", "業務委託", "公募", "プロポーザル", "受託", "選定"]
KW_THEME = ["自転車", "交通安全教室", "交通安全教育", "安全利用教室", "交通安全"]
# 教育・啓発と直接結びつくテーマ語（駐輪場や保険などの周辺行政を除くために使う）
KW_THEME_EDU = ["交通安全教室", "交通安全教育", "安全利用教室", "自転車教室", "安全教室", "交通安全講習"]
# 「もう終わっている」ことを示す語
KW_CLOSED = [
    "選定結果", "審査結果", "結果について", "結果の公表", "結果を公表", "落札結果", "入札結果",
    "受付終了", "募集は終了", "終了しました", "締め切りました", "締切ました",
    "決定しました", "選定しました", "中止", "実施しました", "開催しました",
]

# 締切を読み取るための手がかり語
KW_DEADLINE_CUE = [
    "提出期限", "提出締切", "受付期限", "受付締切", "申込期限", "申込締切",
    "応募期限", "応募締切", "参加申込", "参加表明", "質問期限", "入札書",
    "企画提案書", "提案書の提出", "申請書の提出", "参加申請",
    "締切", "締め切り", "期限", "必着", "まで",
]

# 交通安全担当課の所管でも、案件として無関係なもの
KW_EXCLUDE = [
    "駐車場", "駐輪", "放置自転車", "指定管理", "レンタサイクル", "シェアサイクル",
    "自転車保険", "撤去", "返還", "作文コンクール", "標語", "ポスターコンクール",
]

# 周辺案件（同じ交通安全担当課が出す、スケアードストレート以外の発注）
KW_ADJACENT = [
    "動画", "映像", "ビデオ", "DVD", "教材", "冊子", "リーフレット", "パンフレット",
    "啓発物", "啓発品", "反射材", "シミュレータ", "シミュレーター", "VR", "ＶＲ",
    "講師派遣", "指導員", "教室運営", "キャンペーン", "啓発", "教本", "ヘルメット",
]

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
    if s == 0 and is_adjacent(text):
        s += 30  # 周辺案件（動画・教材・シミュレータ等）
    hit_deal = [k for k in KW_DEAL if k in text]
    if hit_deal:
        s += 25 if any(k in text for k in ["入札", "公告", "業務委託", "公募", "プロポーザル"]) else 12
    return min(s, 100)


def kind_of(text):
    core = any(k in text for k in KW_CORE + KW_METHOD)
    if not core and is_adjacent(text):
        return "周辺案件"
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


def is_adjacent(text):
    """周辺案件（動画制作・教材・シミュレータ等）の発注情報か判定する。

    交通安全テーマ × 周辺メニュー × 調達語 の3つが揃ったときだけ拾う。
    ニュース記事で溢れないよう、調達語は強いもの（入札・公告・業務委託等）に限定する。
    """
    if any(k in text for k in KW_EXCLUDE):
        return False
    return (any(k in text for k in KW_THEME)
            and any(k in text for k in KW_ADJACENT)
            and any(k in text for k in KW_DEAL_STRONG))


def is_relevant(text):
    """リンクテキストが案件として拾う価値があるか判定する。"""
    if any(k in text for k in KW_EXCLUDE):
        return False
    if any(k in text for k in KW_CORE + KW_METHOD):
        return True
    if is_adjacent(text):
        return True
    # 一般枠は「教育・啓発」と直接結びつくテーマ語に限る（駐輪場・保険などを弾く）
    return any(k in text for k in KW_THEME_EDU) and any(k in text for k in KW_DEAL)


# 「令和8年4月15日」「令和8（2026）年4月15日」「2026（令和8）年4月15日」いずれにも対応する
_PAREN = r"(?:[（(][^）)]{0,12}[）)])?"
DATE_RE = re.compile(
    r"令和\s*(?P<r>\d{1,2})\s*" + _PAREN + r"\s*年\s*(?P<rm>\d{1,2})\s*月\s*(?P<rd>\d{1,2})\s*日"
    r"|(?P<y>20\d{2})\s*" + _PAREN + r"\s*年\s*(?P<ym>\d{1,2})\s*月\s*(?P<yd>\d{1,2})\s*日"
    r"|(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"
)


def _to_date(m):
    """正規表現マッチを date に変換する。年が無い場合は直近の日付として推定する。"""
    try:
        if m.group("r"):
            return datetime.date(2018 + int(m.group("r")), int(m.group("rm")), int(m.group("rd")))
        if m.group("y"):
            return datetime.date(int(m.group("y")), int(m.group("ym")), int(m.group("yd")))
        # 「4月16日」のように年が無い表記は、年を推測すると
        # 過ぎた締切を未来のものに変えてしまうため採用しない
        return None
    except ValueError:
        return None


def extract_deadline(html):
    """本文から申込・提出の締切日を推定する。

    手がかり語の直後に現れる日付だけを候補にし、
    未来の日付があればその最も近いもの、無ければ最も遅い過去日を返す。
    """
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", unescape(text))
    today_d = datetime.date.today()
    future, past = [], []
    for cue in KW_DEADLINE_CUE:
        for hit in re.finditer(re.escape(cue), text):
            seg = text[hit.end(): hit.end() + 60]
            m = DATE_RE.search(seg)
            if not m:
                continue
            d = _to_date(m)
            if not d:
                continue
            (future if d >= today_d else past).append(d)
    if future:
        return min(future).isoformat()
    if past:
        return max(past).isoformat()
    return ""


FY_RE = re.compile(r"令和\s*(\d{1,2})\s*(?:[（(][^）)]{0,12}[）)])?\s*年度")


def current_fiscal_year():
    """今日の日本の年度（令和X）を返す。4月始まり。"""
    d = datetime.date.today()
    year = d.year if d.month >= 4 else d.year - 1
    return year - 2018


def latest_fiscal_year(html):
    """本文中で最も新しい「令和X年度」を返す（見つからなければ 0）。"""
    text = re.sub(r"<[^>]+>", " ", html)
    years = [int(m.group(1)) for m in FY_RE.finditer(text)]
    return max(years) if years else 0


def status_of(item):
    """案件の状態を open / closed / expired で返す。"""
    if any(k in item["title"] for k in KW_CLOSED):
        return "closed"
    if item.get("deadline") and item["deadline"] < today():
        return "expired"
    # 本文が過去年度しか触れていない案件は、募集が終わっているとみなす
    fy = item.get("fy")
    if fy and fy < current_fiscal_year():
        return "expired"
    return "open"


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
            if any(k in title for k in KW_EXCLUDE):
                continue
            if not any(k in title for k in KW_CORE + KW_METHOD) and not is_adjacent(title):
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
            prev = merged[rid]
            item["first_seen"] = prev.get("first_seen", now)
            for k in ("deadline", "fy", "checked"):
                if prev.get(k):
                    item[k] = prev[k]
        else:
            item["first_seen"] = now
            new_count += 1
        item["is_new"] = item["first_seen"] == now
        merged[rid] = item

    # 案件性のあるものだけ、詳細ページを読んで締切日を拾う
    print("締切日を確認します")
    checked = 0
    for it in merged.values():
        if checked >= MAX_DETAIL_FETCH:
            break
        if it.get("checked") or it.get("kind") not in ("入札・公募", "募集", "周辺案件"):
            continue
        if any(k in it["title"] for k in KW_CLOSED):
            continue
        if "news.google.com" in it["url"]:      # ニュースの中継URLは本文が取れない
            continue
        try:
            html = fetch(it["url"])
        except Exception as e:
            print("  [skip] %s: %s" % (it["title"][:24], e), file=sys.stderr)
            checked += 1
            continue
        checked += 1
        it["checked"] = now
        d = extract_deadline(html)
        fy = latest_fiscal_year(html)
        if fy:
            it["fy"] = fy
        if d:
            it["deadline"] = d
        if d or fy:
            print("  [ok] %s → 締切%s / 令和%s年度"
                  % (it["title"][:26], d or "不明", fy or "?"))

    for it in merged.values():
        it["status"] = status_of(it)

    # 古すぎるもの・除外語に当たるものを落とす
    # （除外語は後から追加されるため、過去に保存済みの案件もここで掃除する）
    limit = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    items = [it for it in merged.values()
             if it.get("first_seen", now) >= limit
             and not any(k in it["title"] for k in KW_EXCLUDE)]
    items.sort(key=lambda x: (x.get("date") or x.get("first_seen", ""), x.get("score", 0)), reverse=True)
    items = items[:MAX_ITEMS]

    payload = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "generated_date": now,
        "new_count": sum(1 for it in items if it.get("is_new")),
        "due_soon_count": sum(1 for it in items
                              if it.get("status") == "open" and it.get("deadline")
                              and it["deadline"] <= (datetime.date.today() + datetime.timedelta(days=14)).isoformat()),
        "total": len(items),
        "items": items,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print("完了: 全%d件 / 新規%d件 / 締切間近%d件 -> %s"
          % (payload["total"], payload["new_count"], payload["due_soon_count"], OUTPUT_PATH))


if __name__ == "__main__":
    main()
