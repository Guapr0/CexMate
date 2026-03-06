import json
import re
import shutil
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List

from marketplace_deals.config import OUTPUT_DIR

HTML_REPORT_FILENAME = "facebook_deals_report.html"


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


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _clean_text(value)
    if not text:
        return None
    text = text.replace(",", "").replace("£", "").replace("$", "").replace("€", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _format_number(value: Any, suffix: str = "") -> str:
    numeric = _safe_number(value)
    if numeric is None:
        return "N/A"
    if float(numeric).is_integer():
        return f"{int(numeric):,}{suffix}"
    return f"{numeric:,.2f}{suffix}"


def _format_storage(value: Any) -> str:
    numeric = _safe_number(value)
    if numeric is None:
        return "N/A"
    if numeric >= 1024:
        tb_value = numeric / 1024
        if float(tb_value).is_integer():
            return f"{int(tb_value)} TB"
        return f"{tb_value:.2f}".rstrip("0").rstrip(".") + " TB"
    return _format_number(numeric, " GB")


def _format_bool_or_text(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    text = _clean_text(value)
    return text if text else "N/A"


def _format_link_anchor(value: Any, label: str) -> str:
    raw = _clean_text(value)
    if not raw or raw.upper() == "NOT_FOUND":
        return '<span class="muted">N/A</span>'
    if raw.lower().startswith(("http://", "https://")):
        safe_url = escape(raw, quote=True)
        return (
            f'<a class="link-chip" href="{safe_url}" target="_blank" rel="noopener noreferrer">'
            f"{escape(label)}</a>"
        )
    return escape(raw)


def _render_image_cell(value: Any) -> str:
    image_url = _clean_text(value)
    if not image_url.lower().startswith(("http://", "https://")):
        return '<span class="muted">N/A</span>'
    return (
        f'<img class="thumb" src="{escape(image_url, quote=True)}" '
        'alt="Listing image" loading="lazy" />'
    )


def _render_listing_row(listing: Dict[str, Any], index: int) -> str:
    description = _format_bool_or_text(listing.get("description"))
    description_html = f'<div class="desc-text" title="{escape(description, quote=True)}">{escape(description)}</div>'

    cells: List[tuple[str, str, bool]] = [
        ("idx", str(index), False),
        ("", _format_bool_or_text(listing.get("brand")), False),
        ("", _format_bool_or_text(listing.get("model")), False),
        ("", _format_bool_or_text(listing.get("variant")), False),
        ("", _format_bool_or_text(listing.get("color")), False),
        ("spec", _format_storage(listing.get("storage_gb")), False),
        ("spec", _format_number(listing.get("ram_gb"), " GB"), False),
        ("spec", _format_bool_or_text(listing.get("dual_sim")), False),
        ("spec", _format_number(listing.get("battery_health_percent"), "%"), False),
        ("spec", _format_bool_or_text(listing.get("accessories_included")), False),
        ("", _format_bool_or_text(listing.get("carrier")), False),
        ("", _format_bool_or_text(listing.get("location")), False),
        ("", _format_bool_or_text(listing.get("recency")), False),
        ("spec", _format_bool_or_text(listing.get("grade")), False),
        ("price", _format_number(listing.get("price")), False),
        ("price", _format_number(listing.get("market_price")), False),
        ("image", _render_image_cell(listing.get("image")), True),
        ("links", _format_link_anchor(listing.get("fb_link"), "Open"), True),
        ("links", _format_link_anchor(listing.get("cex_link"), "Open"), True),
        ("desc", description_html, True),
    ]

    rendered_cells: List[str] = []
    for class_name, value, is_html in cells:
        class_attr = f' class="{class_name}"' if class_name else ""
        rendered_value = value if is_html else escape(value)
        rendered_cells.append(f"<td{class_attr}>{rendered_value}</td>")
    return f"<tr>{''.join(rendered_cells)}</tr>"


def _render_group_table(listings: List[Dict[str, Any]]) -> str:
    if not listings:
        return '<p class="empty">No listings in this group.</p>'

    headers: List[tuple[str, str]] = [
        ("idx", "#"),
        ("", "Brand"),
        ("", "Model"),
        ("", "Variant"),
        ("", "Color"),
        ("spec", "Storage"),
        ("spec", "RAM"),
        ("spec", "Dual SIM"),
        ("spec", "Battery"),
        ("spec", "Accessories"),
        ("", "Carrier"),
        ("", "Location"),
        ("", "Recency"),
        ("spec", "Grade"),
        ("price", "Price"),
        ("price", "Market Price"),
        ("image", "Image"),
        ("links", "FB"),
        ("links", "CeX"),
        ("desc", "Description"),
    ]

    header_html: List[str] = []
    for class_name, label in headers:
        class_attr = f' class="{class_name}"' if class_name else ""
        header_html.append(f"<th{class_attr}>{escape(label)}</th>")

    body_html = "".join(_render_listing_row(listing, idx) for idx, listing in enumerate(listings, start=1))
    return (
        '<div class="table-wrap">'
        '<table class="listing-table">'
        f"<thead><tr>{''.join(header_html)}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
        "</div>"
    )


def generate_facebook_listings_html(filtered_json_path: str) -> Dict[str, Any]:
    path = Path(filtered_json_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Filtered file not found: {path}")

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid filtered JSON: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("Filtered JSON root must be an array of groups.")

    groups: List[Dict[str, Any]] = [row for row in payload if isinstance(row, dict)]
    groups.sort(key=lambda row: _clean_text(row.get("group_title")).lower())

    rendered_groups: List[str] = []
    listings_rendered = 0

    def _listing_price_sort_key(row: Dict[str, Any]) -> tuple[bool, float]:
        number = _safe_number(row.get("price"))
        return number is None, number if number is not None else float("inf")

    for group in groups:
        group_title = _clean_text(group.get("group_title")) or "Untitled Group"
        listings = group.get("listings")
        if not isinstance(listings, list):
            listings = []

        sorted_listings = sorted(
            [row for row in listings if isinstance(row, dict)],
            key=_listing_price_sort_key,
        )

        listings_rendered += len(sorted_listings)

        group_content_html = _render_group_table(sorted_listings)
        rendered_groups.append(
            "<details class=\"group\" open>"
            "<summary>"
            f"<span class=\"group-title\">{escape(group_title)}</span>"
            f"<span class=\"group-count\">{len(sorted_listings)} listing(s)</span>"
            "</summary>"
            f"<div class=\"group-content\">{group_content_html}</div>"
            "</details>"
        )

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%SZ")
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Facebook Deals Report</title>
  <style>
    :root {{
      --bg: #f5f8ff;
      --panel: #ffffff;
      --ink: #13213a;
      --muted: #5f6f8f;
      --line: #d7e0f0;
      --primary: #1242a8;
      --accent: #eaf1ff;
      --shadow: 0 8px 22px rgba(11, 27, 56, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 20px;
      font-family: "Segoe UI", Tahoma, Arial, sans-serif;
      color: var(--ink);
      background: radial-gradient(circle at top left, #f0f6ff 0%, #f5f8ff 45%, #eef3fb 100%);
      overflow-x: hidden;
    }}
    .container {{
      max-width: 1480px;
      margin: 0 auto;
    }}
    .report-header {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px 20px;
      box-shadow: var(--shadow);
      margin-bottom: 14px;
    }}
    .report-header h1 {{
      margin: 0;
      font-size: 1.45rem;
      color: var(--primary);
    }}
    .report-meta {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 0.95rem;
    }}
    .group {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      margin-bottom: 12px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .group > summary {{
      list-style: none;
      cursor: pointer;
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 14px 16px;
      background: var(--accent);
      border-bottom: 1px solid var(--line);
    }}
    .group > summary::-webkit-details-marker {{
      display: none;
    }}
    .group-title {{
      font-weight: 700;
      color: var(--primary);
      overflow-wrap: anywhere;
    }}
    .group-count {{
      color: var(--muted);
      font-size: 0.92rem;
      white-space: nowrap;
    }}
    .group-content {{
      padding: 10px 12px 12px;
    }}
    .table-wrap {{
      width: 100%;
    }}
    .listing-table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 12px;
      background: #fff;
    }}
    .listing-table th,
    .listing-table td {{
      border: 1px solid var(--line);
      padding: 6px 7px;
      vertical-align: top;
      overflow-wrap: anywhere;
      word-break: break-word;
      line-height: 1.25;
    }}
    .listing-table th {{
      background: #edf3ff;
      color: #1d3a75;
      position: sticky;
      top: 0;
      z-index: 1;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      font-size: 11px;
    }}
    .listing-table tbody tr:nth-child(even) {{
      background: #fafcff;
    }}
    .listing-table .idx {{
      width: 32px;
      text-align: center;
      font-weight: 700;
    }}
    .listing-table .spec {{
      width: 70px;
      text-align: center;
    }}
    .listing-table .price {{
      width: 88px;
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    .listing-table .image {{
      width: 64px;
      text-align: center;
    }}
    .listing-table .links {{
      width: 62px;
      text-align: center;
    }}
    .listing-table .desc {{
      width: 20%;
    }}
    .thumb {{
      width: 52px;
      height: 52px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid var(--line);
      display: block;
      margin: 0 auto;
    }}
    .link-chip {{
      display: inline-block;
      padding: 2px 7px;
      border-radius: 999px;
      border: 1px solid #c8d9ff;
      background: #f5f9ff;
      color: #0f4ac2;
      text-decoration: none;
      font-size: 11px;
      font-weight: 600;
    }}
    .link-chip:hover {{
      background: #e8f0ff;
    }}
    .desc-text {{
      display: -webkit-box;
      -webkit-line-clamp: 4;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }}
    .muted {{
      color: var(--muted);
    }}
    .empty {{
      margin: 0;
      color: var(--muted);
      padding: 4px;
    }}
    @media (max-width: 1180px) {{
      .listing-table {{
        font-size: 11px;
      }}
      .listing-table th {{
        font-size: 10px;
      }}
    }}
    @media (max-width: 860px) {{
      body {{
        padding: 12px;
      }}
      .group > summary {{
        flex-direction: column;
        align-items: flex-start;
      }}
      .listing-table {{
        font-size: 10px;
      }}
      .listing-table .desc {{
        width: auto;
      }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <section class="report-header">
      <h1>Facebook Deals Report</h1>
      <div class="report-meta">Generated at {escape(generated_at)} | Groups: {len(groups)} | Listings: {listings_rendered}</div>
    </section>
    {''.join(rendered_groups) if rendered_groups else '<p class="empty">No grouped listings available.</p>'}
  </div>
</body>
</html>
"""

    html_path = path.parent / HTML_REPORT_FILENAME
    html_path.write_text(html_content, encoding="utf-8")

    return {
        "html_path": str(html_path.resolve()),
        "groups_rendered": len(groups),
        "listings_rendered": listings_rendered,
    }


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

    html_meta = generate_facebook_listings_html(str(path))

    return {
        "json_path": str(path),
        "html_path": html_meta.get("html_path", ""),
        "html_groups_rendered": int(html_meta.get("groups_rendered", 0)),
        "html_listings_rendered": int(html_meta.get("listings_rendered", 0)),
        "groups_in_cex_results": len(updates_by_group),
        "groups_updated": groups_updated,
        "groups_unmatched": max(0, len(updates_by_group) - len(matched_groups)),
        "listings_updated": listings_updated,
    }
