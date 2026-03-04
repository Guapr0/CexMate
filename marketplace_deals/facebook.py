import asyncio
import os
import random
import time
from collections import deque
from typing import Any, Deque, Dict, List, Set, Tuple

from fastapi import HTTPException
from playwright.sync_api import sync_playwright

from marketplace_deals.browser_ui import (
    extract_marketplace_item_id,
    highlight_marketplace_item,
    page_needs_login,
    show_browser_banner,
    wait_for_manual_login,
)
from marketplace_deals.config import normalize_browser_mode, resolve_profile_dir
from marketplace_deals.text_utils import (
    build_marketplace_url,
    normalize_condition_filters,
    normalize_date_listed,
    normalize_radius_km,
    parse_best_price,
    parse_facebook_card_text,
)


def human_pause(min_seconds: float, max_seconds: float) -> None:
    low = max(0.0, min_seconds)
    high = max(low, max_seconds)
    time.sleep(random.uniform(low, high))


def collect_marketplace_cards(page: Any, limit: int) -> List[Dict[str, Any]]:
    return page.evaluate(
        """
        (limit) => {
          const anchors = Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]'));
          const out = [];
          const seen = new Set();

          for (const anchor of anchors) {
            const hrefRaw = anchor.getAttribute('href') || '';
            const href = hrefRaw.split('?')[0];
            if (!href || seen.has(href)) continue;
            seen.add(href);

            const container = anchor.parentElement || anchor;
            const text = (container.innerText || anchor.innerText || '').trim();
            const imageEl = container.querySelector('img') || anchor.querySelector('img');
            const image = imageEl ? (imageEl.src || imageEl.getAttribute('src') || '') : '';
            const rect = container.getBoundingClientRect();
            const y = Math.max(0, (window.scrollY || 0) + rect.top);
            const x = Math.max(0, rect.left);

            out.push({ href: hrefRaw, text, image, y, x });
            if (out.length >= limit) break;
          }

          out.sort((a, b) => {
            if (a.y !== b.y) return a.y - b.y;
            return a.x - b.x;
          });

          return out;
        }
        """,
        limit,
    )


def wait_for_more_cards(page: Any, previous_count: int, timeout_seconds: float) -> bool:
    deadline = time.time() + max(0.5, timeout_seconds)
    while time.time() < deadline:
        try:
            current_count = int(
                page.evaluate("() => document.querySelectorAll('a[href*=\"/marketplace/item/\"]').length")
            )
        except Exception:
            current_count = previous_count
        if current_count > previous_count:
            return True
        human_pause(0.45, 0.95)
    return False


def wait_for_initial_cards(page: Any, timeout_seconds: float) -> bool:
    return wait_for_more_cards(page, previous_count=0, timeout_seconds=timeout_seconds)


def enqueue_new_cards(
    cards: List[Dict[str, Any]],
    pending_cards: Deque[Dict[str, Any]],
    pending_links: Set[str],
    inspected_links: Set[str],
) -> int:
    added = 0
    for card in cards:
        href = (card.get("href") or "").strip()
        if not href:
            continue
        full_link = href if href.startswith("http") else f"https://www.facebook.com{href}"
        if full_link in inspected_links or full_link in pending_links:
            continue
        enriched = dict(card)
        enriched["full_link"] = full_link
        pending_cards.append(enriched)
        pending_links.add(full_link)
        added += 1
    return added


def looks_like_price_only_text(value: str) -> bool:
    text = (value or "").strip()
    if not text:
        return False
    if parse_best_price(text, require_currency=True) is None:
        return False
    return not any(ch.isalpha() for ch in text)


