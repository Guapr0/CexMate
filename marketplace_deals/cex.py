import json
import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup
from fastapi import HTTPException
from playwright.sync_api import sync_playwright

from marketplace_deals.browser_ui import cex_page_needs_challenge, show_browser_banner
from marketplace_deals.config import normalize_browser_mode, resolve_profile_dir
from marketplace_deals.text_utils import dedupe_items_by_name_price, normalize_text, parse_price


def extract_cex_records_from_next_data(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        return records

    try:
        payload = json.loads(script.string)
    except json.JSONDecodeError:
        return records

    price_keys = ["price", "sellPrice", "boxedPrice", "unboxedPrice", "discountedPrice"]
    name_keys = ["name", "title", "displayName"]
    url_keys = ["url", "link", "productUrl", "slug"]

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            possible_name = next((str(node[k]) for k in name_keys if k in node and node[k]), "")

            possible_price = None
            for key in price_keys:
                if key in node:
                    parsed = parse_price(node.get(key))
                    if parsed is not None:
                        possible_price = parsed
                        break

            possible_url = next((str(node[k]) for k in url_keys if k in node and node[k]), "")
            if possible_name and possible_price is not None:
                if possible_url and possible_url.startswith("/"):
                    possible_url = f"https://uk.webuy.com{possible_url}"
                records.append(
                    {
                        "name": possible_name.strip(),
                        "price": round(possible_price, 2),
                        "url": possible_url,
                        "source": "next_data",
                    }
                )

            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return records


def extract_cex_records_from_html_cards(soup: BeautifulSoup, max_results: int = 100) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen: Set[Tuple[str, float]] = set()

    for anchor in soup.select('a[href*="/product-detail"]'):
        href = (anchor.get("href") or "").strip()
        if not href:
            continue

        full_url = href if href.startswith("http") else f"https://uk.webuy.com{href}"
        name = " ".join(anchor.get_text(" ", strip=True).split())

        container = anchor
        card_text = name
        for _ in range(4):
            parent = container.parent
            if not parent:
                break
            container = parent
            parent_text = " ".join(container.get_text(" ", strip=True).split())
            if parent_text:
                card_text = parent_text
            if "£" in parent_text or "�" in parent_text:
                break

        price_match = re.search(r"[£�]\s?\d[\d,]*(?:\.\d{1,2})?", card_text)
        if not price_match:
            continue

        price_raw = price_match.group(0).replace("�", "£")
        price_value = parse_price(price_raw)
        if price_value is None:
            continue

        if not name:
            name_without_price = re.sub(r"[£�]\s?\d[\d,]*(?:\.\d{1,2})?", "", card_text).strip()
            name = name_without_price if name_without_price else "CeX item"

        key = (normalize_text(name), float(price_value))
        if key in seen:
            continue
        seen.add(key)

        items.append(
            {
                "name": name.strip(),
                "price": round(price_value, 2),
                "url": full_url,
                "source": "playwright_html",
            }
        )
        if len(items) >= max_results:
            break

    return items


def scrape_cex_prices_with_browser(
    search_text: str,
    max_results: int = 100,
    browser_mode: str = "chrome_persistent",
    browser_profile_dir: str = "",
    interactive_browser: bool = False,
    challenge_timeout: int = 0,
) -> List[Dict[str, Any]]:
    mode = normalize_browser_mode(browser_mode)
    if mode == "chromium":
        # Chromium is often blocked by Cloudflare on CeX.
        mode = "chrome"

    search_url = f"https://uk.webuy.com/search/?stext={quote_plus(search_text)}"
    profile_path = resolve_profile_dir(browser_profile_dir) if mode == "chrome_persistent" else None

    with sync_playwright() as p:
        context = None
        browser = None
        try:
            if mode == "chrome_persistent":
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=str(profile_path),
                        channel="chrome",
                        headless=False,
                        viewport={"width": 1366, "height": 900},
                    )
                except Exception as exc:
                    raise HTTPException(
                        409,
                        "Could not open Chrome persistent profile for CeX. Close conflicting Chrome windows "
                        "or choose a different profile directory.",
                    ) from exc
                page = context.pages[0] if context.pages else context.new_page()
            else:
                launch_kwargs: Dict[str, Any] = {"headless": not interactive_browser}
                if mode == "chrome":
                    launch_kwargs["channel"] = "chrome"
                browser = p.chromium.launch(**launch_kwargs)
                page = browser.new_page()

            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            if cex_page_needs_challenge(page):
                if not interactive_browser:
                    raise HTTPException(
                        502,
                        "CeX blocked automated access (Cloudflare challenge). "
                        "Re-run with browser_mode=chrome_persistent and interactive_browser=true.",
                    )

                show_browser_banner(
                    page,
                    "Please complete the CeX security check in this browser. Matching will continue automatically.",
                    "warn",
                )
                has_deadline = challenge_timeout is not None and challenge_timeout > 0
                deadline = time.time() + challenge_timeout if has_deadline else None
                while True:
                    if has_deadline and deadline is not None and time.time() >= deadline:
                        break
                    if not cex_page_needs_challenge(page):
                        break
                    page.wait_for_timeout(1500)
                if cex_page_needs_challenge(page):
                    raise HTTPException(
                        408,
                        "Timed out waiting for CeX security check. Please complete challenge and retry.",
                    )

            page.keyboard.press("End")
            page.wait_for_timeout(1200)

            soup = BeautifulSoup(page.content(), "html.parser")
            items = extract_cex_records_from_html_cards(soup, max_results=max_results)
            return items
        finally:
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if browser:
                    browser.close()
            except Exception:
                pass


def scrape_cex_prices(
    search_text: str,
    max_results: int = 100,
    browser_mode: str = "chrome_persistent",
    browser_profile_dir: str = "",
    interactive_browser: bool = False,
    challenge_timeout: int = 0,
) -> List[Dict[str, Any]]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
    url = f"https://uk.webuy.com/search?stext={quote_plus(search_text)}"

    try:
        response = requests.get(url, headers=headers, timeout=25)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        items: List[Dict[str, Any]] = []

        for anchor in soup.select("a[href]"):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            text = " ".join(anchor.get_text(" ", strip=True).split())
            if not text or len(text) < 5:
                continue

            price_match = re.search(r"£\s?\d[\d,]*(?:\.\d{1,2})?", text)
            if not price_match and anchor.parent:
                parent_text = " ".join(anchor.parent.get_text(" ", strip=True).split())
                price_match = re.search(r"£\s?\d[\d,]*(?:\.\d{1,2})?", parent_text)
            if not price_match:
                continue

            price_value = parse_price(price_match.group(0))
            if price_value is None:
                continue

            name = str(anchor.get("title")) if anchor.get("title") else text
            full_url = href if href.startswith("http") else f"https://uk.webuy.com{href}"
            items.append(
                {
                    "name": name.strip(),
                    "price": round(price_value, 2),
                    "url": full_url,
                    "source": "html",
                }
            )

        items.extend(extract_cex_records_from_next_data(soup))
        deduped = dedupe_items_by_name_price(items, max_results=max_results)
        if deduped:
            return deduped
    except requests.RequestException:
        pass

    return scrape_cex_prices_with_browser(
        search_text=search_text,
        max_results=max_results,
        browser_mode=browser_mode,
        browser_profile_dir=browser_profile_dir,
        interactive_browser=interactive_browser,
        challenge_timeout=challenge_timeout,
    )
