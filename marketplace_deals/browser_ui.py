import time
from typing import Any

from fastapi import HTTPException

from marketplace_deals.constants import MARKETPLACE_ITEM_PATTERN


def page_needs_login(page: Any) -> bool:
    try:
        current_url = (page.url or "").lower()
        if "/login" in current_url or "/checkpoint" in current_url:
            return True
    except Exception:
        # During redirects/navigation Playwright may throw transient errors.
        return True

    login_selectors = [
        'input[name="email"]',
        'input[name="pass"]',
        'button[name="login"]',
    ]
    for selector in login_selectors:
        try:
            if page.locator(selector).count() > 0:
                return True
        except Exception:
            return True
    return False


def show_browser_banner(page: Any, message: str, tone: str = "info") -> None:
    styles = {
        "info": {"bg": "#1d4ed8", "fg": "#ffffff"},
        "warn": {"bg": "#b45309", "fg": "#ffffff"},
        "ok": {"bg": "#166534", "fg": "#ffffff"},
    }
    palette = styles.get(tone, styles["info"])
    try:
        page.evaluate(
            """
            ({ message, bg, fg }) => {
              let banner = document.getElementById('__codex_scraper_banner');
              if (!banner) {
                banner = document.createElement('div');
                banner.id = '__codex_scraper_banner';
                banner.style.position = 'fixed';
                banner.style.top = '12px';
                banner.style.left = '50%';
                banner.style.transform = 'translateX(-50%)';
                banner.style.padding = '10px 14px';
                banner.style.borderRadius = '12px';
                banner.style.fontSize = '14px';
                banner.style.fontWeight = '600';
                banner.style.zIndex = '2147483647';
                banner.style.boxShadow = '0 8px 24px rgba(0,0,0,.25)';
              }
              banner.style.background = bg;
              banner.style.color = fg;
              banner.textContent = message;
              document.body.appendChild(banner);
            }
            """,
            {"message": message, "bg": palette["bg"], "fg": palette["fg"]},
        )
    except Exception:
        pass


def wait_for_manual_login(page: Any, timeout_seconds: int = 0) -> None:
    show_browser_banner(
        page,
        "Please log in to Facebook in this browser window. Scanning will continue automatically.",
        "warn",
    )
    has_deadline = timeout_seconds is not None and timeout_seconds > 0
    deadline = time.time() + timeout_seconds if has_deadline else None

    while True:
        if has_deadline and deadline is not None and time.time() >= deadline:
            break
        try:
            if not page_needs_login(page):
                return
        except Exception:
            pass
        time.sleep(1)

    raise HTTPException(
        408,
        "Facebook login not completed in time. Please run again and complete login when prompted.",
    )


def extract_marketplace_item_id(href: str) -> str:
    match = MARKETPLACE_ITEM_PATTERN.search(href or "")
    return match.group(1) if match else ""


def highlight_marketplace_item(page: Any, item_id: str, accepted: bool) -> None:
    if not item_id:
        return

    color = "#22c55e" if accepted else "#38bdf8"
    background = "rgba(34, 197, 94, .18)" if accepted else "rgba(56, 189, 248, .18)"
    try:
        page.evaluate(
            """
            ({ itemId, color, background }) => {
              const previous = document.querySelector('[data-codex-scan-active="1"]');
              if (previous) {
                previous.style.outline = '';
                previous.style.background = '';
                previous.removeAttribute('data-codex-scan-active');
              }

              const anchors = Array.from(document.querySelectorAll('a[href*="/marketplace/item/"]'));
              const target = anchors.find((a) => (a.getAttribute('href') || '').includes(`/marketplace/item/${itemId}`));
              if (!target) return;

              const container = target.parentElement || target;
              container.setAttribute('data-codex-scan-active', '1');
              container.scrollIntoView({ behavior: 'smooth', block: 'center' });
              container.style.transition = 'all .2s ease';
              container.style.outline = `3px solid ${color}`;
              container.style.background = background;
            }
            """,
            {"itemId": item_id, "color": color, "background": background},
        )
    except Exception:
        pass


def cex_page_needs_challenge(page: Any) -> bool:
    try:
        title = page.title().lower()
        if "attention required" in title or "just a moment" in title:
            return True
    except Exception:
        return True

    try:
        body = page.inner_text("body").lower()
    except Exception:
        return True

    challenge_markers = [
        "you have been blocked",
        "checking your browser",
        "verify you are human",
        "cloudflare",
    ]
    return any(marker in body for marker in challenge_markers)