def extract_listing_details(page: Any, listing_url: str, timeout_ms: int = 20000) -> Dict[str, str]:
    detail_page = None
    try:
        detail_page = page.context.new_page()
        detail_page.goto(listing_url, wait_until="domcontentloaded", timeout=timeout_ms)
        detail_page.wait_for_timeout(random.randint(1000, 1900))

        for _ in range(6):
            clicked_count = detail_page.evaluate(
                """
                () => {
                  let clicked = 0;
                  const clickable = Array.from(
                    document.querySelectorAll('div[role="button"], span[role="button"], button, a')
                  );
                  for (const node of clickable) {
                    const txt = (node.textContent || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    if (txt === 'see more' || txt.endsWith(' see more')) {
                      try {
                        node.click();
                        clicked += 1;
                      } catch (e) {}
                    }
                  }
                  return clicked;
                }
                """
            )
            detail_page.wait_for_timeout(random.randint(250, 520))
            if not clicked_count:
                break

        payload = detail_page.evaluate(
            """
            () => {
              const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
              const isPriceOnly = (value) => {
                const text = clean(value);
                if (!text || !/[£$€]/.test(text)) return false;
                const match = text.match(/[£$€]\\s?\\d[\\d,]*(?:\\.\\d{1,2})?/);
                if (!match) return false;
                const residual = text
                  .replace(/[£$€]\\s?\\d[\\d,]*(?:\\.\\d{1,2})?/g, '')
                  .replace(/[\\s.,:/()\\-]/g, '');
                return residual.length === 0;
              };
              const extractPriceFromText = (value) => {
                const text = clean(value);
                if (!text) return '';
                const match = text.match(/[£$€]\\s?\\d[\\d,]*(?:\\.\\d{1,2})?/);
                return match ? clean(match[0]) : '';
              };
              const extractRecencyFromText = (value) => {
                const text = clean(value);
                if (!text) return '';
                const match = text.match(
                  /(just now|today|yesterday|a\\s+minute\\s+ago|an\\s+hour\\s+ago|a\\s+day\\s+ago|a\\s+week\\s+ago|a\\s+month\\s+ago|a\\s+year\\s+ago|\\d+\\s*(?:min|mins|minute|minutes|hr|hrs|hour|hours|day|days|week|weeks|month|months|year|years)\\s+ago)/i
                );
                return match ? clean(match[1]) : '';
              };
              const bodyText = document.body ? (document.body.innerText || '') : '';
              const lines = bodyText.split('\\n').map((line) => clean(line)).filter(Boolean);

              let title = '';
              const titleCandidates = [
                clean(document.querySelector('h1') ? document.querySelector('h1').textContent : ''),
                clean(document.querySelector('meta[property="og:title"]') ? document.querySelector('meta[property="og:title"]').getAttribute('content') : ''),
                clean(document.title || ''),
              ];
              for (const candidate of titleCandidates) {
                if (!candidate) continue;
                const normalized = candidate
                  .replace(/\\s*\\|\\s*Facebook\\s*$/i, '')
                  .replace(/\\s*-\\s*Marketplace\\s*$/i, '')
                  .trim();
                if (!normalized) continue;
                if (isPriceOnly(normalized)) continue;
                title = clean(normalized);
                break;
              }

              let price = '';
              const priceNodes = Array.from(document.querySelectorAll('div,span,p,strong,h2,h3'));
              for (const node of priceNodes) {
                const value = clean(node.textContent || '');
                if (!value || value.length > 45) continue;
                const extracted = extractPriceFromText(value);
                if (!extracted) continue;
                price = extracted;
                break;
              }
              if (!price) {
                const ogDescription = clean(
                  document.querySelector('meta[property="og:description"]')
                    ? document.querySelector('meta[property="og:description"]').getAttribute('content')
                    : ''
                );
                price = extractPriceFromText(ogDescription);
              }
              if (!price) {
                price = extractPriceFromText(bodyText);
              }

              let recency = '';
              let recencyRaw = '';
              for (const line of lines) {
                if (!/^listed\\s+/i.test(line)) continue;
                recencyRaw = line;
                recency = extractRecencyFromText(line);
                break;
              }

              if (!recency) {
                recency = extractRecencyFromText(bodyText);
              }

              let description = '';
              const detailsIdx = lines.findIndex((line) => /^details$/i.test(line));
              if (detailsIdx >= 0) {
                const stopPatterns = [
                  /^seller information$/i,
                  /^about this seller$/i,
                  /^send seller a message$/i,
                  /^message seller$/i,
                  /^similar listings$/i,
                  /^you may also like$/i,
                  /^location is approximate$/i,
                  /^report$/i,
                  /^safety tips$/i,
                ];
                const picked = [];
                for (let i = detailsIdx + 1; i < lines.length; i++) {
                  const line = lines[i];
                  if (stopPatterns.some((pattern) => pattern.test(line))) break;
                  if (/^see (more|less)$/i.test(line)) continue;
                  if (/^listed\\s+/i.test(line)) continue;
                  picked.push(line);
                  if (picked.length >= 48) break;
                }
                description = picked.join(' | ');
              }

              if (!description) {
                const og = document.querySelector('meta[property="og:description"]');
                if (og) description = clean(og.getAttribute('content'));
              }

              if (!title || isPriceOnly(title)) {
                title = '';
              }

              return { title, price, description, recency, recency_raw: recencyRaw };
            }
            """
        )

        if not isinstance(payload, dict):
            return {}
        title = " ".join(str(payload.get("title", "")).split())
        price = " ".join(str(payload.get("price", "")).split())
        description = " ".join(str(payload.get("description", "")).split())
        recency = " ".join(str(payload.get("recency", "")).split())
        recency_raw = " ".join(str(payload.get("recency_raw", "")).split())
        if len(description) > 600:
            description = description[:597] + "..."
        return {
            "title": title,
            "price": price,
            "description": description,
            "recency": recency,
            "recency_raw": recency_raw,
        }
    except Exception:
        return {}
    finally:
        try:
            if detail_page:
                detail_page.close()
        except Exception:
            pass


