import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from marketplace_deals.config import OUTPUT_DIR


def clear_output_directory() -> None:
    for item in OUTPUT_DIR.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except FileNotFoundError:
                continue


def save_raw_facebook_results(listings: List[Dict[str, Any]]) -> Dict[str, str]:
    raw_json_path = OUTPUT_DIR / "raw_facebook_list.json"
    with raw_json_path.open("w", encoding="utf-8") as json_file:
        json.dump(listings, json_file, indent=2)
    return {"raw_facebook_json_path": str(Path(raw_json_path).resolve())}


def save_cex_results_json(query: str, city_slug: str, results: List[Dict[str, Any]]) -> Dict[str, str]:
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    safe_name = re.sub(r"[^a-z0-9]+", "-", f"{query}-{city_slug}".lower()).strip("-")
    base_name = f"{timestamp}-{safe_name}" if safe_name else timestamp

    json_path = OUTPUT_DIR / f"{base_name}.json"
    with json_path.open("w", encoding="utf-8") as json_file:
        json.dump(results, json_file, indent=2)
    return {"json_path": str(Path(json_path).resolve())}


def _normalize_group_title_key(value: str) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def apply_cex_results_to_filtered_json(
    filtered_json_path: str,
    cex_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    path = Path(filtered_json_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Filtered file not found: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid filtered JSON: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("Filtered JSON root must be an array of groups.")

    updates_by_group: Dict[str, Dict[str, Any]] = {}
    for row in cex_results or []:
        if not isinstance(row, dict):
            continue
        group_title = str(row.get("Group Title", "")).strip()
        key = _normalize_group_title_key(group_title)
        if not key:
            continue
        updates_by_group[key] = {
            "market_price": row.get("market_price"),
            "cex_link": row.get("cex_link"),
        }

    groups_updated = 0
    listings_updated = 0
    matched_groups: set[str] = set()
    for group in payload:
        if not isinstance(group, dict):
            continue
        group_title = str(group.get("group_title", "")).strip()
        key = _normalize_group_title_key(group_title)
        if not key:
            continue
        update_values = updates_by_group.get(key)
        if update_values is None:
            continue

        listings = group.get("listings")
        if not isinstance(listings, list):
            continue

        matched_groups.add(key)
        groups_updated += 1
        for listing in listings:
            if not isinstance(listing, dict):
                continue
            listing["market_price"] = update_values.get("market_price")
            listing["cex_link"] = update_values.get("cex_link")
            listings_updated += 1

    with path.open("w", encoding="utf-8") as json_file:
        json.dump(payload, json_file, indent=2)

    return {
        "json_path": str(path),
        "groups_in_cex_results": len(updates_by_group),
        "groups_updated": groups_updated,
        "groups_unmatched": max(0, len(updates_by_group) - len(matched_groups)),
        "listings_updated": listings_updated,
    }
