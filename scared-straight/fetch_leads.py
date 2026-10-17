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
# 記事タイトルに県名が出ないことが多いため、市町村名からも推定する
PREF_ALIAS = {
    "北海道": ["北海道", "道警", "札幌", "旭川", "函館", "帯広", "十勝", "釧路", "置戸", "陸別", "石狩", "苫小牧", "北見"],
    "青森県": ["青森", "弘前", "八戸"],
    "岩手県": ["岩手", "盛岡", "一関", "花巻", "奥州"],
    "宮城県": ["宮城", "仙台", "石巻", "大崎市", "名取"],
    "秋田県": ["秋田", "横手", "大館"],
    "山形県": ["山形", "鶴岡", "酒田", "米沢"],
    "福島県": ["福島", "郡山", "いわき", "会津", "白河", "須賀川", "南相馬", "minamisoma"],
    "茨城県": ["茨城", "水戸", "つくば", "日立市", "土浦", "ひたちなか"],
    "栃木県": ["栃木", "宇都宮", "小山市", "足利"],
    "群馬県": ["群馬", "前橋", "高崎", "太田市", "伊勢崎"],
    "埼玉県": ["埼玉", "さいたま", "川越", "川口", "所沢", "越谷", "春日部", "戸田市", "草加"],
    "千葉県": ["千葉", "船橋", "柏市", "松戸", "市川市", "木更津", "成田"],
    "東京都": ["東京", "都内", "警視庁", "多摩", "八王子", "立川市", "町田"],
    "神奈川県": ["神奈川", "横浜", "川崎市", "相模原", "藤沢", "厚木", "平塚", "小田原"],
    "新潟県": ["新潟", "長岡市", "上越市", "三条市"],
    "富山県": ["富山", "高岡"],
    "石川県": ["石川", "金沢", "小松市", "白山市"],
    "福井県": ["福井", "敦賀", "越前市"],
    "山梨県": ["山梨", "甲府", "富士吉田"],
    "長野県": ["長野", "松本市", "上田市", "飯田市", "諏訪"],
    "岐阜県": ["岐阜", "大垣", "多治見"],
    "静岡県": ["静岡", "浜松", "沼津", "富士市", "藤枝", "磐田"],
    "愛知県": ["愛知", "名古屋", "豊田市", "岡崎市", "一宮市", "豊橋", "春日井"],
    "三重県": ["三重", "津市", "四日市", "伊勢", "松阪", "桑名"],
    "滋賀県": ["滋賀", "大津", "びわ湖", "琵琶湖", "彦根", "草津市"],
    "京都府": ["京都", "木津川", "宇治市", "福知山"],
    "大阪府": ["大阪", "堺市", "箕面", "泉大津", "東大阪", "豊中", "吹田", "枚方", "高槻"],
    "兵庫県": ["兵庫", "神戸", "姫路", "西宮", "尼崎", "明石市", "朝来", "加古川"],
    "奈良県": ["奈良", "橿原", "生駒"],
    "和歌山県": ["和歌山", "田辺市", "橋本市"],
    "鳥取県": ["鳥取", "米子", "倉吉"],
    "島根県": ["島根", "松江", "出雲市"],
    "岡山県": ["岡山", "倉敷", "津山", "建部"],
    "広島県": ["広島", "福山市", "呉市", "東広島"],
    "山口県": ["山口", "下関", "宇部市", "周南", "岩国"],
    "徳島県": ["徳島", "鳴門"],
    "香川県": ["香川", "高松市", "丸亀"],
    "愛媛県": ["愛媛", "松山市", "今治", "新居浜"],
    "高知県": ["高知", "四万十", "南国市"],
    "福岡県": ["福岡", "北九州", "春日市", "久留米", "飯塚", "大牟田", "基山"],
    "佐賀県": ["佐賀", "唐津", "鳥栖"],
    "長崎県": ["長崎", "佐世保", "諫早", "大村市"],
    "熊本県": ["熊本", "八代市", "天草"],
    "大分県": ["大分", "別府", "中津市", "日田"],
    "宮崎県": ["宮崎", "都城", "延岡", "日向市"],
    "鹿児島県": ["鹿児島", "霧島市", "鹿屋"],
    "沖縄県": ["沖縄", "那覇", "浦添", "宜野湾"],
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


# 地方局・地方紙の名前は地域の手がかりになる（タイトルに地名が無いとき用）
MEDIA_ALIAS = {
    "KKT": "熊本県", "RKK": "熊本県", "YBC": "山形県", "さくらんぼテレビ": "山形県",
    "MBC": "鹿児島県", "KTS": "鹿児島県", "KKB": "鹿児島県",
    "SBC": "長野県", "NBS": "長野県", "長野放送": "長野県", "信濃毎日": "長野県",
    "びわ湖放送": "滋賀県", "BBC": "滋賀県",
    "サガテレビ": "佐賀県", "佐賀新聞": "佐賀県",
    "カナロコ": "神奈川県", "タウンニュース": "神奈川県", "神奈川新聞": "神奈川県",
    "伊賀タウン情報": "三重県", "伊勢新聞": "三重県",
    "山陰中央新報": "島根県", "山陽新聞": "岡山県", "中国新聞": "広島県",
    "北海道新聞": "北海道", "十勝毎日": "北海道", "HTB": "北海道", "UHB": "北海道",
    "河北新報": "宮城県", "福島民報": "福島県", "福島民友": "福島県",
    "長崎新聞": "長崎県", "西日本新聞": "福岡県", "RKB": "福岡県",
    "静岡新聞": "静岡県", "SBS": "静岡県", "新潟日報": "新潟県",
    "山梨日日": "山梨県", "UTY": "山梨県", "YBS": "山梨県",
    "沖縄タイムス": "沖縄県", "琉球新報": "沖縄県",
    "myfuna": "千葉県", "千葉日報": "千葉県", "上毛新聞": "群馬県",
    "下野新聞": "栃木県", "茨城新聞": "茨城県", "秋田魁": "秋田県",
    "岩手日報": "岩手県", "東奥日報": "青森県", "北日本新聞": "富山県",
    "北國新聞": "石川県", "福井新聞": "福井県", "宮崎日日": "宮崎県",
    "大分合同": "大分県", "愛媛新聞": "愛媛県", "高知新聞": "高知県",
    "徳島新聞": "徳島県", "四国新聞": "香川県", "日本海新聞": "鳥取県",
    "奈良新聞": "奈良県", "紀伊民報": "和歌山県", "山口新聞": "山口県",
}


def pref_of(text):
    """タイトル等から都道府県を推定する（分からなければ空）。"""
    for p in PREFS:
        if p in text or p.rstrip("県都府道") in text:
            return p
    for pref, words in PREF_ALIAS.items():
        if any(w in text for w in words):
            return pref
    for media, pref in MEDIA_ALIAS.items():
        if media in text:
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


DUPE_STRIP = re.compile(r"[【\[（(][^】\]）)]{0,30}[】\]）)]")
DUPE_NOISE = re.compile(r"[\s　・、。！？!?|｜/／\-–—…'\"「」『』“”‘’]")


def dedupe_key(item):
    """同じ出来事を別媒体が報じたものをひとまとめにするためのキー。

    【媒体名】や（掲載日）、先頭の日付を落とし、記号と空白を除いた
    本文の先頭40字と記事日付の組み合わせで同一性を見る。
    """
    t = DUPE_STRIP.sub("", item["title"])
    t = re.sub(r"^\d{1,2}月\d{1,2}日", "", t)
    t = DUPE_NOISE.sub("", t)
    return t[:40] + "|" + (item.get("date") or item.get("first_seen", ""))


def pick_better(a, b):
    """重複した2件のうち残す方を選ぶ。日付あり＞初出が古い＞スコアが高い。"""
    if bool(a.get("date")) != bool(b.get("date")):
        return a if a.get("date") else b
    if a.get("first_seen", "") != b.get("first_seen", ""):
        return a if a.get("first_seen", "") < b.get("first_seen", "") else b
    return a if a.get("score", 0) >= b.get("score", 0) else b


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

    # 保存済みの案件も毎回ラベルを付け直す。
    # 地名の辞書やキーワードを足したとき、過去に拾った分にも反映されるようにする。
    for it in merged.values():
        blob = it["title"] + " " + it.get("org", "") + " " + it.get("source", "")
        it["pref"] = pref_of(blob)
        it["kind"] = kind_of(it["title"] + " " + it.get("org", ""))
        it["score"] = score_of(it["title"] + " " + it.get("org", ""))
        it["status"] = status_of(it)

    # 古すぎるもの・除外語に当たるものを落とす
    # （除外語は後から追加されるため、過去に保存済みの案件もここで掃除する）
    limit = (datetime.date.today() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    items = [it for it in merged.values()
             if it.get("first_seen", now) >= limit
             and not any(k in it["title"] for k in KW_EXCLUDE)]
    # 同じ出来事を報じた重複記事をまとめる
    before = len(items)
    uniq = {}
    for it in items:
        k = dedupe_key(it)
        uniq[k] = pick_better(uniq[k], it) if k in uniq else it
    items = list(uniq.values())
    dupes = before - len(items)

    items.sort(key=lambda x: (x.get("date") or x.get("first_seen", ""), x.get("score", 0)), reverse=True)
    items = items[:MAX_ITEMS]

    payload = {
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "generated_date": now,
        "new_count": sum(1 for it in items if it.get("is_new")),
        "deduped": dupes,
        "due_soon_count": sum(1 for it in items
                              if it.get("status") == "open" and it.get("deadline")
                              and it["deadline"] <= (datetime.date.today() + datetime.timedelta(days=14)).isoformat()),
        "total": len(items),
        "items": items,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    print("完了: 全%d件 / 新規%d件 / 締切間近%d件 / 重複統合%d件 -> %s"
          % (payload["total"], payload["new_count"], payload["due_soon_count"], dupes, OUTPUT_PATH))


if __name__ == "__main__":
    main()
