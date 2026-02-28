from datetime import datetime
import os
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from marketplace_deals.cex import scrape_cex_prices
from marketplace_deals.facebook import scrape_facebook_marketplace
from marketplace_deals.ip_info import return_ip_information as get_ip_information
from marketplace_deals.matching import compare_marketplace_vs_cex
from marketplace_deals.storage import save_deals, save_facebook_results
from marketplace_deals.text_utils import resolve_marketplace_slug


def cex_pipeline_enabled() -> bool:
    value = os.getenv("ENABLE_CEX_PIPELINE", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def create_app() -> FastAPI:
    app = FastAPI()
    origins = [
        "http://localhost",
        "http://localhost:8000",
        "http://localhost:3000",
        "http://localhost:8501",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/")
    def root() -> Dict[str, str]:
        return {
            "message": "Facebook Marketplace phone deal finder API is running. Use /find_phone_deals for arbitrage scanning."
        }

    @app.get("/crawl_facebook_marketplace")
    def crawl_facebook_marketplace(
        city: str,
        query: str,
        max_price: float = 0,
        min_price: float = 0,
        spec: str = "",
        max_results: int = 50,
        browser_mode: str = "chrome_persistent",
        browser_profile_dir: str = "",
        radius_km: int = 0,
        sort_by: str = "suggested",
        condition_filters: List[str] = Query(default_factory=list),
        date_listed: str = "all",
    ) -> List[Dict[str, Any]]:
        city_slug = resolve_marketplace_slug(city)
        facebook_results, _ = scrape_facebook_marketplace(
            city_slug,
            query,
            spec,
            min_price,
            max_price,
            max_results,
            interactive_browser=False,
            browser_mode=browser_mode,
            browser_profile_dir=browser_profile_dir,
            radius_km=radius_km,
            sort_by=sort_by,
            condition_filters=condition_filters,
            date_listed=date_listed,
        )
        return facebook_results

    @app.get("/find_phone_deals")
    def find_phone_deals(
        city: str,
        query: str,
        max_price: float = 0,
        min_price: float = 0,
        spec: str = "",
        max_results: int = 50,
        interactive_browser: bool = True,
        manual_login_timeout: int = 0,
        browser_mode: str = "chrome_persistent",
        browser_profile_dir: str = "",
        radius_km: int = 0,
        sort_by: str = "suggested",
        condition_filters: List[str] = Query(default_factory=list),
        date_listed: str = "all",
    ) -> Dict[str, Any]:
        if min_price < 0:
            raise HTTPException(400, "min_price must be >= 0")
        if max_price < 0:
            raise HTTPException(400, "max_price must be >= 0")
        if max_price > 0 and min_price > max_price:
            raise HTTPException(400, "min_price cannot be greater than max_price")
        if not query.strip():
            raise HTTPException(400, "query is required")

        city_slug = resolve_marketplace_slug(city)
        facebook_results, scan_meta = scrape_facebook_marketplace(
            city_slug=city_slug,
            query=query,
            spec=spec,
            min_price=min_price,
            max_price=max_price,
            max_results=max_results,
            interactive_browser=interactive_browser,
            manual_login_timeout=manual_login_timeout,
            browser_mode=browser_mode,
            browser_profile_dir=browser_profile_dir,
            radius_km=radius_km,
            sort_by=sort_by,
            condition_filters=condition_filters,
            date_listed=date_listed,
        )

        is_cex_enabled = cex_pipeline_enabled()
        cex_results: List[Dict[str, Any]] = []
        opportunities: List[Dict[str, Any]] = []

        if is_cex_enabled:
            cex_results = scrape_cex_prices(
                search_text=f"{query} {spec}".strip(),
                browser_mode=browser_mode,
                browser_profile_dir=browser_profile_dir,
                interactive_browser=interactive_browser,
                challenge_timeout=manual_login_timeout,
            )
            opportunities = compare_marketplace_vs_cex(facebook_results, cex_results)
            saved_files = save_deals(query, city_slug, opportunities)
        else:
            saved_files = save_facebook_results(query, city_slug, facebook_results)

        return {
            "search": {
                "city": city,
                "city_slug": city_slug,
                "query": query,
                "spec": spec,
                "min_price": min_price,
                "max_price": max_price,
                "max_results": max_results,
                "radius_km": radius_km,
                "sort_by": sort_by,
                "condition_filters": condition_filters,
                "date_listed": date_listed,
                "searched_at_utc": datetime.utcnow().isoformat() + "Z",
            },
            "pipeline": {
                "cex_enabled": is_cex_enabled,
                "cex_note": (
                    "CeX scraping and comparison are temporarily deactivated."
                    if not is_cex_enabled
                    else "CeX pipeline active."
                ),
            },
            "counts": {
                "facebook_matches": len(facebook_results),
                "cex_candidates": len(cex_results),
                "profitable_deals": len(opportunities),
            },
            "scan_meta": scan_meta,
            "files": saved_files,
            "results": facebook_results,
            "opportunities": opportunities,
        }

    @app.get("/return_ip_information")
    def return_ip_information() -> Dict[str, str]:
        return get_ip_information()

    return app


app = create_app()
