from datetime import datetime
from pathlib import Path

import requests
import streamlit as st

API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_PROFILE_DIR = ".browser_profile/chrome_marketplace"
RADIUS_OPTIONS = [1, 2, 5, 10, 20, 40, 60, 100, 250, 500]

COUNTRY_CURRENCIES = {
    "United Kingdom": ("GBP", "£"),
}

SORT_OPTIONS = {
    "Suggested (default)": "suggested",
    "Distance: Nearest First": "distance_ascend",
    "Date Listed: Newest First": "creation_time_descend",
    "Price: Lowest First": "price_ascend",
    "Price: Highest First": "price_descend",
}
CONDITION_OPTIONS = {
    "New": "new",
    "Used - Like new": "used_like_new",
    "Used - Good": "used_good",
    "Used - Fair": "used_fair",
}
DATE_LISTED_OPTIONS = {
    "All": "all",
    "Last 24 hours": "1",
    "Last 7 days": "7",
    "Last 30 days": "30",
}

THEMES = {
    "Dark": {
        "bg": "radial-gradient(circle at 10% 20%, #152137 0%, #0a101f 55%, #070c18 100%)",
        "ink": "#f2f6ff",
        "muted": "#9eb1d4",
        "card": "rgba(20,30,52,.78)",
        "line": "#2a3d62",
        "shadow": "0 14px 40px rgba(0,0,0,.35)",
    },
}


def parse_optional_price(raw: str, field_name: str, symbol: str) -> float | None:
    value = (raw or "").strip()
    if not value:
        return None

    normalized = value.replace(symbol, "").replace(",", "").strip()
    try:
        parsed = float(normalized)
    except ValueError:
        st.error(f"{field_name} must be a valid number.")
        st.stop()

    if parsed < 0:
        st.error(f"{field_name} cannot be negative.")
        st.stop()
    return parsed


st.set_page_config(page_title="Guapro Glow", layout="wide")

theme = THEMES["Dark"]

