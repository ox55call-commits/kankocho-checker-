"""
複数サイトから相場価格を取得する
- aucfan.com（ヤフオク・メルカリ等の落札価格履歴）
- Yahoo!オークション（現在の出品価格）
- Amazon Japan（小売・中古価格）
- ウリドキ（買取業者の買取価格：車・大型品向け）
"""

import re
import time
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9",
}


def _get(url, timeout=15):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "lxml")
    except Exception:
        return None


def search_aucfan(keyword: str) -> dict:
    """aucfan.com で落札価格履歴を取得（メルカリ・ヤフオク等を集約）"""
    url = f"https://aucfan.com/search1/?q={requests.utils.quote(keyword)}&ob=n"
    soup = _get(url)
    if not soup:
        return {}

    prices = []
    # 落札価格のリストを取得
    for item in soup.select(".s-resultList .s-item, .resultList .itemList li"):
        price_el = item.select_one(".s-price, .price, .aucPrice")
        if price_el:
            price_text = re.sub(r"[^\d]", "", price_el.get_text())
            if price_text:
                prices.append(int(price_text))

    if not prices:
        return {}

    prices.sort()
    return {
        "source": "aucfan",
        "avg": int(sum(prices) / len(prices)),
        "median": prices[len(prices) // 2],
        "max": max(prices),
        "min": min(prices),
        "count": len(prices),
    }


def search_yahoo_auctions(keyword: str) -> dict:
    """Yahoo!オークションの落札済み価格を取得（出品中ではなく実際に売れた価格）"""
    # f=0x4 で落札済み商品のみ、s1=end&o1=d で終了日降順
    url = (
        f"https://auctions.yahoo.co.jp/search/search"
        f"?p={requests.utils.quote(keyword)}&va={requests.utils.quote(keyword)}"
        f"&n=20&s1=end&o1=d&f=0x4&istatus=2"
    )
    soup = _get(url)
    if not soup:
        return {}

    prices = []
    for item in soup.select(".Product, .ac-result-list li, [class*='product']"):
        price_el = item.select_one(".Product__priceValue, .price, [class*='price']")
        if price_el:
            price_text = re.sub(r"[^\d]", "", price_el.get_text())
            if price_text and len(price_text) >= 3:
                prices.append(int(price_text))

    if not prices:
        return {}

    prices = sorted(p for p in prices if p >= 100)  # 100円未満は除外
    if not prices:
        return {}

    return {
        "source": "yahoo_落札価格",
        "avg": int(sum(prices) / len(prices)),
        "median": prices[len(prices) // 2],
        "max": max(prices),
        "min": min(prices),
        "count": len(prices),
    }


def search_amazon(keyword: str) -> dict:
    """Amazon Japanで中古・新品価格を取得"""
    url = f"https://www.amazon.co.jp/s?k={requests.utils.quote(keyword)}"
    soup = _get(url)
    if not soup:
        return {}

    prices = []
    for item in soup.select(".s-result-item[data-component-type='s-search-result']"):
        price_el = item.select_one(".a-price-whole")
        if price_el:
            price_text = re.sub(r"[^\d]", "", price_el.get_text())
            if price_text:
                prices.append(int(price_text))

    if not prices:
        return {}

    prices.sort()
    return {
        "source": "amazon",
        "avg": int(sum(prices) / len(prices)),
        "median": prices[len(prices) // 2],
        "min": min(prices),
        "count": len(prices),
    }


def search_uridoki(keyword: str) -> dict:
    """ウリドキで買取業者の買取価格を取得（車・大型品向け）"""
    url = f"https://uridoki.jp/search/?q={requests.utils.quote(keyword)}"
    soup = _get(url)
    if not soup:
        return {}

    prices = []
    for item in soup.select(".shop-item, .result-item, .buy-price"):
        price_el = item.select_one(".price, .buy-price-value")
        if price_el:
            price_text = re.sub(r"[^\d]", "", price_el.get_text())
            if price_text:
                prices.append(int(price_text))

    if not prices:
        return {}

    return {
        "source": "uridoki",
        "avg": int(sum(prices) / len(prices)),
        "max": max(prices),
        "count": len(prices),
    }


# 大型・フリマ不向き品のキーワード
LARGE_ITEM_KEYWORDS = [
    "自動車", "車", "バイク", "オートバイ", "トラック", "ピアノ", "グランドピアノ",
    "バイク", "重機", "フォークリフト", "建設機械", "船", "ボート",
]

# ブランド品キーワード（真贋確認が必要）
BRAND_KEYWORDS = [
    "ルイヴィトン", "louis vuitton", "グッチ", "gucci", "エルメス", "hermes",
    "シャネル", "chanel", "プラダ", "prada", "ロレックス", "rolex",
    "オメガ", "omega", "カルティエ", "cartier", "ティファニー", "tiffany",
    "バーバリー", "burberry", "コーチ", "coach", "フェンディ", "fendi",
    "ヴェルサーチ", "versace", "ディオール", "dior", "バレンシアガ", "balenciaga",
    "ゴヤール", "goyard", "セリーヌ", "celine", "ボッテガ", "bottega",
]


def is_large_item(title: str, description: str) -> bool:
    text = (title + " " + description).lower()
    return any(k in text for k in LARGE_ITEM_KEYWORDS)


def is_brand_item(title: str, description: str) -> tuple[bool, str]:
    """ブランド品かどうかと検出されたブランド名を返す"""
    text = (title + " " + description).lower()
    for brand in BRAND_KEYWORDS:
        if brand.lower() in text:
            return True, brand
    return False, ""


def get_market_price(title: str, description: str = "") -> dict:
    """複数ソースから相場価格を取得して最も信頼性の高い価格を返す"""
    # キーワードを抽出（タイトルの最初の部分を使用）
    keyword = title[:30].strip()

    result = {
        "keyword": keyword,
        "sources": {},
        "estimated_price": 0,
        "is_large_item": is_large_item(title, description),
        "is_brand": False,
        "brand_name": "",
    }

    brand_detected, brand_name = is_brand_item(title, description)
    result["is_brand"] = brand_detected
    result["brand_name"] = brand_name

    # 大型品は買取価格を優先
    if result["is_large_item"]:
        uridoki = search_uridoki(keyword)
        if uridoki:
            result["sources"]["uridoki"] = uridoki
        time.sleep(1)

    # aucfan（最も信頼性高い：実際の落札価格）
    aucfan = search_aucfan(keyword)
    if aucfan:
        result["sources"]["aucfan"] = aucfan
    time.sleep(1)

    # Yahoo!オークション
    yahoo = search_yahoo_auctions(keyword)
    if yahoo:
        result["sources"]["yahoo"] = yahoo
    time.sleep(1)

    # Amazon
    amazon = search_amazon(keyword)
    if amazon:
        result["sources"]["amazon"] = amazon
    time.sleep(1)

    # 最終的な相場価格を算出（優先度: aucfan > yahoo > amazon > uridoki）
    prices = []
    for source_name in ["aucfan", "yahoo", "uridoki", "amazon"]:
        src = result["sources"].get(source_name)
        if src and src.get("median"):
            prices.append(src["median"])
        elif src and src.get("avg"):
            prices.append(src["avg"])

    if prices:
        result["estimated_price"] = int(sum(prices) / len(prices))

    return result
