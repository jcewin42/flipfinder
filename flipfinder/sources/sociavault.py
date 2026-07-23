"""
SociaVault source adapter.

Built against SociaVault's published Facebook Marketplace API reference
(https://docs.sociavault.com/api-reference/facebook-marketplace/*) as of
mid-2026:

    GET /v1/scrape/facebook-marketplace/search   (listing search, cursor pagination)
    GET /v1/scrape/facebook-marketplace/item      (full listing detail)

Note: SociaVault's location-search endpoint is intentionally NOT
implemented here -- resolve your lat/lng once by hand (e.g. right-click your
area on Google Maps -> "What's here?") and put it straight into config.yaml.
It's not worth spending API credits on a lookup you only need once.

The item detail endpoint's location field doesn't reliably include
latitude/longitude (see SociaVault's docs sample), but the search endpoint's
listing.location does. main.py backfills a detail's coordinates from its
originating summary for that reason -- see merge_location_from_summary()
in flipfinder/main.py.

Delisting detection does NOT rely on search-result fields (status and
listed_at are confirmed unreliable -- always null in testing despite being
present in the schema) or on absence from search results (SociaVault only
returns the first page or so of results per query, and FB's own ranking mixes
in older "suggested" listings unpredictably, so "not in this page" doesn't
mean "gone"). See check_still_active() for the approach actually used
instead: periodic get_detail() rechecks via flipfinder/pipeline/market_stats.py.

If SociaVault changes their response shape, this is the ONLY file that
should need to change -- that's the point of the SourceAdapter boundary.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import requests

from flipfinder.models import ListingDetail, ListingSummary, Photo, SearchSpec
from flipfinder.sources.base import SearchResult, SourceAdapter

BASE_URL = "https://api.sociavault.com"


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class SociaVaultSource(SourceAdapter):
    name = "sociavault"

    def __init__(
        self,
        api_key: str,
        cost_per_search_call: float = 0.0,
        cost_per_detail_call: float = 0.0,
        timeout: float = 15.0,
    ):
        self.api_key = api_key
        self.cost_per_search_call = cost_per_search_call
        self.cost_per_detail_call = cost_per_detail_call
        self.timeout = timeout
        self._session = requests.Session()
        self._session.headers["x-api-key"] = api_key

    def search(self, spec: SearchSpec, cursor: Optional[str] = None) -> SearchResult:
        params = {
            "query": spec.query,
            "latitude": spec.latitude,
            "longitude": spec.longitude,
            "radius_km": spec.radius_km,
        }
        if spec.price_min is not None:
            params["price_min"] = spec.price_min
        if spec.price_max is not None:
            params["price_max"] = spec.price_max
        if cursor:
            params["cursor"] = cursor

        resp = self._session.get(
            f"{BASE_URL}/v1/scrape/facebook-marketplace/search",
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        listings = []
        for item in data.get("listings", []):
            loc = item.get("location") or {}
            listings.append(
                ListingSummary(
                    id=str(item["id"]),
                    source=self.name,
                    category_id=spec.category_id,
                    title=item.get("title", ""),
                    price=(item.get("price") or {}).get("amount"),
                    url=item.get("url") or f"https://www.facebook.com/marketplace/item/{item['id']}",
                    thumbnail_url=(item.get("primary_photo") or {}).get("url"),
                    posted_at=_parse_dt(item.get("listed_at")),
                    latitude=loc.get("latitude"),
                    longitude=loc.get("longitude"),
                    raw=item,
                )
            )
        return SearchResult(
            listings=listings,
            next_cursor=data.get("cursor") or None,
            total_count=data.get("total_count"),
        )

    def get_detail(self, listing_id: str, category_id: str) -> ListingDetail:
        resp = self._session.get(
            f"{BASE_URL}/v1/scrape/facebook-marketplace/item",
            params={"id": listing_id},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        item = resp.json()

        attributes = {a["label"]: a["value"] for a in item.get("attributes", [])}
        photos = [
            Photo(url=p["url"], width=p.get("width"), height=p.get("height"))
            for p in item.get("photos", [])
        ]
        loc = item.get("location", {})

        return ListingDetail(
            id=str(item["id"]),
            source=self.name,
            category_id=category_id,
            title=item.get("title", ""),
            description=item.get("description", ""),
            price=(item.get("price") or {}).get("amount"),
            url=f"https://www.facebook.com/marketplace/item/{item['id']}",
            photos=photos,
            attributes=attributes,
            seller=item.get("seller", {}),
            location=loc,
            posted_at=_parse_dt(item.get("listed_at")),
            latitude=loc.get("latitude"),   # usually absent on this endpoint; see module docstring
            longitude=loc.get("longitude"),
            raw=item,
        )

    def check_still_active(self, listing_id: str) -> Optional[bool]:
        """
        NOTE: SociaVault's search-result fields for listing status and
        posted-at date are unreliable (confirmed via testing -- always null
        despite being present in the schema), so this can't just inspect
        those fields. The one signal that's likely solid is the item
        endpoint returning a 404/not-found once FB actually removes the
        underlying listing -- that's what this leans on. It ALSO
        speculatively checks a couple of plausible status-ish keys in case
        SociaVault does expose one for a "sold" listing that still 200s;
        verify against your own account's real responses and adjust the
        candidate key list below if it doesn't match what you actually see.
        """
        try:
            resp = self._session.get(
                f"{BASE_URL}/v1/scrape/facebook-marketplace/item",
                params={"id": listing_id},
                timeout=self.timeout,
            )
        except requests.RequestException:
            return None  # network hiccup -- inconclusive, try again later

        if resp.status_code == 404:
            return False
        if resp.status_code >= 500:
            return None  # server-side issue, not evidence either way

        try:
            resp.raise_for_status()
            item = resp.json()
        except (requests.RequestException, ValueError):
            return None

        for key in ("status", "availability", "is_available", "sold"):
            if key in item:
                value = str(item[key]).lower()
                if value in ("sold", "removed", "unavailable", "false", "0"):
                    return False

        return True
