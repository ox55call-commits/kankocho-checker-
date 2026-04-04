#!/usr/bin/env python3
"""
官公庁オークション 転売利益チェッカー
- kankocho.jp から全商品を取得
- 複数サイトで相場を調査
- 利益が出る商品をExcel出力・お気に入り登録
"""

import asyncio
import os
import sys
import traceback
from datetime import datetime

from crawler import KankochoCrawler
from price_checker import get_market_price
from excel_exporter import export_to_excel

# 設定
MIN_PROFIT = 1000          # 最低利益（円）
MIN_PRICE_RATIO = 1.5      # 最低利益倍率（相場が出品価格の1.5倍以上）
KANKOCHO_EMAIL = os.environ["KANKOCHO_EMAIL"]
KANKOCHO_PASSWORD = os.environ["KANKOCHO_PASSWORD"]


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def analyze_profit(item: dict, market: dict) -> dict:
    """利益分析"""
    auction_price = item.get("auction_price", 0)
    estimated_price = market.get("estimated_price", 0)

    if auction_price == 0 or estimated_price == 0:
        return None

    ratio = estimated_price / auction_price
    profit = estimated_price - auction_price

    if ratio < MIN_PRICE_RATIO or profit < MIN_PROFIT:
        return None

    sources = list(market.get("sources", {}).keys())

    return {
        **item,
        "estimated_price": estimated_price,
        "estimated_profit": profit,
        "price_ratio": ratio,
        "price_sources": sources,
        "is_large_item": market.get("is_large_item", False),
        "is_brand": market.get("is_brand", False),
        "brand_name": market.get("brand_name", ""),
    }


async def main():
    log("=== 官公庁オークション 利益チェック開始 ===")

    profitable_items = []

    async with KankochoCrawler(KANKOCHO_EMAIL, KANKOCHO_PASSWORD) as crawler:
        # スケジュール確認
        if not await crawler.has_active_auctions():
            log("=== 実施中のオークションなし、終了します ===")
            return

        # ログイン
        logged_in = await crawler.login()
        if not logged_in:
            log("⚠️ ログイン失敗 - お気に入り登録はスキップされます")

        # 全商品取得
        log("商品一覧を取得中...")
        items = await crawler.get_all_items()
        log(f"取得商品数: {len(items)} 件")

        if not items:
            log("商品が見つかりませんでした")
            return

        # 各商品の相場チェック
        for i, item in enumerate(items):
            title = item.get("title", "")
            auction_price = item.get("auction_price", 0)
            log(f"[{i+1}/{len(items)}] 相場調査中: {title[:40]} (出品価格: {auction_price:,}円)")

            try:
                market = get_market_price(title, item.get("description", ""))

                if market.get("estimated_price", 0) == 0:
                    log(f"  → 相場データなし、スキップ")
                    continue

                result = analyze_profit(item, market)

                if result:
                    log(f"  ✅ 利益商品: 相場 {market['estimated_price']:,}円 / 利益 {result['estimated_profit']:,}円")

                    # お気に入り登録
                    if logged_in and item.get("url"):
                        result["favorited"] = await crawler.favorite_item(item["url"])
                        if result["favorited"]:
                            log(f"  ⭐ お気に入り登録完了")

                    profitable_items.append(result)
                else:
                    ratio = market["estimated_price"] / auction_price if auction_price > 0 else 0
                    log(f"  → 利益不足 (相場倍率: {ratio:.2f}x)")

            except Exception as e:
                log(f"  エラー: {e}")
                traceback.print_exc()

    # Excel出力
    if profitable_items:
        output_file = f"kankocho_profit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        export_to_excel(profitable_items, output_file)
        log(f"=== 完了: {len(profitable_items)} 件の利益商品を {output_file} に出力 ===")
    else:
        log("=== 完了: 利益商品は見つかりませんでした ===")


if __name__ == "__main__":
    asyncio.run(main())
