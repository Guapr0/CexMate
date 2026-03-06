import asyncio
import json
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote_plus, urlencode, urljoin, urlparse, urlunparse

from fastapi import HTTPException
from playwright.sync_api import sync_playwright

from marketplace_deals.browser_ui import cex_page_needs_challenge, show_browser_banner
from marketplace_deals.config import normalize_browser_mode, resolve_profile_dir
from marketplace_deals.text_utils import normalize_text

CURRENCY_PATTERN = re.compile(r"[£$€]\s?\d[\d,]*(?:\.\d{1,2})?")
GB_PATTERN = re.compile(r"(\d+)\s*gb", re.IGNORECASE)
TB_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*tb", re.IGNORECASE)
RAM_PATTERN = re.compile(r"(\d+)\s*gb\s*ram", re.IGNORECASE)
GRADE_PATTERN = re.compile(r",\s*([ABC])\s*$", re.IGNORECASE)


def _safe_float_from_currency(raw_value: str) -> Optional[float]:
    match = CURRENCY_PATTERN.search(raw_value or "")
    if not match:
        return None
    text = match.group(0)
    cleaned = text.replace("£", "").replace("$", "").replace("€", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_trade_in_cash_price(trade_block_text: str) -> Optional[float]:
    text = " ".join(str(trade_block_text or "").split())
    if not text:
        return None

    # Prefer price adjacent to "Trade in for Cash" specifically.
    cash_price_before_label = re.search(
        r"([£$€]\s?\d[\d,]*(?:\.\d{1,2})?)\s*trade\s*in\s*for\s*cash",
        text,
        re.IGNORECASE,
    )
    if cash_price_before_label:
        return _safe_float_from_currency(cash_price_before_label.group(1))

    cash_price_after_label = re.search(
        r"trade\s*in\s*for\s*cash[^£$€]*([£$€]\s?\d[\d,]*(?:\.\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if cash_price_after_label:
        return _safe_float_from_currency(cash_price_after_label.group(1))

    # Fallback: when both voucher+cash prices exist, cash is usually the last currency value.
    if re.search(r"trade\s*in\s*for\s*cash", text, re.IGNORECASE):
        prices = re.findall(r"[£$€]\s?\d[\d,]*(?:\.\d{1,2})?", text)
        if prices:
            return _safe_float_from_currency(prices[-1])

    return _safe_float_from_currency(text)


def _human_pause(min_seconds: float, max_seconds: float) -> None:
    low = max(0.0, float(min_seconds))
    high = max(low, float(max_seconds))
    time.sleep(random.uniform(low, high))


def _load_group_titles(filtered_json_path: str) -> List[str]:
    path = Path(filtered_json_path).resolve()
    if not path.exists():
        raise HTTPException(502, f"Filtered file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(502, f"Invalid filtered JSON: {exc}") from exc

    if not isinstance(payload, list):
        raise HTTPException(502, "Filtered JSON root must be an array.")

    group_titles: List[str] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        group_title = str(row.get("group_title", "")).strip()
        if group_title:
            group_titles.append(group_title)
    return group_titles


def _parse_group_title_constraints(group_title: str) -> Dict[str, Any]:
    ram_gb: Optional[int] = None
    ram_match = RAM_PATTERN.search(group_title)
    if ram_match:
        ram_gb = int(ram_match.group(1))

    storage_gb: Optional[int] = None
    tb_values_gb: List[int] = []
    for match in TB_PATTERN.finditer(group_title):
        try:
            tb_raw = float(match.group(1))
        except ValueError:
            continue
        if tb_raw <= 0:
            continue
        tb_values_gb.append(int(round(tb_raw * 1024)))

    if tb_values_gb:
        storage_gb = tb_values_gb[0]
    else:
        gb_values = [int(match.group(1)) for match in GB_PATTERN.finditer(group_title)]
        if gb_values:
            if ram_gb is None:
                storage_gb = gb_values[0]
            else:
                for value in gb_values:
                    if value != ram_gb:
                        storage_gb = value
                        break
                if storage_gb is None and gb_values:
                    storage_gb = gb_values[0]

    grade = ""
    grade_match = GRADE_PATTERN.search(group_title)
    if grade_match:
        grade = grade_match.group(1).upper()

    cleaned_title = group_title
    cleaned_title = re.sub(r",\s*[ABC]\s*$", " ", cleaned_title, flags=re.IGNORECASE)
    cleaned_title = re.sub(r"\b\d+\s*gb\s*ram\b", " ", cleaned_title, flags=re.IGNORECASE)
    cleaned_title = re.sub(r"\b\d+\s*gb\b", " ", cleaned_title, flags=re.IGNORECASE)
    cleaned_title = re.sub(r"\b\d+(?:\.\d+)?\s*tb\b", " ", cleaned_title, flags=re.IGNORECASE)

    normalized = normalize_text(cleaned_title)
    tokens = [token for token in normalized.split() if token]
    filtered_tokens: List[str] = []
    for token in tokens:
        if token in {"gb", "tb", "ram", "grade", "cash", "trade", "in", "for"}:
            continue
        if token in {"a", "b", "c"}:
            continue
        if token.isdigit():
            continue
        if len(token) <= 1:
            continue
        filtered_tokens.append(token)

    return {
        "required_tokens": sorted(set(filtered_tokens)),
        "storage_gb": storage_gb,
        "ram_gb": ram_gb,
        "grade": grade,
    }


def _normalize_cex_link(raw_href: str) -> str:
    href = str(raw_href or "").strip()
    if not href:
        return ""

    absolute = href if href.startswith("http") else urljoin("https://uk.webuy.com", href)
    parsed = urlparse(absolute)
    if not parsed.scheme or not parsed.netloc:
        return ""

    path = parsed.path or "/"
    path_no_trailing = path.rstrip("/") or "/"
    query = parsed.query or ""

    # Canonicalize product-detail links to the stable id-based URL.
    if path_no_trailing == "/product-detail":
        product_id = parse_qs(query).get("id", [None])[0]
        if product_id:
            canonical_query = urlencode({"id": product_id})
            return urlunparse(("https", "uk.webuy.com", "/product-detail", "", canonical_query, ""))

    return urlunparse(("https", parsed.netloc, path, "", query, ""))


def _collect_cex_cards(page: Any, limit: int) -> List[Dict[str, Any]]:
    payload = page.evaluate(
        """
        (limit) => {
          const cards = Array.from(document.querySelectorAll('.search-product-card'));
          const out = [];
          for (const card of cards) {
            const titleAnchor = card.querySelector('.card-title a[title], .card-title a[href]');
            const anchor = titleAnchor || card.querySelector('a[href*="/product-detail"], a[href]');
            if (!anchor) continue;
            const hrefRaw = anchor.getAttribute('href') || '';
            const href = hrefRaw.trim();
            if (!href) continue;

            const cardTitleNode = card.querySelector('.card-title');
            const title = (
              (titleAnchor && (titleAnchor.getAttribute('title') || titleAnchor.textContent)) ||
              (cardTitleNode && cardTitleNode.textContent) ||
              anchor.getAttribute('title') ||
              anchor.textContent ||
              ''
            ).trim();

            const tradeNode = card.querySelector('.tradeInPrices');
            const tradeText = ((tradeNode && (tradeNode.innerText || tradeNode.textContent)) || '').trim();

            out.push({ href, title, tradeText });
            if (out.length >= limit) break;
          }
          return out;
        }
        """,
        max(10, limit),
    )

    cards: List[Dict[str, Any]] = []
    for row in payload or []:
        href = str(row.get("href") or "").strip()
        if not href:
            continue
        cex_link = _normalize_cex_link(href)
        if not cex_link:
            continue
        title = " ".join(str(row.get("title") or "").split())
        trade_text = " ".join(str(row.get("tradeText") or "").split())
        market_price = _extract_trade_in_cash_price(trade_text)
        cards.append(
            {
                "title": title,
                "cex_link": cex_link,
                "market_price": market_price,
            }
        )
    return cards


def _wait_for_cex_results_render(
    page: Any,
    timeout_seconds: float = 30.0,
    poll_ms: int = 450,
) -> bool:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        try:
            card_count = int(page.locator(".search-product-card").count())
        except Exception:
            card_count = 0
        if card_count > 0:
            return True

        try:
            # Early exit when explicit empty-state text is already rendered.
            empty_count = int(page.locator("text=/no results|0 results|no products/i").count())
        except Exception:
            empty_count = 0
        if empty_count > 0:
            return False

        page.wait_for_timeout(max(120, int(poll_ms)))
    return False


def _highlight_cex_card(page: Any, cex_link: str, accepted: bool) -> None:
    if not cex_link:
        return
    color = "#22c55e" if accepted else "#38bdf8"
    background = "rgba(34, 197, 94, .18)" if accepted else "rgba(56, 189, 248, .18)"
    try:
        page.evaluate(
            """
            ({ cexLink, color, background }) => {
              const previous = document.querySelector('[data-codex-cex-active="1"]');
              if (previous) {
                previous.style.outline = '';
                previous.style.background = '';
                previous.removeAttribute('data-codex-cex-active');
              }

              const cards = Array.from(document.querySelectorAll('.search-product-card'));
              const target = cards.find((card) => {
                const anchor = card.querySelector('.card-title a[href], a[href*="/product-detail"], a[href]');
                if (!anchor) return false;
                const hrefRaw = anchor.getAttribute('href') || '';
                if (!hrefRaw) return false;
                let abs = '';
                try {
                  const url = new URL(hrefRaw, 'https://uk.webuy.com');
                  const productId = url.searchParams.get('id');
                  if (productId) {
                    abs = `https://uk.webuy.com/product-detail?id=${encodeURIComponent(productId)}`;
                  } else {
                    abs = `${url.origin}${url.pathname}${url.search}`;
                  }
                } catch (e) {
                  return false;
                }
                return abs === cexLink;
              });
              if (!target) return;

              target.setAttribute('data-codex-cex-active', '1');
              target.style.transition = 'all .2s ease';
              target.style.outline = `3px solid ${color}`;
              target.style.background = background;
            }
            """,
            {"cexLink": cex_link, "color": color, "background": background},
        )
    except Exception:
        pass


def _passes_filters(candidate_title: str, constraints: Dict[str, Any]) -> Tuple[bool, float]:
    title = str(candidate_title or "")
    title_lower = title.lower()
    title_norm = normalize_text(title)
    title_tokens = set(title_norm.split()) if title_norm else set()

    required_tokens: List[str] = constraints.get("required_tokens", [])
    token_ratio = 1.0
    if required_tokens:
        hits = sum(1 for token in required_tokens if token in title_tokens)
        token_ratio = hits / len(required_tokens)
        if token_ratio < 0.4:
            return False, round(token_ratio, 3)

    storage_gb = constraints.get("storage_gb")
    if storage_gb is not None:
        storage_value = int(storage_gb)
        storage_match = re.search(rf"\b{storage_value}\s*gb\b", title_lower) is not None
        if not storage_match and storage_value >= 1024:
            tb_value = storage_value / 1024
            if float(tb_value).is_integer():
                tb_text = str(int(tb_value))
            else:
                tb_text = f"{tb_value:.3f}".rstrip("0").rstrip(".")
            storage_match = re.search(rf"\b{re.escape(tb_text)}\s*tb\b", title_lower) is not None
        if not storage_match:
            return False, round(token_ratio, 3)

    ram_gb = constraints.get("ram_gb")
    if ram_gb is not None and re.search(rf"\b{int(ram_gb)}\s*gb\b", title_lower) is None:
        return False, round(token_ratio, 3)

    return True, round(token_ratio, 3)


def _scan_cex_by_group_titles_impl(
    filtered_json_path: str,
    browser_mode: str = "chrome_persistent",
    browser_profile_dir: str = "",
    interactive_browser: bool = False,
    challenge_timeout: int = 0,
    max_scroll_rounds: int = 12,
) -> Dict[str, Any]:
    group_titles = _load_group_titles(filtered_json_path)
    if not group_titles:
        return {
            "results": [],
            "group_summaries": [],
            "groups_scanned": 0,
            "groups_matched": 0,
            "items_checked": 0,
        }

    mode = normalize_browser_mode(browser_mode)
    if mode == "chromium":
        mode = "chrome"
    profile_path = resolve_profile_dir(browser_profile_dir) if mode == "chrome_persistent" else None

    results: List[Dict[str, Any]] = []
    group_summaries: List[Dict[str, Any]] = []
    groups_matched = 0
    items_checked = 0

    playwright_mgr = sync_playwright()
    p = playwright_mgr.start()
    context = None
    browser = None
    page = None
    try:
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
                page = context.new_page()
            else:
                launch_kwargs: Dict[str, Any] = {"headless": not interactive_browser}
                if mode == "chrome":
                    launch_kwargs["channel"] = "chrome"
                browser = p.chromium.launch(**launch_kwargs)
                page = browser.new_page()

            for index, group_title in enumerate(group_titles, start=1):
                if index > 1:
                    _human_pause(1.0, 2.0)
                constraints = _parse_group_title_constraints(group_title)
                search_url = f"https://uk.webuy.com/search?stext={quote_plus(group_title)}"
                page.goto(search_url, wait_until="domcontentloaded")
                page.wait_for_timeout(random.randint(1000, 1800))

                if cex_page_needs_challenge(page):
                    if not interactive_browser:
                        raise HTTPException(
                            502,
                            "CeX blocked automated access (Cloudflare challenge). "
                            "Re-run with browser_mode=chrome_persistent and interactive_browser=true.",
                        )
                    show_browser_banner(
                        page,
                        "Please complete the CeX security check in this browser. Scanning will continue automatically.",
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
                    # Challenge solved; wait for results to hydrate.
                    _wait_for_cex_results_render(page, timeout_seconds=35.0, poll_ms=500)
                else:
                    _wait_for_cex_results_render(page, timeout_seconds=35.0, poll_ms=500)

                if interactive_browser:
                    show_browser_banner(page, f"Scanning CeX group {index}/{len(group_titles)}", "info")

                seen_links: set[str] = set()
                matched_row: Optional[Dict[str, Any]] = None
                checked_for_group = 0
                idle_scroll_rounds = 0

                try:
                    page.evaluate("() => window.scrollTo({ top: 0, behavior: 'instant' })")
                except Exception:
                    pass
                page.wait_for_timeout(random.randint(250, 520))

                max_idle_scroll_rounds = max(1, int(max_scroll_rounds))
                # Process products in-order for this group until a passing match is found.
                # Only then move to the next group title.
                while idle_scroll_rounds < max_idle_scroll_rounds and matched_row is None:
                    cards = _collect_cex_cards(page, 120)
                    pending_cards = []
                    for card in cards:
                        cex_link = str(card.get("cex_link", "")).strip()
                        if not cex_link or cex_link in seen_links:
                            continue
                        pending_cards.append(card)

                    if not pending_cards:
                        idle_scroll_rounds += 1
                        try:
                            page.mouse.wheel(0, random.randint(750, 1200))
                        except Exception:
                            try:
                                page.keyboard.press("PageDown")
                            except Exception:
                                pass
                        page.wait_for_timeout(random.randint(900, 1700))
                        continue

                    idle_scroll_rounds = 0
                    for card in pending_cards:
                        cex_link = str(card.get("cex_link", "")).strip()
                        title = str(card.get("title", "")).strip()
                        seen_links.add(cex_link)

                        checked_for_group += 1
                        items_checked += 1

                        passed, _ = _passes_filters(title, constraints)
                        _highlight_cex_card(page, cex_link, accepted=passed)

                        if passed:
                            matched_row = {
                                "Group Title": group_title,
                                "market_price": card.get("market_price"),
                                "cex_link": cex_link,
                            }
                            break

                        _human_pause(0.22, 0.55)

                    if matched_row is None:
                        # Give CeX list renderer time to append additional cards before next poll.
                        page.wait_for_timeout(random.randint(420, 900))

                if matched_row is None:
                    matched_row = {
                        "Group Title": group_title,
                        "market_price": None,
                        "cex_link": "NOT_FOUND",
                    }
                    found = False
                else:
                    found = True
                    groups_matched += 1

                results.append(matched_row)
                group_summaries.append(
                    {
                        "group_title": group_title,
                        "checked_items": checked_for_group,
                        "found": found,
                        "search_url": search_url,
                    }
                )

            if interactive_browser:
                show_browser_banner(page, "CeX group scanning complete.", "ok")
                page.wait_for_timeout(1000)

            return {
                "results": results,
                "group_summaries": group_summaries,
                "groups_scanned": len(group_titles),
                "groups_matched": groups_matched,
                "items_checked": items_checked,
            }
        finally:
            try:
                if page:
                    page.close()
            except Exception:
                pass
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
    finally:
        try:
            playwright_mgr.stop()
        except asyncio.InvalidStateError:
            # Rare Playwright shutdown race on Windows worker threads.
            pass
        except Exception:
            pass


def scan_cex_by_group_titles(
    filtered_json_path: str,
    browser_mode: str = "chrome_persistent",
    browser_profile_dir: str = "",
    interactive_browser: bool = False,
    challenge_timeout: int = 0,
    max_scroll_rounds: int = 12,
) -> Dict[str, Any]:
    try:
        asyncio.get_running_loop()
        has_running_loop = True
    except RuntimeError:
        has_running_loop = False

    if has_running_loop:
        # Playwright sync API cannot be started inside an active asyncio loop.
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _scan_cex_by_group_titles_impl,
                filtered_json_path,
                browser_mode,
                browser_profile_dir,
                interactive_browser,
                challenge_timeout,
                max_scroll_rounds,
            )
            return future.result()

    return _scan_cex_by_group_titles_impl(
        filtered_json_path=filtered_json_path,
        browser_mode=browser_mode,
        browser_profile_dir=browser_profile_dir,
        interactive_browser=interactive_browser,
        challenge_timeout=challenge_timeout,
        max_scroll_rounds=max_scroll_rounds,
    )
