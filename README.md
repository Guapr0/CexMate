# Facebook Marketplace Scanner (Codex + CeX)

This project scans Facebook Marketplace listings, normalizes/groups them with Codex, checks CeX matches, and produces enriched JSON + HTML outputs.

## Current Pipeline

1. Scrape Facebook Marketplace using filters (location, radius, sort, date listed, condition, price).
2. Save raw listings to `output/raw_facebook_list.json`.
3. Run Codex organizer to create `output/organized_facebook_list.json`.
4. Run Codex filter/grouping stage to create `output/filtered_facebook_list.json`.
5. Scan CeX by filtered group titles and write `market_price` + `cex_link` back into the filtered JSON.
6. Generate an HTML report from the filtered/enriched file.

## Runtime Behavior

- Each `/find_phone_deals` run clears everything in `output/` first.
- Default browser mode is Chrome persistent profile: `.browser_profile/chrome_marketplace`.
- Codex runs two stages (organizer + filter) and opens status CMD windows for each stage.

## Requirements

- Python 3.10+
- Google Chrome installed
- Codex CLI installed and on `PATH` (`codex --version`)
- Python packages in `requirements.txt`

Install dependencies:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

Note: Chromium install is needed for Playwright Chromium mode and `/return_ip_information`.

## Run

From the project root:

```powershell
python app.py
```

In a second terminal:

```powershell
streamlit run gui.py
```

Open Streamlit (usually `http://localhost:8501`).

## Output Files

- `output/raw_facebook_list.json`
- `output/organized_facebook_list.json`
- `output/filtered_facebook_list.json` (grouped listings, later enriched with CeX fields)
- `output/facebook_deals_report.html`

## API Endpoints

- `GET /find_phone_deals` - full pipeline (Facebook scrape -> Codex -> CeX -> HTML report).
- `GET /crawl_facebook_marketplace` - scrape-only endpoint.
- `GET /return_ip_information` - IP information lookup via Playwright + parsing.

## Troubleshooting

- `Failed to reach API` / connection refused:
  - Start API first with `python app.py`.
- `codex executable not found`:
  - Install Codex CLI and ensure it is available to the API process `PATH`.
- `Could not open Chrome persistent profile`:
  - Close other Chrome windows using the same profile, or switch profile directory/browser mode.
- CeX challenge errors:
  - Re-run with interactive browser enabled so you can solve the challenge in the opened browser.
