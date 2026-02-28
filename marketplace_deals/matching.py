from typing import Any, Dict, List, Tuple

from marketplace_deals.text_utils import similarity_score


def compare_marketplace_vs_cex(
    facebook_results: List[Dict[str, Any]], cex_results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    opportunities: List[Dict[str, Any]] = []

    for listing in facebook_results:
        listing_title = listing["title"]
        listing_price = listing["price_value"]

        matches: List[Tuple[float, Dict[str, Any]]] = []
        for cex_item in cex_results:
            score = similarity_score(listing_title, cex_item["name"])
            if score >= 0.45:
                matches.append((score, cex_item))

        if not matches:
            continue

        # Prioritize highest CeX price among reasonable matches.
        best_score, best_cex = max(matches, key=lambda pair: pair[1]["price"])
        if listing_price >= best_cex["price"]:
            continue

        profit = round(best_cex["price"] - listing_price, 2)
        margin_pct = round((profit / listing_price) * 100, 2) if listing_price > 0 else None

        opportunities.append(
            {
                "facebook_title": listing_title,
                "facebook_price": listing_price,
                "facebook_price_text": listing["price_text"],
                "facebook_location": listing["location"],
                "facebook_link": listing["link"],
                "facebook_image": listing["image"],
                "cex_title": best_cex["name"],
                "cex_price": best_cex["price"],
                "cex_link": best_cex["url"],
                "match_score": round(best_score, 3),
                "estimated_profit": profit,
                "margin_percent": margin_pct,
            }
        )

    opportunities.sort(key=lambda row: row["estimated_profit"], reverse=True)
    return opportunities

