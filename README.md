# Facebook Marketplace Scraper + Codex Organizer

This app scans Facebook Marketplace listings for a product query, saves raw results, and then runs Codex to create a normalized JSON file.

## What It Does

- Scrapes Facebook Marketplace with your selected filters (location, radius, sort, date, price, condition).
- Saves raw listings to `output/raw_facebook_list.json`.
- Runs Codex organizer to create `output/organized_facebook_list.json`.
- Shows progress updates in the Streamlit UI.
- Opens a separate live log window for organizer progress.

## Important Runtime Behavior

- On every `/find_phone_deals` run, the app clears everything inside `output/` first.
- This ensures each run starts clean and you only keep the latest run outputs/logs.

## Requirements

- Windows (recommended for current launch scripts)
- Python 3.10+ (3.11 recommended)
- Google Chrome installed
- Codex CLI installed and available in `PATH` (`codex --version` should work)

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

## How To Run

From project root:

```powershell
cd C:\Users\8-Muso-8\Downloads\facebook-marketplace-scraper-main\facebook-marketplace-scraper-main
```

Start API (terminal 1):

```powershell
python app.py
```

Start UI (terminal 2):

```powershell
streamlit run gui.py
```

Open the Streamlit URL shown in terminal 2 (usually `http://localhost:8501`).

## Typical Flow

1. Fill search filters in UI.
2. Click `Start Scan`.
3. Chrome opens for Marketplace scraping (login/session as needed).
4. Raw output is saved.
5. Codex organizer runs and writes organized output.
6. Download files from the UI output section.

## Output Files

- `output/raw_facebook_list.json`
- `output/organized_facebook_list.json`
- `output/codex_organizer_exec.log`
- `output/codex_organizer_prompt.txt`
- `output/codex_organizer_last_message.txt` (when available)

## Troubleshooting

- `Failed to reach API` / WinError 10061:
  - API is not running on `127.0.0.1:8000`.
  - Start `python app.py` first.
- `codex executable not found`:
  - Install Codex CLI and ensure `codex` is on `PATH`.
- No organizer output:
  - Check `output/codex_organizer_exec.log` for errors.

## Can I Minimize Chrome/CMD While It Runs?

Yes. Minimizing windows does not stop the process.

- Keep the API terminal and Streamlit terminal running.
- Do not close Chrome if the scraper still needs it.
- Do not close the organizer log window if you still want live progress visibility.
