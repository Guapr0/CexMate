# Facebook Marketplace Scanner

This project scans Facebook Marketplace listings, normalizes/groups them with Codex, checks CeX matches, and produces enriched JSON + HTML outputs.

## Current Pipeline

1. Scrape Facebook Marketplace using filters (location, radius, sort, date listed, condition, price).
2. Save raw listings to `output/raw_facebook_list.json`.
3. Run Codex organizer to create `output/organized_facebook_list.json`.
4. Run Codex filter/grouping stage to create `output/filtered_facebook_list.json`.
5. Scan CeX by filtered group titles and write `market_price` + `cex_link` back into the filtered JSON.
6. Generate an HTML report from the filtered/enriched file.

## Requirements

- Python 3.10+
- Google Chrome installed
- Node.js + npm (for Codex CLI installation)
- Python packages in `requirements.txt`

## Prerequisites (Before Running Setup.ps1)

1. Install Python 3.10+ (3.11 recommended):
   - https://www.python.org/downloads/windows/
   - During install, enable **Add python.exe to PATH**.
   - `pip` is included with Python.

2. Install Node.js LTS (includes `npm`):
   - https://nodejs.org/en/download

3. Install Google Chrome:
   - https://www.google.com/chrome/

4. If PowerShell blocks scripts, run this in the same terminal:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

Quick checks:

```powershell
python --version
python -m pip --version
npm --version
```

## One-Shot Setup (Windows PowerShell)

Run from project root:

```powershell
.\setup.ps1
```

This installs:

- Python dependencies from `requirements.txt`
- Playwright Chromium browser
- Codex CLI (`npm install -g @openai/codex`)

Optional (skip Codex CLI install):

```powershell
.\setup.ps1 -SkipCodex
```

Manual equivalent:

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
npm install -g @openai/codex
```

After install, verify:

```powershell
codex --version
```

## Run

One-click launcher (recommended):

```powershell
.\run.bat
```

This opens two terminal windows automatically:
- API (`python app.py`)
- Streamlit UI (`python -m streamlit run gui.py`)

Manual run:

From the project root:

```powershell
python app.py
```

In a second terminal:

```powershell
py -m streamlit run gui.py
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
