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

Confirmed via live testing against a real account (contradicts the
published docs sample, and an earlier -- wrong -- assumption in this file):
every response is wrapped in a {success, data: {...}, credits_used}
envelope, and `data.listings`/`data.photos`/`data.attributes` are all dicts
keyed by string index ("0", "1", ...), not JSON arrays. Also, it's the
DETAIL endpoint whose location field reliably includes latitude/longitude
-- search results never do (location there is just city/state/display
name). main.py's merge_location_from_summary() is kept as a harmless
no-op-in-practice safety net, not because it's still needed the way it was
originally written.

Delisting detection does NOT rely on search-result fields (status and
listed_at don't exist in the real response at all, despite being in
SociaVault's published schema) or on absence from search results
(SociaVault only returns the first page or so of results per query, and
FB's own ranking mixes in older "suggested" listings unpredictably, so "not
in this page" doesn't mean "gone"). See check_still_active() for the
approach actually used instead: periodic get_detail() rechecks via
flipfinder/pipeline/market_stats.py -- which can now lean on the real
is_sold/is_live/is_hidden/is_pending booleans confirmed present on both
search and detail responses, not just the 404 case.

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
            "lat": spec.latitude,
            "lng": spec.longitude,
            "radius_km": spec.radius_km,
            "sort_by": "creation_time_descend",
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
        envelope = resp.json()
        # Real response shape: {success, data: {success, credits_charged,
        # listings, cursor, has_next_page}, credits_used}. `listings` itself
        # is a dict keyed by string index ("0", "1", ...), not a JSON array --
        # confirmed via live testing, differs from the published docs sample.
        data = envelope.get("data") or {}

        listings = []
        for item in data.get("listings", {}).values():
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
                    posted_at=_parse_dt(item.get("creation_time")),   # null on search results in practice; see README
                    latitude=loc.get("latitude"),   # location has no lat/lng at all in practice -- see README
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
        envelope = resp.json()
        # Same {success, data: {...}, credits_used} envelope as search() --
        # confirmed via live testing, item fields live under `data`.
        item = envelope.get("data") or envelope

        attributes = {a["label"]: a["value"] for a in item.get("attributes", {}).values()}
        photos = [
            Photo(url=p["url"], width=p.get("width"), height=p.get("height"))
            for p in item.get("photos", {}).values()
        ]
        loc = item.get("location") or {}

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
            seller=item.get("seller") or {},
            location=loc,
            posted_at=_parse_dt(item.get("creation_time")),
            latitude=loc.get("latitude"),   # reliable on this endpoint -- see module docstring
            longitude=loc.get("longitude"),
            raw=item,
        )

    def check_still_active(self, listing_id: str) -> Optional[bool]:
        """
        Confirmed via live testing: the status/posted-at fields SociaVault's
        published schema advertises on SEARCH results (status, listed_at)
        don't exist there at all. The item DETAIL endpoint is different --
        it reliably returns is_hidden/is_live/is_pending/is_sold booleans
        (real fields, not a guess), on top of a 404 once FB fully removes
        the listing. is_pending (sale in progress but not yet confirmed) is
        deliberately not treated as inactive -- the listing is still up.
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
            envelope = resp.json()
        except (requests.RequestException, ValueError):
            return None

        item = envelope.get("data") or envelope

        if item.get("is_sold") or item.get("is_hidden"):
            return False
        if item.get("is_live") is False:
            return False

        return True
