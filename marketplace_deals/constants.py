import re

# Friendly names mapped to marketplace location slugs.
LOCATION_SLUGS = {
    "new york": "nyc",
    "los angeles": "la",
    "chicago": "chicago",
    "houston": "houston",
    "las vegas": "vegas",
    "orlando": "orlando",
    "miami": "miami",
    "seattle": "seattle",
    "san francisco": "sanfrancisco",
    "london": "london",
    "manchester": "manchester",
    "birmingham": "birmingham",
    "glasgow": "glasgow",
    "liverpool": "liverpool",
    "leeds": "leeds",
    "bristol": "bristol",
    "edinburgh": "edinburgh",
    "sheffield": "sheffield",
    "leicester": "leicester",
    "nottingham": "nottingham",
}

PRICE_PATTERN = re.compile(r"(?:[$£€]\s*)?(\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)")
MARKETPLACE_ITEM_PATTERN = re.compile(r"/marketplace/item/(\d+)")

STOP_WORDS = {
    "the",
    "and",
    "or",
    "for",
    "with",
    "gb",
    "phone",
    "mobile",
    "new",
    "used",
    "unlocked",
    "good",
    "condition",
}

SUPPORTED_BROWSER_MODES = {"chromium", "chrome", "chrome_persistent"}

