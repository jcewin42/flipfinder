"""
Source adapter interface.

This is THE modularity boundary for where listings come from. SociaVault is
one implementation. A future self-hosted monitor is another. Nothing outside
this file's contract should know or care which one is active -- the pipeline
only ever talks to a SourceAdapter.

To add a new source: subclass SourceAdapter, implement search() and
get_detail(), register it in flipfinder/sources/__init__.py, and reference it
by name in config. That's the whole integration surface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from flipfinder.models import ListingDetail, ListingSummary, SearchSpec


@dataclass
class SearchResult:
    listings: list[ListingSummary]
    next_cursor: Optional[str]           # None/absent means "no more pages"
    total_count: Optional[int] = None


class SourceAdapter(ABC):
    """
    Implementations should be cheap to construct and safe to reuse across
    many calls (e.g. hold a requests.Session, not open a new connection
    per call).
    """

    #: Human-readable name, used in logs and stored against every listing.
    name: str = "unnamed-source"

    #: Rough $ cost of a single search() call on this source (SociaVault
    #: charges a credit per search regardless of how many results it
    #: returns). 0.0 for a self-hosted monitor. Category profiles can use
    #: this to decide how many separate queries to run per poll -- see
    #: OutboardMotorProfile's search_strategy.
    cost_per_search_call: float = 0.0

    #: Rough $ cost of a single get_detail() call on this source. 0.0 for a
    #: self-hosted monitor. This lets stage 1 filtering decide how
    #: aggressive to be -- expensive sources filter harder, free sources can
    #: afford to let more through.
    cost_per_detail_call: float = 0.0

    @abstractmethod
    def search(self, spec: SearchSpec, cursor: Optional[str] = None) -> SearchResult:
        """
        Return one page of lightweight listing summaries matching spec.
        Callers handle pagination by passing next_cursor back in on the next
        call, until next_cursor is None.
        """
        raise NotImplementedError

    @abstractmethod
    def get_detail(self, listing_id: str, category_id: str) -> ListingDetail:
        """Return full detail for a single listing."""
        raise NotImplementedError

    @abstractmethod
    def check_still_active(self, listing_id: str) -> Optional[bool]:
        """
        Return True if the listing still appears live, False if it looks
        gone (sold/removed), or None if the check was inconclusive (e.g. a
        transient network error) -- callers should treat None as "don't know
        yet, try again later" rather than evidence of delisting.

        On most sources this costs the same as get_detail() since that's the
        only per-item signal available (see cost_per_detail_call) -- but it's
        its own method rather than reusing get_detail() directly because a
        source MAY have a cheaper way to check liveness than a full detail
        fetch, and callers shouldn't need to know which.
        """
        raise NotImplementedError
