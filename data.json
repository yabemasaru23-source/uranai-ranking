#!/usr/bin/env python3
"""
占いデータ自動取得スクリプト
- 6媒体から12星座のランキングを取得
- 失敗したサイトは前日データを保持
- data.json として保存
"""

import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

JST = timezone(timedelta(hours=9))
NOW_JST = datetime.now(JST)
TODAY_STR = NOW_JST.strftime('%Y-%m-%d')

# 星座キーのマッピング（日本語 → 英語キー）
ZODIAC_MAP = {
    'おひつじ': 'aries', '牡羊': 'aries', 'おひつじ座': 'aries', '牡羊座': 'aries',
    'おうし': 'taurus', '牡牛': 'taurus', 'おうし座': 'taurus', '牡牛座': 'taurus',
    'ふたご': 'gemini', '双子': 'gemini', 'ふたご座': 'gemini', '双子座': 'gemini',
    'かに': 'cancer', '蟹': 'cancer', 'かに座': 'cancer', '蟹座': 'cancer',
    'しし': 'leo', '獅子': 'leo', 'しし座': 'leo', '獅子座': 'leo',
    'おとめ': 'virgo', '乙女': 'virgo', 'おとめ座': 'virgo', '乙女座': 'virgo',
    'てんびん': 'libra', '天秤': 'libra', 'てんびん座': 'libra', '天秤座': 'libra',
    'さそり': 'scorpio', '蠍': 'scorpio', 'さそり座': 'scorpio', '蠍座': 'scorpio',
    'いて': 'sagittarius', '射手': 'sagittarius', 'いて座': 'sagittarius', '射手座': 'sagittarius',
    'やぎ': 'capricorn', '山羊': 'capricorn', 'やぎ座': 'capricorn', '山羊座': 'capricorn',
    'みずがめ': 'aquarius', '水瓶': 'aquarius', 'みずがめ座': 'aquarius', '水瓶座': 'aquarius',
    'うお': 'pisces', '魚': 'pisces', 'うお座': 'pisces', '魚座': 'pisces',
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36'
}


def extract_zodiac_key(text):
    """テキストから星座キーを抽出"""
    for jp, en in ZODIAC_MAP.items():
        if jp in text:
            return en
    return None


def fetch_mezamashi():
    """めざまし占い（フジテレビ）"""
    try:
        url = 'https://fujitvforsugotoku.jp/fortune.php'
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'lxml')
        text = soup.get_text()
        # 「1位 ふたご座」のようなパターンを探す
        ranks = {}
        for i in range(1, 13):
            pattern = rf'{i}\s*位[^\d]{{0,30}}'
            matches = re.findall(pattern, text)
            for m in matches:
                key = extract_zodiac_key(m)
                if key and key not in ranks.values():
                    ranks[i] = key
                    break
        if len(ranks) >= 10:
            return {z: r for r, z in ranks.items()}
    except Exception as e:
        print(f'めざまし占い 取得失敗: {e}', file=sys.stderr)
    return None


