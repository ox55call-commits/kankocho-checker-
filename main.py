#!/usr/bin/env python3
"""
官公庁オークション 転売利益チェッカー
- スケジュールページで実施期間を確認
- 新規出品のみ分析・通知
- 利益商品をChatworkに通知・お気に入り登録
"""

import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

import requests

from crawler import KankochoCrawler
from price_checker import get_market_price
from excel_exporter import export_to_excel

# 設定
MIN_PROFIT = 1000
MIN_PRICE_RATIO = 1.5
KANKOCHO_EMAIL = os.environ["KANKOCHO_EMAIL"]
KANKOCHO_PASSWORD = os.environ["KANKOCHO_PASSWORD"]
CHATWORK_TOKEN = os.environ.get("CHATWORK_TOKEN", "")
CHATWORK_ROOM_ID = os.environ.get("CHATWORK_OWNER_ROOM_ID", "")

STATE_FILE = Path("kankocho_state.json")


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"seen_items": []}


def save_state(state: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_chatwork(message: str):
    if not CHATWORK_TOKEN or not CHATWORK_ROOM_ID:
        log("Chatwork設定なし、スキップ")
        return
    try:
        resp = requests.post(
            f"https://api.chatwork.com/v2/rooms/{CHATWORK_ROOM_ID.strip()}/messages",
            headers={"X-ChatWorkToken": CHATWORK_TOKEN.strip()},
            data={"body": message},
            timeout=15,
        )
        resp.raise_for_status()
    except Exception as e:
        log(f"Chatwork通知エラー: {e}")


def build_profit_message(items: list) -> str:
    lines = [
        "[info][title]【官公庁オークション】利益商品が見つかりました[/title]",
        f"新規利益商品: {len(items)} 件\n",
    ]
    for item in items[:10]:  # 最大10件
        lines.append(
            f"■ {item['title'][:40]}\n"
            f"  出品価格: {item['auction_price']:,}円 → 相場: {item['estimated_price']:,}円\n"
            f"  推定利益: {item['estimated_profit']:,}円（{item['price_ratio']:.1f}倍）\n"
            f"  {item.get('url', '')}\n"
        )
    if len(items) > 10:
        lines.append(f"  ...他 {len(items)-10} 件（Excelファイル参照）")
    lines.append("[/info]")
    return "\n".join(lines)


def analyze_profit(item: dict, market: dict) -> dict:
    auction_price = item.get("auction_price", 0)
    estimated_price = market.get("estimated_price", 0)

    if auction_price == 0 or estimated_price == 0:
        return None

    ratio = estimated_price / auction_price
    profit = estimated_price - auction_price

    if ratio < MIN_PRICE_RATIO or profit < MIN_PROFIT:
        return None

    return {
        **item,
        "estimated_price": estimated_price,
        "estimated_profit": profit,
        "price_ratio": ratio,
        "price_sources": list(market.get("sources", {}).keys()),
        "is_large_item": market.get("is_large_item", False),
        "is_brand": market.get("is_brand", False),
        "brand_name": market.get("brand_name", ""),
    }


async def main():
    log("=== 官公庁オークション チェック開始 ===")

    state = load_state()
    seen_items = set(state.get("seen_items", []))

    profitable_items = []

    async with KankochoCrawler(KANKOCHO_EMAIL, KANKOCHO_PASSWORD) as crawler:
        # スケジュール確認
        is_active = await crawler.check_schedule()
        if not is_active:
            log("=== 実施中のオークションなし、終了 ===")
            return

        # ログイン
        logged_in = await crawler.login()
        if not logged_in:
            log("⚠️ ログイン失敗 - お気に入り登録はスキップ")

        # 全商品取得
        log("商品一覧を取得中...")
        items = await crawler.get_all_items()
        log(f"取得商品数: {len(items)} 件")

        if not items:
            log("商品が見つかりませんでした")
            return

        # 新規商品のみ処理
        new_items = [i for i in items if i.get("url") not in seen_items]
        log(f"新規商品: {len(new_items)} 件（既存: {len(items) - len(new_items)} 件スキップ）")

        for i, item in enumerate(new_items):
            title = item.get("title", "")
            auction_price = item.get("auction_price", 0)
            log(f"[{i+1}/{len(new_items)}] {title[:40]} ({auction_price:,}円)")

            # 状態に追加
            if item.get("url"):
                seen_items.add(item["url"])

            try:
                market = get_market_price(title, item.get("description", ""))

                if market.get("estimated_price", 0) == 0:
                    log(f"  → 相場データなし")
                    continue

                result = analyze_profit(item, market)
                if result:
                    log(f"  ✅ 利益商品: 相場 {market['estimated_price']:,}円 / 利益 {result['estimated_profit']:,}円")

                    if logged_in and item.get("url"):
                        result["favorited"] = await crawler.favorite_item(item["url"])

                    profitable_items.append(result)
                else:
                    ratio = market["estimated_price"] / auction_price if auction_price > 0 else 0
                    log(f"  → 利益不足 ({ratio:.2f}倍)")

            except Exception as e:
                log(f"  エラー: {e}")
                traceback.print_exc()

    # 状態保存
    save_state({"seen_items": list(seen_items)})

    # Excel出力 & Chatwork通知
    if profitable_items:
        output_file = f"kankocho_profit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        export_to_excel(profitable_items, output_file)
        log(f"Excel出力: {output_file} ({len(profitable_items)} 件)")

        send_chatwork(build_profit_message(profitable_items))
        log("Chatwork通知送信完了")
    else:
        log("新規の利益商品なし")

    log("=== チェック完了 ===")


if __name__ == "__main__":
    asyncio.run(main())
