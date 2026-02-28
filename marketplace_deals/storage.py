import csv
import json
import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List

from marketplace_deals.config import OUTPUT_DIR


def _build_facebook_html_report(
    query: str,
    city_slug: str,
    listings: List[Dict[str, Any]],
    generated_at: str,
) -> str:
    rows: List[str] = []
    for index, listing in enumerate(listings, start=1):
        title = escape(str(listing.get("title", ""))) or "Untitled item"
        description = escape(str(listing.get("description", ""))).strip() or "-"
        price_text = escape(str(listing.get("price_text", ""))).strip()
        if not price_text:
            price_value = listing.get("price_value")
            price_text = f"{price_value:.2f}" if isinstance(price_value, (float, int)) else "-"
        location = escape(str(listing.get("location", ""))).strip() or "-"
        recency = escape(str(listing.get("recency", ""))).strip() or "Unknown"
        link = escape(str(listing.get("link", ""))).strip()
        image_url = escape(str(listing.get("image", ""))).strip()
        image_html = (
            f'<img src="{image_url}" alt="Item image" class="item-image" loading="lazy" />'
            if image_url
            else "<span class='na'>N/A</span>"
        )
        link_html = f'<a href="{link}" target="_blank" rel="noopener noreferrer">Open Listing</a>' if link else "-"

        rows.append(
            "<tr>"
            f"<td>{index}</td>"
            f"<td>{title}</td>"
            f"<td>{description}</td>"
            f"<td>{price_text}</td>"
            f"<td>{location}</td>"
            f"<td>{recency}</td>"
            f"<td>{image_html}</td>"
            f"<td>{link_html}</td>"
            "</tr>"
        )

    table_rows = "".join(rows) if rows else (
        "<tr><td colspan='8' class='empty'>No Facebook listings matched the filters.</td></tr>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Facebook Marketplace Scan Report</title>
  <style>
    :root {{
      --bg: #f4f7fb;
      --card: #ffffff;
      --ink: #1a2233;
      --muted: #5d6a84;
      --line: #d9e1ef;
      --accent: #1d4ed8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background: radial-gradient(circle at top right, #e6edff 0%, var(--bg) 45%);
      color: var(--ink);
      padding: 28px 16px 42px;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      overflow: hidden;
      box-shadow: 0 18px 40px rgba(14, 30, 80, 0.12);
    }}
    .header {{
      padding: 20px 24px;
      border-bottom: 1px solid var(--line);
      background: linear-gradient(135deg, #ffffff 0%, #f4f8ff 100%);
    }}
    .header h1 {{
      margin: 0;
      font-size: 24px;
      letter-spacing: 0.2px;
    }}
    .meta {{
      margin-top: 8px;
      color: var(--muted);
      font-size: 14px;
    }}
    .table-wrap {{
      overflow-x: auto;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      min-width: 980px;
    }}
    thead th {{
      text-align: left;
      padding: 12px 14px;
      background: #eff4ff;
      border-bottom: 1px solid var(--line);
      color: #1d2d57;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: 0.3px;
    }}
    tbody td {{
      padding: 12px 14px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      font-size: 14px;
    }}
    tbody tr:nth-child(even) {{
      background: #fafcff;
    }}
    .item-image {{
      width: 92px;
      height: 92px;
      border-radius: 10px;
      object-fit: cover;
      border: 1px solid var(--line);
      background: #eef3ff;
    }}
    a {{
      color: var(--accent);
      font-weight: 600;
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    .na {{
      color: var(--muted);
    }}
    .empty {{
      text-align: center;
      color: var(--muted);
      padding: 22px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <h1>Facebook Marketplace Results</h1>
      <div class="meta">Query: <strong>{escape(query)}</strong> | Location: <strong>{escape(city_slug)}</strong></div>
      <div class="meta">Generated (UTC): {escape(generated_at)} | Total items: {len(listings)}</div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>No</th>
            <th>Item</th>
            <th>Description</th>
            <th>Price</th>
            <th>Location</th>
            <th>How Recent</th>
            <th>Image</th>
            <th>Listing</th>
          </tr>
        </thead>
        <tbody>
          {table_rows}
        </tbody>
      </table>
    </div>
  </div>
</body>
</html>
"""


def save_deals(query: str, city_slug: str, deals: List[Dict[str, Any]]) -> Dict[str, str]:
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    safe_name = re.sub(r"[^a-z0-9]+", "-", f"{query}-{city_slug}".lower()).strip("-")
    base_name = f"{timestamp}-{safe_name}" if safe_name else timestamp

    json_path = OUTPUT_DIR / f"{base_name}.json"
    csv_path = OUTPUT_DIR / f"{base_name}.csv"

    with json_path.open("w", encoding="utf-8") as json_file:
        json.dump(deals, json_file, indent=2)

    headers = [
        "facebook_title",
        "facebook_price",
        "facebook_price_text",
        "facebook_location",
        "facebook_link",
        "cex_title",
        "cex_price",
        "cex_link",
        "match_score",
        "estimated_profit",
        "margin_percent",
    ]

    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=headers)
        writer.writeheader()
        for deal in deals:
            writer.writerow({header: deal.get(header) for header in headers})

    return {
        "json_path": str(Path(json_path).resolve()),
        "csv_path": str(Path(csv_path).resolve()),
    }


def save_facebook_results(
    query: str,
    city_slug: str,
    listings: List[Dict[str, Any]],
) -> Dict[str, str]:
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    generated_at_utc = datetime.utcnow().isoformat() + "Z"
    safe_name = re.sub(r"[^a-z0-9]+", "-", f"{query}-{city_slug}".lower()).strip("-")
    base_name = f"{timestamp}-{safe_name}" if safe_name else timestamp

    json_path = OUTPUT_DIR / f"{base_name}.json"
    csv_path = OUTPUT_DIR / f"{base_name}.csv"
    html_path = OUTPUT_DIR / f"{base_name}.html"

    with json_path.open("w", encoding="utf-8") as json_file:
        json.dump(listings, json_file, indent=2)

    headers = ["no", "item", "description", "price", "location", "how_recent", "image", "link"]
    with csv_path.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=headers)
        writer.writeheader()
        for index, listing in enumerate(listings, start=1):
            writer.writerow(
                {
                    "no": index,
                    "item": listing.get("title", ""),
                    "description": listing.get("description", ""),
                    "price": listing.get("price_text", listing.get("price_value", "")),
                    "location": listing.get("location", ""),
                    "how_recent": listing.get("recency", ""),
                    "image": listing.get("image", ""),
                    "link": listing.get("link", ""),
                }
            )

    html_output = _build_facebook_html_report(
        query=query,
        city_slug=city_slug,
        listings=listings,
        generated_at=generated_at_utc,
    )
    with html_path.open("w", encoding="utf-8") as html_file:
        html_file.write(html_output)

    return {
        "json_path": str(Path(json_path).resolve()),
        "csv_path": str(Path(csv_path).resolve()),
        "html_path": str(Path(html_path).resolve()),
    }