def fetch_mynavi():
    """マイナビニュース 12星座占いランキング"""
    try:
        # 今日の日付でURL生成
        date_part = NOW_JST.strftime('%Y%m%d')
        # 検索でランキングページを探す
        search_url = f'https://news.mynavi.jp/search/?query=12星座占いランキング&date={date_part}'
        r = requests.get(search_url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        # 最新の占いランキング記事URLを取得
        soup = BeautifulSoup(r.text, 'lxml')
        article_link = soup.find('a', href=re.compile(r'/article/\d+-'))
        if not article_link:
            return None
        article_url = 'https://news.mynavi.jp' + article_link['href']
        r2 = requests.get(article_url, headers=HEADERS, timeout=15)
        r2.raise_for_status()
        text = BeautifulSoup(r2.text, 'lxml').get_text()
        ranks = {}
        for i in range(1, 13):
            pattern = rf'{i}\s*位\s*[:\s]*([^\d\n]{{1,15}}座)'
            m = re.search(pattern, text)
            if m:
                key = extract_zodiac_key(m.group(1))
                if key:
                    ranks[key] = i
        if len(ranks) >= 10:
            return ranks
    except Exception as e:
        print(f'マイナビ 取得失敗: {e}', file=sys.stderr)
    return None


def fetch_modelpress():
    """モデルプレス 12星座占い・運勢ランキング"""
    try:
        # トップから今日のランキング記事を探す
        url = 'https://mdpr.jp/other/list/41/'
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'lxml')
        article_link = soup.find('a', href=re.compile(r'/other/detail/\d+'))
        if not article_link:
            return None
        article_url = 'https://mdpr.jp' + article_link['href']
        r2 = requests.get(article_url, headers=HEADERS, timeout=15)
        r2.raise_for_status()
        text = BeautifulSoup(r2.text, 'lxml').get_text()
        ranks = {}
        for i in range(1, 13):
            pattern = rf'{i}\s*位[^\d]{{1,30}}'
            m = re.search(pattern, text)
            if m:
                key = extract_zodiac_key(m.group(0))
                if key and key not in ranks:
                    ranks[key] = i
        if len(ranks) >= 10:
            return ranks
    except Exception as e:
        print(f'モデルプレス 取得失敗: {e}', file=sys.stderr)
    return None


def load_previous_data():
    """前日データを読み込み（取得失敗時のフォールバック）"""
    path = Path('data.json')
    if path.exists():
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None


def main():
    print(f'=== 占いデータ取得開始: {NOW_JST.isoformat()} ===')

    previous = load_previous_data()

    sources_config = {
        'めざまし占い': {
            'label': 'フジテレビ｜公式',
            'fetcher': fetch_mezamashi,
        },
        'マイナビニュース': {
            'label': '12星座占いランキング',
            'fetcher': fetch_mynavi,
        },
        'モデルプレス': {
            'label': '12星座占い・運勢ランキング',
            'fetcher': fetch_modelpress,
        },
        # 取得困難な以下3媒体は前日データ or 初期値を保持
        'TOKYO FM+ 桜羽結万': {
            'label': '池袋占い館セレーネ',
            'fetcher': lambda: None,
        },
        '福井新聞 フェリーチェ': {
            'label': '西洋占星術',
            'fetcher': lambda: None,
        },
        'anna media': {
            'label': '読売テレビ｜毎日更新',
            'fetcher': lambda: None,
        },
    }

    sources_data = {}
    fetch_log = []

    for name, cfg in sources_config.items():
        ranks = cfg['fetcher']()
        if ranks:
            sources_data[name] = {
                'label': cfg['label'],
                'ranks': ranks,
                'updated': TODAY_STR,
                'status': 'success',
            }
            fetch_log.append(f'✅ {name}: 取得成功')
        elif previous and 'sources' in previous and name in previous['sources']:
            # 前日データを保持
            prev = previous['sources'][name]
            sources_data[name] = {
                'label': cfg['label'],
                'ranks': prev['ranks'],
                'updated': prev.get('updated', '不明'),
                'status': 'fallback',
            }
            fetch_log.append(f'⚠️ {name}: 取得失敗（前回データ使用: {prev.get("updated", "?")}）')
        else:
            fetch_log.append(f'❌ {name}: 取得失敗（前回データもなし）')

    # 全媒体失敗 → 前日データ全体を返す
    if not any(s['status'] == 'success' for s in sources_data.values()):
        if previous:
            print('⚠️ 全媒体取得失敗、前日データを保持')
            output = previous
            output['lastAttempt'] = NOW_JST.isoformat()
            output['lastAttemptStatus'] = 'all_failed'
        else:
            print('❌ 初回実行で全取得失敗、致命的エラー')
            sys.exit(1)
    else:
        output = {
            'date': TODAY_STR,
            'updated': NOW_JST.isoformat(),
            'sources': sources_data,
            'lastAttempt': NOW_JST.isoformat(),
            'lastAttemptStatus': 'success',
            'fetchLog': fetch_log,
        }

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print('=== 取得結果サマリ ===')
    for line in fetch_log:
        print(line)
    print(f'=== data.json 保存完了 ===')


if __name__ == '__main__':
    main()
