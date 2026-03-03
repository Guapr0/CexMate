import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from fastapi import HTTPException

from marketplace_deals.constants import LOCATION_SLUGS, PRICE_PATTERN, STOP_WORDS

ALLOWED_SORTS = {
    "suggested",
    "distance_ascend",
    "creation_time_descend",
    "price_ascend",
    "price_descend",
}
ALLOWED_CONDITIONS = {
    "new",
    "used_like_new",
    "used_good",
    "used_fair",
}
ALLOWED_DATE_LISTED = {
    "all",
    "1",
    "7",
    "30",
}
ALLOWED_RADIUS_KM = [1, 2, 5, 10, 20, 40, 60, 100, 250, 500]

CURRENCY_PRICE_PATTERN = re.compile(
    r"[£$€�]\s*(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
)


def parse_price(raw_value: Any, require_currency: bool = False) -> Optional[float]:
    if raw_value is None:
        return None

    text = str(raw_value).strip().lower()
    if not text:
        return None
    if "free" in text:
        return 0.0
    if require_currency and not any(symbol in text for symbol in ("£", "$", "€", "�")):
        return None

    match = PRICE_PATTERN.search(text.replace("\u00a0", " "))
    if not match:
        return None

    number = match.group(1).replace(",", "").replace(" ", "")
    try:
        return float(number)
    except ValueError:
        return None


def extract_prices(raw_value: Any, require_currency: bool = False) -> List[float]:
    if raw_value is None:
        return []

    text = str(raw_value).strip().lower().replace("\u00a0", " ")
    if not text:
        return []

    values: List[float] = []
    if require_currency:
        matches = CURRENCY_PRICE_PATTERN.finditer(text)
    else:
        matches = PRICE_PATTERN.finditer(text)

    for match in matches:
        number = match.group(1).replace(",", "").replace(" ", "")
        try:
            values.append(float(number))
        except ValueError:
            continue
    return values


def parse_best_price(raw_value: Any, require_currency: bool = False) -> Optional[float]:
    values = extract_prices(raw_value, require_currency=require_currency)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    # Discounted cards often include old+new prices. Use the lower one.
    return min(values)


def normalize_text(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def tokenize(value: str) -> List[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value.lower()) if token]


def smart_match(candidate: str, query: str, spec: str) -> bool:
    candidate_norm = normalize_text(candidate)
    if not candidate_norm:
        return False

    target = f"{query} {spec}".strip()
    target_norm = normalize_text(target)
    if target_norm and target_norm in candidate_norm:
        return True

    target_tokens = [
        token
        for token in tokenize(target)
        if token not in STOP_WORDS and len(token) > 1
    ]
    if not target_tokens:
        return True

    candidate_tokens = set(tokenize(candidate))
    hits = sum(1 for token in target_tokens if token in candidate_tokens)
    token_ratio = hits / len(target_tokens)
    fuzzy_ratio = SequenceMatcher(None, candidate_norm, " ".join(target_tokens)).ratio()
    return token_ratio >= 0.4 or fuzzy_ratio >= 0.55


def similarity_score(a: str, b: str) -> float:
    a_norm = normalize_text(a)
    b_norm = normalize_text(b)
    if not a_norm or not b_norm:
        return 0.0

    a_tokens = {t for t in tokenize(a_norm) if t not in STOP_WORDS}
    b_tokens = {t for t in tokenize(b_norm) if t not in STOP_WORDS}

    overlap = 0.0
    if b_tokens:
        overlap = len(a_tokens & b_tokens) / len(b_tokens)

    seq = SequenceMatcher(None, a_norm, b_norm).ratio()
    return max(seq, overlap)


def resolve_marketplace_slug(city: str) -> str:
    city_clean = city.strip().lower()
    if not city_clean:
        raise HTTPException(400, "city/location is required")

    if city_clean in LOCATION_SLUGS:
        return LOCATION_SLUGS[city_clean]

    slug = re.sub(r"[^a-z0-9-]", "", city_clean.replace(" ", ""))
    if not slug:
        raise HTTPException(400, "Invalid city/location value")
    return slug


def normalize_sort_by(sort_by: str) -> str:
    sort_value = (sort_by or "suggested").strip().lower()
    if sort_value not in ALLOWED_SORTS:
        raise HTTPException(
            400,
            "Invalid sort_by value. Use one of: suggested, distance_ascend, creation_time_descend, "
            "price_ascend, price_descend.",
        )
    return sort_value


