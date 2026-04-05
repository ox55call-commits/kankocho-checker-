"""
kankocho.jp クローラー
- ログイン
- 3種類のオークション（公有財産売却・インターネット公売・国税公売）の商品を取得
- お気に入り登録
"""

import asyncio
import re
from playwright.async_api import async_playwright, Page, Browser

BASE_URL = "https://kankocho.jp"

# 実施中を示すキーワード
ACTIVE_KEYWORDS = [
    "参加受付中", "入札受付中", "せり売受付中", "公売中",
    "入札期間", "受付期間", "参加申込受付中",
]


class KankochoCrawler:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.browser: Browser = None
        self.page: Page = None

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        context = await self.browser.new_context(
            locale="ja-JP",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        self.page = await context.new_page()
        return self

    async def __aexit__(self, *args):
        await self.browser.close()
        await self.playwright.stop()

    async def login(self) -> bool:
        print("ログイン中...")

        # トップページを開いてJSが読み込まれるまで待つ
        await self.page.goto(BASE_URL, wait_until="networkidle")
        await asyncio.sleep(4)

        # ログインボタン・リンクを探してクリック
        login_link_selectors = [
            'a:has-text("ログイン")',
            'a:has-text("サインイン")',
            'button:has-text("ログイン")',
            '[href*="login"]',
            '[href*="signin"]',
            '[href*="sign_in"]',
        ]
        clicked = False
        for sel in login_link_selectors:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    await el.click()
                    await self.page.wait_for_load_state("networkidle")
                    await asyncio.sleep(3)
                    clicked = True
                    print(f"  ログインリンクをクリック: {sel}")
                    break
            except Exception:
                continue

        if not clicked:
            # 直接マイページへ移動を試みる
            await self.page.goto(f"{BASE_URL}/mypage", wait_until="networkidle")
            await asyncio.sleep(3)

        # スクリーンショット保存（デバッグ用）
        await self.page.screenshot(path="login_page.png")
        print(f"  現在のURL: {self.page.url}")

        # メールアドレス入力
        email_selectors = [
            'input[type="email"]',
            'input[name="email"]',
            'input[name="mail"]',
            'input[placeholder*="メール"]',
            'input[placeholder*="mail"]',
            'input[placeholder*="Email"]',
        ]
        filled_email = False
        for sel in email_selectors:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    await el.fill(self.email)
                    filled_email = True
                    break
            except Exception:
                continue

        if not filled_email:
            print("  メール入力欄が見つかりません")
            return False

        # パスワード入力
        pass_selectors = [
            'input[type="password"]',
            'input[name="password"]',
        ]
        for sel in pass_selectors:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    await el.fill(self.password)
                    break
            except Exception:
                continue

        # ログインボタン送信
        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("ログイン")',
            'button:has-text("サインイン")',
            'button:has-text("sign in")',
        ]
        for sel in submit_selectors:
            try:
                el = await self.page.query_selector(sel)
                if el:
                    await el.click()
                    break
            except Exception:
                continue

        await self.page.wait_for_load_state("networkidle")
        await asyncio.sleep(4)

        # ログイン後スクリーンショット
        await self.page.screenshot(path="after_login.png")

        # ログイン確認
        current_url = self.page.url
        page_text = await self.page.inner_text("body")
        is_logged_in = (
            "マイページ" in page_text
            or "ログアウト" in page_text
            or "mypage" in current_url.lower()
            or "マイリスト" in page_text
        )
        print(f"ログイン{'成功' if is_logged_in else '失敗'}: {current_url}")
        return is_logged_in

    async def check_schedule(self) -> bool:
        """スケジュールページで実施中のオークションを確認"""
        print("スケジュール確認中: https://kankocho.jp/schedules/")
        await self.page.goto(f"{BASE_URL}/schedules/", wait_until="networkidle")
        await asyncio.sleep(4)

        await self.page.screenshot(path="schedule_page.png")
        page_text = await self.page.inner_text("body")
        print(f"  スケジュールページ文字数: {len(page_text)}")

        # 実施中キーワード確認
        for keyword in ACTIVE_KEYWORDS:
            if keyword in page_text:
                print(f"  → 実施中検出: 「{keyword}」")
                return True

        # 今日の日付が含まれているか確認（出品期間内の可能性）
        from datetime import date
        today = date.today()
        today_str = today.strftime("%Y/%m/%d")
        today_str2 = today.strftime("%Y年%m月%d日")
        if today_str in page_text or today_str2 in page_text:
            print(f"  → 本日（{today_str}）の日程あり")
            return True

        # 商品一覧に商品があれば実施中とみなす
        await self.page.goto(BASE_URL, wait_until="networkidle")
        await asyncio.sleep(3)
        page_text = await self.page.inner_text("body")
        item_count_match = re.search(r"(\d+)\s*件", page_text)
        if item_count_match and int(item_count_match.group(1)) > 0:
            print(f"  → 出品中の商品あり: {item_count_match.group(0)}")
            return True

        print("  → 実施中のオークションなし")
        return False

    async def get_all_items(self) -> list:
        """3種類のオークションから全商品を取得"""
        all_items = []

        # トップページから全カテゴリを取得
        await self.page.goto(BASE_URL, wait_until="networkidle")
        await asyncio.sleep(3)

        # 全商品を検索（キーワードなし）
        items = await self._get_items_from_search("")
        all_items.extend(items)

        print(f"取得した商品数: {len(all_items)}")
        return all_items

    async def _get_items_from_search(self, keyword: str = "") -> list:
        """検索結果から商品を取得（全ページ）"""
        items = []
        page_num = 1

        while True:
            print(f"  ページ {page_num} を取得中...")
            page_items = await self._scrape_search_page(keyword, page_num)
            if not page_items:
                break
            items.extend(page_items)
            page_num += 1

            # 最大50ページまで（5000件）
            if page_num > 50:
                break

            await asyncio.sleep(1)

        return items

    async def _scrape_search_page(self, keyword: str, page_num: int) -> list:
        """1ページ分の商品を取得"""
        # URLパラメータで全商品を取得（ページネーション対応）
        url = f"{BASE_URL}/search?keyword={keyword}&page={page_num}"
        await self.page.goto(url, wait_until="networkidle")
        await asyncio.sleep(2)

        items = []

        # 商品リストのセレクター（サイト構造に合わせて調整）
        item_selectors = [
            ".item-card",
            ".product-item",
            ".auction-item",
            "[class*='item']",
            "[class*='product']",
            "article",
        ]

        item_elements = []
        for sel in item_selectors:
            elements = await self.page.query_selector_all(sel)
            if elements:
                item_elements = elements
                break

        if not item_elements:
            return []

        for el in item_elements:
            try:
                item = await self._extract_item_data(el)
                if item:
                    items.append(item)
            except Exception as e:
                print(f"  商品データ取得エラー: {e}")
                continue

        return items

    async def _extract_item_data(self, element) -> dict:
        """商品要素からデータを抽出"""
        try:
            text = await element.inner_text()
            html = await element.inner_html()

            # リンク（商品URL）
            link_el = await element.query_selector("a")
            href = ""
            if link_el:
                href = await link_el.get_attribute("href") or ""
                if href and not href.startswith("http"):
                    href = BASE_URL + href

            # タイトル
            title = ""
            for sel in ["h2", "h3", "h4", ".title", ".name", "[class*='title']", "[class*='name']"]:
                el = await element.query_selector(sel)
                if el:
                    title = (await el.inner_text()).strip()
                    if title:
                        break

            # 価格（開始価格・見積価格）
            price = 0
            price_patterns = [
                r"(\d[\d,]+)\s*円",
                r"¥\s*(\d[\d,]+)",
            ]
            for pattern in price_patterns:
                match = re.search(pattern, text)
                if match:
                    price = int(match.group(1).replace(",", ""))
                    break

            # 主催機関
            organizer = ""
            for keyword in ["市", "町", "村", "県", "国税", "財務", "区", "省", "庁"]:
                if keyword in text:
                    lines = text.split("\n")
                    for line in lines:
                        if keyword in line and len(line) < 50:
                            organizer = line.strip()
                            break
                    if organizer:
                        break

            if not title or price == 0:
                return None

            # オークション種別の推定
            auction_type = "不明"
            if "公有財産" in text or "市" in text or "町" in text or "村" in text:
                auction_type = "公有財産売却"
            elif "国税" in text or "税務署" in text:
                auction_type = "国税公売"
            elif "公売" in text:
                auction_type = "インターネット公売"

            return {
                "title": title,
                "auction_price": price,
                "url": href,
                "auction_type": auction_type,
                "organizer": organizer,
                "description": text[:500],
                "favorited": False,
            }
        except Exception:
            return None

    async def get_item_detail(self, url: str) -> dict:
        """商品詳細ページから詳細情報を取得"""
        await self.page.goto(url, wait_until="networkidle")
        await asyncio.sleep(2)

        text = await self.page.inner_text("body")

        detail = {"description": text[:1000]}

        # より詳細な説明文を取得
        desc_selectors = [
            ".description",
            ".item-description",
            "[class*='description']",
            "[class*='detail']",
        ]
        for sel in desc_selectors:
            el = await self.page.query_selector(sel)
            if el:
                detail["description"] = (await el.inner_text()).strip()
                break

        return detail

    async def favorite_item(self, url: str) -> bool:
        """商品をお気に入り登録"""
        try:
            await self.page.goto(url, wait_until="networkidle")
            await asyncio.sleep(2)

            # お気に入りボタンのセレクター
            fav_selectors = [
                'button:has-text("お気に入り")',
                'button:has-text("ウィッシュリスト")',
                '[class*="favorite"]',
                '[class*="wishlist"]',
                '[class*="bookmark"]',
                'button[aria-label*="お気に入り"]',
            ]
            for sel in fav_selectors:
                try:
                    btn = await self.page.query_selector(sel)
                    if btn:
                        await btn.click()
                        await asyncio.sleep(1)
                        return True
                except Exception:
                    continue
            return False
        except Exception as e:
            print(f"  お気に入り登録エラー: {e}")
            return False