def scrape_facebook_marketplace(
    city_slug: str,
    query: str,
    min_price: float,
    max_price: float,
    max_results: int,
    interactive_browser: bool = True,
    manual_login_timeout: int = 0,
    browser_mode: str = "chrome_persistent",
    browser_profile_dir: str = "",
    radius_km: int = 0,
    sort_by: str = "suggested",
    condition_filters: List[str] | None = None,
    date_listed: str = "all",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    condition_values = normalize_condition_filters(condition_filters or [])
    date_listed_value = normalize_date_listed(date_listed)
    radius_value = normalize_radius_km(radius_km)

    marketplace_url = build_marketplace_url(
        city_slug,
        query,
        min_price,
        max_price,
        radius_km=radius_value,
        sort_by=sort_by,
        condition_filters=condition_values,
        date_listed=date_listed_value,
    )
    fb_email = os.getenv("FB_EMAIL", "").strip()
    fb_password = os.getenv("FB_PASSWORD", "").strip()
    mode = normalize_browser_mode(browser_mode)
    env_headless = os.getenv("FB_HEADLESS", "true").lower() != "false"
    headless = False if interactive_browser or mode == "chrome_persistent" else env_headless
    max_idle_rounds = max(1, int(os.getenv("FB_MAX_IDLE_SCROLLS", os.getenv("FB_SCROLL_ROUNDS", "6"))))
    scan_delay_min = float(os.getenv("FB_SCAN_DELAY_MIN", "0.25"))
    scan_delay_max = float(os.getenv("FB_SCAN_DELAY_MAX", "0.75"))
    fetch_listing_details = os.getenv("FB_FETCH_LISTING_DETAILS", "true").lower() != "false"
    detail_timeout_ms = int(os.getenv("FB_DETAIL_TIMEOUT_MS", "20000"))
    profile_path = resolve_profile_dir(browser_profile_dir) if mode == "chrome_persistent" else None

    scan_meta: Dict[str, Any] = {
        "interactive_browser": interactive_browser,
        "login_prompted": False,
        "manual_login_timeout_seconds": manual_login_timeout,
        "raw_cards_seen": 0,
        "cards_processed": 0,
        "browser_mode": mode,
        "browser_profile_dir": str(profile_path) if profile_path else "",
        "radius_km": radius_value,
        "sort_by": sort_by,
        "condition_filters": condition_values,
        "date_listed": date_listed_value,
        "max_idle_rounds": max_idle_rounds,
        "fetch_listing_details": fetch_listing_details,
    }

    playwright_mgr = sync_playwright()
    p = playwright_mgr.start()
    browser = None
    context = None
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
                        "Could not open Chrome persistent profile. Close other Chrome windows using this profile, "
                        "or choose a different profile directory.",
                    ) from exc
                page = context.new_page()
            else:
                launch_kwargs: Dict[str, Any] = {"headless": headless}
                if mode == "chrome":
                    launch_kwargs["channel"] = "chrome"
                browser = p.chromium.launch(**launch_kwargs)
                page = browser.new_page()

            # Optional login if credentials are provided.
            if fb_email and fb_password:
                try:
                    page.goto(
                        "https://www.facebook.com/login/device-based/regular/login/",
                        wait_until="domcontentloaded",
                    )
                    page.wait_for_selector('input[name="email"]', timeout=10000).fill(fb_email)
                    page.wait_for_selector('input[name="pass"]', timeout=10000).fill(fb_password)
                    page.wait_for_selector('button[name="login"]', timeout=10000).click()
                    time.sleep(3)
                except Exception:
                    pass

            page.goto(marketplace_url, wait_until="domcontentloaded")
            human_pause(1.6, 2.4)

            if page_needs_login(page):
                scan_meta["login_prompted"] = True
                if interactive_browser:
                    wait_for_manual_login(page, manual_login_timeout)
                    page.goto(marketplace_url, wait_until="domcontentloaded")
                    human_pause(1.6, 2.4)
                    if page_needs_login(page):
                        raise HTTPException(
                            401,
                            "Login still required after manual attempt. Complete all Facebook prompts/checkpoints and retry.",
                        )
                else:
                    raise HTTPException(
                        401,
                        "Facebook login is required. Re-run with interactive_browser=true so you can log in manually.",
                    )

            results: List[Dict[str, Any]] = []
            inspected_links: Set[str] = set()
            pending_links: Set[str] = set()
            pending_cards: Deque[Dict[str, Any]] = deque()
            idle_rounds = 0

            try:
                page.evaluate("() => window.scrollTo({ top: 0, behavior: 'instant' })")
            except Exception:
                pass
            human_pause(0.8, 1.4)

            if interactive_browser:
                show_browser_banner(page, "Waiting for first listings to load...", "info")
            initial_cards_ready = wait_for_initial_cards(page, timeout_seconds=10.0)
            if not initial_cards_ready:
                if interactive_browser:
                    show_browser_banner(
                        page,
                        "Listings are taking longer to appear. Staying at top and retrying...",
                        "warn",
                    )
                human_pause(1.0, 1.8)

            while len(results) < max_results and idle_rounds < max_idle_rounds:
                if not pending_cards:
                    cards = collect_marketplace_cards(page, max_results * 24)
                    scan_meta["raw_cards_seen"] = max(scan_meta["raw_cards_seen"], len(cards))
                    added_now = enqueue_new_cards(cards, pending_cards, pending_links, inspected_links)
                    if added_now > 0:
                        idle_rounds = 0
                        # Process queued cards immediately before any further scrolling.
                        pass

                    # Do not scroll immediately at startup; wait for first visible cards first.
                    if len(inspected_links) == 0 and added_now == 0 and idle_rounds < 3:
                        if interactive_browser:
                            show_browser_banner(page, "Waiting for visible listings...", "info")
                        if wait_for_initial_cards(page, timeout_seconds=6.0):
                            idle_rounds = 0
                        else:
                            idle_rounds += 1
                            human_pause(0.8, 1.4)
                        continue

                    if added_now == 0:
                        if interactive_browser:
                            show_browser_banner(page, "Loading more listings...", "info")

                        # Only scroll when there is nothing left to scan in the current loaded batch.
                        try:
                            page.keyboard.press("End")
                        except Exception:
                            try:
                                page.mouse.wheel(0, random.randint(800, 1200))
                            except Exception:
                                pass
                        human_pause(1.0, 1.8)

                        added_after_scroll = 0
                        for _ in range(4):
                            cards_after_scroll = collect_marketplace_cards(page, max_results * 24)
                            scan_meta["raw_cards_seen"] = max(scan_meta["raw_cards_seen"], len(cards_after_scroll))
                            added_after_scroll += enqueue_new_cards(
                                cards_after_scroll,
                                pending_cards,
                                pending_links,
                                inspected_links,
                            )
                            if added_after_scroll > 0:
                                break
                            human_pause(0.55, 1.0)

                        if added_after_scroll > 0:
                            idle_rounds = 0
                        else:
                            idle_rounds += 1
                            human_pause(0.6, 1.1)
                        continue

                card = pending_cards.popleft()
                full_link = str(card.get("full_link", "")).strip()
                pending_links.discard(full_link)
                href = (card.get("href") or "").strip()
                if not href or not full_link:
                    human_pause(scan_delay_min, scan_delay_max)
                    continue
                if full_link in inspected_links:
                    human_pause(scan_delay_min, scan_delay_max)
                    continue
                inspected_links.add(full_link)

                seen_order = len(inspected_links)

                if interactive_browser:
                    show_browser_banner(
                        page,
                        f"Scanning listing {seen_order}... kept {len(results)}/{max_results}",
                        "info",
                    )

                raw_text = card.get("text") or ""
                title, description, price_text, location, recency = parse_facebook_card_text(raw_text)
                if not title:
                    human_pause(scan_delay_min, scan_delay_max)
                    continue

                detail_payload: Dict[str, str] = {}

                price_value = (
                    parse_best_price(price_text, require_currency=True)
                    or parse_best_price(raw_text, require_currency=True)
                    or parse_best_price(price_text)
                    or parse_best_price(raw_text)
                )

                if price_value is None and fetch_listing_details:
                    if interactive_browser:
                        show_browser_banner(
                            page,
                            f"Price missing on card, opening listing {seen_order} for price...",
                            "warn",
                        )
                    detail_payload = extract_listing_details(page, full_link, timeout_ms=detail_timeout_ms)
                    detail_price_text = detail_payload.get("price", "")
                    if detail_price_text:
                        price_text = detail_price_text
                    price_value = (
                        parse_best_price(detail_price_text, require_currency=True)
                        or parse_best_price(detail_price_text)
                    )
                    detail_title = detail_payload.get("title", "")
                    if detail_title and looks_like_price_only_text(title):
                        title = detail_title
                    description = detail_payload.get("description") or description
                    recency = detail_payload.get("recency") or recency
                    human_pause(0.35, 0.85)

                if price_value is None:
                    if interactive_browser:
                        highlight_marketplace_item(page, extract_marketplace_item_id(href), accepted=False)
                        human_pause(0.18, 0.35)
                    human_pause(scan_delay_min, scan_delay_max)
                    continue
                if min_price > 0 and price_value < min_price:
                    if interactive_browser:
                        highlight_marketplace_item(page, extract_marketplace_item_id(href), accepted=False)
                        human_pause(0.18, 0.35)
                    human_pause(scan_delay_min, scan_delay_max)
                    continue
                if max_price > 0 and price_value > max_price:
                    if interactive_browser:
                        highlight_marketplace_item(page, extract_marketplace_item_id(href), accepted=False)
                        human_pause(0.18, 0.35)
                    human_pause(scan_delay_min, scan_delay_max)
                    continue

                if interactive_browser:
                    highlight_marketplace_item(page, extract_marketplace_item_id(href), accepted=True)
                    human_pause(0.12, 0.3)

                if fetch_listing_details and not detail_payload:
                    if interactive_browser:
                        show_browser_banner(
                            page,
                            f"Collecting details for listing {seen_order}...",
                            "ok",
                        )
                    details = extract_listing_details(page, full_link, timeout_ms=detail_timeout_ms)
                    detail_title = details.get("title", "")
                    if detail_title and looks_like_price_only_text(title):
                        title = detail_title
                    description = details.get("description") or description
                    recency = details.get("recency") or recency
                    human_pause(0.35, 0.85)

                results.append(
                    {
                        "title": title,
                        "description": description,
                        "price_text": price_text if price_text else f"{price_value:.2f}",
                        "price_value": round(price_value, 2),
                        "location": location,
                        "recency": recency,
                        "image": card.get("image") or "",
                        "link": full_link,
                    }
                )
                scan_meta["cards_processed"] = len(results)

                human_pause(scan_delay_min, scan_delay_max)

            if interactive_browser:
                show_browser_banner(page, "Facebook scanning complete.", "ok")
                human_pause(0.8, 1.4)

            return results, scan_meta
        except HTTPException:
            raise
        except Exception as exc:
            if mode == "chrome_persistent" and "has been closed" in str(exc).lower():
                raise HTTPException(
                    409,
                    "Chrome persistent profile became unavailable during scan. Close conflicting Chrome windows "
                    "or use a separate profile directory.",
                ) from exc
            raise HTTPException(502, f"Facebook scrape failed: {exc}")
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