def normalize_condition_filters(condition_filters: List[str]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for raw in condition_filters or []:
        value = (raw or "").strip().lower()
        if not value:
            continue
        if value not in ALLOWED_CONDITIONS:
            raise HTTPException(
                400,
                "Invalid condition filter. Use any of: new, used_like_new, used_good, used_fair.",
            )
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def normalize_date_listed(date_listed: str) -> str:
    value = (date_listed or "all").strip().lower()
    if value not in ALLOWED_DATE_LISTED:
        raise HTTPException(
            400,
            "Invalid date_listed value. Use one of: all, 1, 7, 30.",
        )
    return value


def normalize_radius_km(radius_km: int) -> int:
    try:
        requested = int(radius_km)
    except (TypeError, ValueError):
        return 0

    if requested <= 0:
        return 0
    if requested in ALLOWED_RADIUS_KM:
        return requested

    # Facebook Marketplace supports discrete radius values; use nearest supported value.
    return min(ALLOWED_RADIUS_KM, key=lambda option: abs(option - requested))


def _looks_like_recency(line: str) -> bool:
    value = (line or "").strip().lower()
    if not value:
        return False

    markers = [
        "just now",
        "yesterday",
        "today",
        "ago",
        "minute",
        "minutes",
        "min",
        "mins",
        "hour",
        "hours",
        "hr",
        "hrs",
        "day",
        "days",
        "week",
        "weeks",
        "month",
        "months",
        "year",
        "years",
    ]
    return any(marker in value for marker in markers)


def _line_has_letters(line: str) -> bool:
    return bool(re.search(r"[a-zA-Z]", line or ""))


def _looks_like_price_only_line(line: str) -> bool:
    value = (line or "").strip()
    if not value:
        return False
    if not extract_prices(value, require_currency=True):
        return False

    stripped = CURRENCY_PRICE_PATTERN.sub(" ", value)
    stripped = re.sub(r"[\d\s,.\-/()|:]", " ", stripped)
    return not _line_has_letters(stripped)


def parse_facebook_card_text(raw_text: str) -> Tuple[str, str, str, str, str]:
    lines = [line.strip() for line in (raw_text or "").splitlines() if line.strip()]
    if not lines:
        return "", "", "", "", ""

    title = ""
    description = ""
    price_text = ""
    location = ""
    recency = ""
    consumed_indexes = set()

    for index, line in enumerate(lines):
        if not price_text and _looks_like_price_only_line(line):
            price_text = line
            consumed_indexes.add(index)
            continue
        if not title and _line_has_letters(line) and not _looks_like_recency(line):
            title = line
            consumed_indexes.add(index)
            continue
        if not recency and _looks_like_recency(line):
            recency = line
            consumed_indexes.add(index)

    if not title:
        for index, line in enumerate(lines):
            if index in consumed_indexes:
                continue
            if _looks_like_recency(line):
                continue
            if _looks_like_price_only_line(line):
                continue
            title = line
            consumed_indexes.add(index)
            break

    if not price_text:
        for index, line in enumerate(lines):
            if parse_best_price(line, require_currency=True) is not None:
                price_text = line
                consumed_indexes.add(index)
                break

    location_index = len(lines) - 1
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if index in consumed_indexes:
            continue
        if _looks_like_recency(line):
            continue
        if _looks_like_price_only_line(line):
            continue
        location_index = index
        break

    if lines:
        location = lines[location_index]
        consumed_indexes.add(location_index)

    description_parts = [
        line
        for index, line in enumerate(lines)
        if index not in consumed_indexes
    ]
    if description_parts:
        description = " | ".join(description_parts)

    return title, description, price_text, location, recency


def build_marketplace_url(
    city_slug: str,
    query: str,
    min_price: float,
    max_price: float,
    radius_km: int = 0,
    sort_by: str = "suggested",
    condition_filters: List[str] | None = None,
    date_listed: str = "all",
) -> str:
    sort_value = normalize_sort_by(sort_by)
    conditions = normalize_condition_filters(condition_filters or [])
    date_value = normalize_date_listed(date_listed)
    radius_value = normalize_radius_km(radius_km)

    query_params: List[Tuple[str, str]] = [("query", query), ("exact", "false")]
    if min_price > 0:
        query_params.append(("minPrice", str(int(min_price))))
    if max_price > 0:
        query_params.append(("maxPrice", str(int(max_price))))
    if radius_value > 0:
        query_params.append(("radiusKM", str(radius_value)))
    if sort_value != "suggested":
        query_params.append(("sortBy", sort_value))
    if conditions:
        query_params.append(("itemCondition", ",".join(conditions)))
    if date_value != "all":
        query_params.append(("daysSinceListed", date_value))

    encoded = urlencode(query_params, doseq=True)
    return f"https://www.facebook.com/marketplace/{city_slug}/search/?{encoded}"


def dedupe_items_by_name_price(items: List[Dict[str, Any]], max_results: int) -> List[Dict[str, Any]]:
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for item in items:
        key = (normalize_text(str(item.get("name", ""))), float(item.get("price", 0.0)))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= max_results:
            break
    return deduped