st.markdown(
    f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=DM+Sans:wght@400;500;700&display=swap');
      .stApp {{
        font-family: 'DM Sans', sans-serif;
        background: {theme["bg"]};
      }}
      h1, h2, h3 {{
        font-family: 'Space Grotesk', sans-serif !important;
        color: {theme["ink"]} !important;
      }}
      .guapro-hero {{
        background: {theme["card"]};
        border: 1px solid {theme["line"]};
        border-radius: 20px;
        padding: 18px 20px;
        box-shadow: {theme["shadow"]};
        text-align: center;
      }}
      .guapro-sub {{
        color: {theme["muted"]};
        margin-top: 6px;
      }}
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="guapro-hero">
      <h1>Guapro Glow: Find Low, Flip Pro</h1>
      <div class="guapro-sub">Facebook Marketplace product search scanner.</div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.write("")

with st.form("deal_scanner_form"):
    st.subheader("Product To Scan For")
    product_query = st.text_input(
        "Product",
        value="",
        placeholder="e.g. iPhone 13, MacBook Pro, PS5, Rolex, Sofa",
    )
    spec_text = st.text_area(
        "Filtering Description(optional)",
        value="",
        placeholder="e.g. 128GB unlocked excellent condition",
        height=90,
    )

    st.subheader("Marketplace Filters")
    row1_col1, row1_col2, row1_col3 = st.columns(3)
    with row1_col1:
        country = st.selectbox(
            "Country",
            list(COUNTRY_CURRENCIES.keys()),
            index=0,
            disabled=True,
        )
    with row1_col2:
        location_value = st.text_input(
            "Location (City or Facebook Slug)",
            "london",
            help="Supports any Facebook Marketplace location slug/city.",
        )
    with row1_col3:
        radius_km = st.selectbox(
            "Radius",
            options=RADIUS_OPTIONS,
            index=6,
            format_func=lambda x: f"{x} kilometers",
        )

    row2_col1, row2_col2, row2_col3 = st.columns(3)
    with row2_col1:
        sort_label = st.selectbox("Sort By", list(SORT_OPTIONS.keys()), index=0)
        sort_by = SORT_OPTIONS[sort_label]
    with row2_col2:
        selected_conditions_labels = st.multiselect(
            "Condition",
            options=list(CONDITION_OPTIONS.keys()),
            default=[],
        )
    with row2_col3:
        date_listed_label = st.selectbox(
            "Date Listed",
            options=list(DATE_LISTED_OPTIONS.keys()),
            index=0,
        )

    row3_col1, row3_col2, row3_col3 = st.columns(3)
    currency_code, currency_symbol = COUNTRY_CURRENCIES[country]
    with row3_col1:
        price_min_col, price_max_col = st.columns(2)
        with price_min_col:
            min_price_raw = st.text_input(
                f"Price Min ({currency_symbol})",
                value="",
                placeholder=f"{currency_symbol}100",
            )
        with price_max_col:
            max_price_raw = st.text_input(
                f"Price Max ({currency_symbol})",
                value="",
                placeholder=f"{currency_symbol}500",
            )
    with row3_col2:
        max_results = st.number_input(
            "Scan Depth",
            min_value=1,
            max_value=250,
            value=20,
            step=1,
            help="Maximum number of listing cards to inspect.",
        )
    with row3_col3:
        browser_mode_label = st.selectbox(
            "Browser Mode",
            [
                "Chrome (persistent login)",
                "Chrome (fresh session)",
            ],
            index=0,
            help="Persistent login reuses your saved Chrome session. Fresh session starts clean each run.",
        )
        browser_mode = {
            "Chrome (persistent login)": "chrome_persistent",
            "Chrome (fresh session)": "chrome",
        }[browser_mode_label]

    button_spacer, button_col = st.columns([8, 2])
    with button_col:
        submitted = st.form_submit_button("Start Scan", use_container_width=True)

if submitted:
    if not product_query.strip():
        st.error("Product is required.")
        st.stop()
    if not location_value.strip():
        st.error("Location is required.")
        st.stop()

    min_price = parse_optional_price(min_price_raw, "Price Min", currency_symbol)
    max_price = parse_optional_price(max_price_raw, "Price Max", currency_symbol)
    if min_price is not None and max_price is not None and min_price > max_price:
        st.error("Price Min cannot be greater than Price Max.")
        st.stop()

    params = {
        "city": location_value.strip(),
        "query": product_query.strip(),
        "max_results": int(max_results),
        "interactive_browser": "true",
        "manual_login_timeout": 0,
        "browser_mode": browser_mode,
        "browser_profile_dir": DEFAULT_PROFILE_DIR,
        "radius_km": radius_km,
        "sort_by": sort_by,
        "date_listed": DATE_LISTED_OPTIONS[date_listed_label],
    }
    selected_condition_values = [CONDITION_OPTIONS[label] for label in selected_conditions_labels]
    if selected_condition_values:
        params["condition_filters"] = selected_condition_values
    if min_price is not None:
        params["min_price"] = min_price
    if max_price is not None:
        params["max_price"] = max_price

    updates: list[str] = []
    updates_placeholder = st.empty()

    def push_update(message: str) -> None:
        stamp = datetime.now().strftime("%I:%M:%S %p")
        updates.append(f"[{stamp}] {message}")
        updates_placeholder.markdown(
            "### Current Updates\n" + "\n".join(f"- {line}" for line in updates)
        )

    push_update("Scan started.")
    push_update(f"Looking for '{product_query.strip()}' in Facebook Marketplace.")
    if spec_text.strip():
        push_update(f"With spec/keywords: {spec_text.strip()}")
    else:
        push_update("No spec/description provided.")
    push_update(
        f"Location: {location_value.strip()} | Radius: {radius_km} km | Sort: {sort_label}"
    )
    if selected_conditions_labels:
        push_update(f"Condition filter: {', '.join(selected_conditions_labels)}")
    else:
        push_update("Condition filter: none (all conditions).")
    push_update(f"Date listed: {date_listed_label}")
    if min_price is None and max_price is None:
        push_update("No price filter provided.")
    else:
        push_update(
            f"Price filter: {currency_symbol}{min_price if min_price is not None else 0:.0f} to "
            f"{currency_symbol}{max_price if max_price is not None else 0:.0f} ({currency_code})"
        )
    push_update(f"Scan depth: up to {max_results} listing cards.")
    push_update(f"Browser mode: {browser_mode_label}.")

    with st.spinner("Scanning Facebook Marketplace in Chrome..."):
        try:
            response = requests.get(f"{API_BASE_URL}/find_phone_deals", params=params)
        except requests.RequestException as exc:
            push_update(f"Scan failed: API connection error ({exc}).")
            st.error(f"Failed to reach API: {exc}")
            st.stop()

    if not response.ok:
        error_message = response.text
        try:
            payload_error = response.json()
            error_message = payload_error.get("detail", error_message)
        except Exception:
            pass
        push_update(f"Scan failed with API error {response.status_code}: {error_message}")
        st.error(f"API error ({response.status_code}): {error_message}")
        st.stop()

    payload = response.json()
    counts = payload.get("counts", {})
    codex_meta = payload.get("codex", {})
    cex_meta = payload.get("cex", {})
    results = payload.get("results", [])
    files = payload.get("files", {})

    push_update(f"{counts.get('facebook_matches', 0)} matching products found.")
    if files.get("raw_facebook_json_path"):
        push_update("Raw Facebook output saved.")
    if codex_meta.get("launched_cmd_window_organizer"):
        push_update("Codex organizer cmd window launched.")
    if codex_meta.get("launched_cmd_window_filter"):
        push_update("Codex filter cmd window launched.")
    if files.get("organized_facebook_json_path"):
        organized_count = codex_meta.get("organized_count")
        if isinstance(organized_count, int):
            push_update(f"Organized Facebook output saved ({organized_count} items).")
        else:
            push_update("Organized Facebook output saved.")
    if files.get("filtered_facebook_json_path"):
        filtered_count = codex_meta.get("filtered_count")
        if isinstance(filtered_count, int):
            push_update(f"Filtered Facebook output saved ({filtered_count} items).")
        else:
            push_update("Filtered Facebook output saved.")
    if codex_meta and not codex_meta.get("strict_done_met", False):
        push_update("Codex finished with a non-DONE final message, but organized output validated successfully.")
    cex_candidates = int(counts.get("cex_candidates", cex_meta.get("items_checked", 0)))
    cex_groups_matched = int(counts.get("cex_groups_matched", cex_meta.get("groups_matched", 0)))
    cex_groups_scanned = int(cex_meta.get("groups_scanned", 0))
    if cex_groups_scanned > 0:
        push_update(f"CeX scan completed ({cex_groups_scanned} groups scanned).")
    else:
        push_update("CeX scan completed.")
    push_update(f"{cex_candidates} CeX candidates checked.")
    push_update(f"{cex_groups_matched} CeX groups matched.")
    push_update("Saving output files.")
    if files.get("deals_json_path") or files.get("deals_csv_path"):
        push_update("CeX group results output saved.")
    push_update("Scan finished.")

    st.subheader("Scan Summary")
    c1, c2, c3 = st.columns(3)
    c1.metric("Facebook Matches", counts.get("facebook_matches", 0))
    c2.metric("CeX Candidates", counts.get("cex_candidates", 0))
    c3.metric("CeX Groups Matched", counts.get("cex_groups_matched", 0))

    if not results:
        st.info("No Facebook listings matched your filters. Try adjusting query, location, or price range.")
    else:
        st.info(f"{len(results)} Facebook listings matched. Raw results are available as JSON below.")

    st.subheader("Output Files")
    raw_json_path = files.get("raw_facebook_json_path")
    organized_json_path = files.get("organized_facebook_json_path")
    filtered_json_path = files.get("filtered_facebook_json_path")
    deals_json_path = files.get("deals_json_path")
    deals_csv_path = files.get("deals_csv_path")

    if raw_json_path:
        st.write(f"Raw Facebook JSON: {raw_json_path}")
    if organized_json_path:
        st.write(f"Organized Facebook JSON: {organized_json_path}")
    if filtered_json_path:
        st.write(f"Filtered Facebook JSON: {filtered_json_path}")
    if deals_json_path:
        st.write(f"Deals JSON: {deals_json_path}")
    if deals_csv_path:
        st.write(f"Deals CSV: {deals_csv_path}")

    if raw_json_path and Path(raw_json_path).exists():
        with open(raw_json_path, "rb") as jf:
            st.download_button(
                label="Download Raw Facebook JSON",
                data=jf,
                file_name=Path(raw_json_path).name,
                mime="application/json",
            )

    if organized_json_path and Path(organized_json_path).exists():
        with open(organized_json_path, "rb") as of:
            st.download_button(
                label="Download Organized Facebook JSON",
                data=of,
                file_name=Path(organized_json_path).name,
                mime="application/json",
            )

    if filtered_json_path and Path(filtered_json_path).exists():
        with open(filtered_json_path, "rb") as ff:
            st.download_button(
                label="Download Filtered Facebook JSON",
                data=ff,
                file_name=Path(filtered_json_path).name,
                mime="application/json",
            )

    if deals_json_path and Path(deals_json_path).exists():
        with open(deals_json_path, "rb") as djf:
            st.download_button(
                label="Download Deals JSON",
                data=djf,
                file_name=Path(deals_json_path).name,
                mime="application/json",
            )

    if deals_csv_path and Path(deals_csv_path).exists():
        with open(deals_csv_path, "rb") as cf:
            st.download_button(
                label="Download Deals CSV",
                data=cf,
                file_name=Path(deals_csv_path).name,
                mime="text/csv",
            )
