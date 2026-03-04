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
