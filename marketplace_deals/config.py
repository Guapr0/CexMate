from pathlib import Path

from fastapi import HTTPException

from marketplace_deals.constants import SUPPORTED_BROWSER_MODES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


def normalize_browser_mode(browser_mode: str) -> str:
    mode = (browser_mode or "chromium").strip().lower()
    if mode not in SUPPORTED_BROWSER_MODES:
        raise HTTPException(
            400,
            f"Unsupported browser_mode '{browser_mode}'. Use one of: chromium, chrome, chrome_persistent.",
        )
    return mode


def resolve_profile_dir(raw_profile_dir: str) -> Path:
    profile_dir = (raw_profile_dir or "").strip()
    if not profile_dir:
        profile_dir = ".browser_profile/chrome_marketplace"
    path = Path(profile_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
