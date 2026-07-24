"""
Entry point. Wires everything together from config.yaml:

  sources <-> categories <-> pipeline (stage1 -> stage2 -> offer) <-> notifier
                                 ^              ^          ^
                          routing backend  inference   lifecycle tracking
                                            backend     (delisting checks)

Nothing in this file is category- or source-specific -- add a new category
or source by registering it (flipfinder/categories/__init__.py,
flipfinder/sources/__init__.py) and adding a block to config.yaml. This file
shouldn't need to change.

Usage:
    python -m flipfinder.main                              # normal long-running mode (Discord + scheduler)
    python -m flipfinder.main --once                        # single poll cycle, all categories, prints to console
    python -m flipfinder.main --once --category outboard_motors
    python -m flipfinder.main --once --discord               # also actually deliver these alerts to Discord
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime

from dotenv import load_dotenv

from flipfinder.categories import build_category
from flipfinder.config import load_config
from flipfinder.db import Database
from flipfinder.inference import build_backend
from flipfinder.logging_config import setup_logging
from flipfinder.notifier.console import ConsoleNotifier
from flipfinder.pipeline import compute_offer, evaluate_listing, should_alert
from flipfinder.pipeline import market_stats as market_stats_mod
from flipfinder.pipeline.feedback_store import FeedbackStore
from flipfinder.pipeline.stage1_filter import passes_stage1
from flipfinder.routing import build_routing_backend
from flipfinder.scheduler import ScheduleConfig, Scheduler
from flipfinder.sources import build_source

# Real listing titles routinely contain non-ASCII characters (smart quotes,
# en/em dashes, emoji). A headless host isn't guaranteed to have a UTF-8
# locale (e.g. LANG=en_US rather than en_US.UTF-8 makes Python default
# stdout/stderr to Latin-1) -- confirmed to actually crash a poll cycle on
# this project's own Pi. Force UTF-8 regardless of host locale rather than
# depending on systemd/shell environment setup to get this right.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

logger = logging.getLogger("flipfinder.main")


def build_app(config: dict):
    db = Database(config["database"]["path"])
    feedback_store = FeedbackStore(db)

    backend_name = config["inference"]["backend"]
    inference_backend = build_backend(backend_name, **config["inference"].get(backend_name, {}))

    routing_cfg = config.get("routing", {"backend": "haversine"})
    routing_backend_name = routing_cfg.get("backend", "haversine")
    routing_backend = build_routing_backend(routing_backend_name, **routing_cfg.get(routing_backend_name, {}))

    sources = {
        name: build_source(name, **cfg)
        for name, cfg in config.get("sources", {}).items()
    }

    location = config["location"]
    categories = {}
    schedules = {}
    for category_id, cat_cfg in config["categories"].items():
        categories[category_id] = build_category(
            category_id,
            latitude=location["latitude"],
            longitude=location["longitude"],
            radius_km=location["radius_km"],
            base_service_cost=cat_cfg["base_service_cost"],
            base_service_hours=cat_cfg.get("base_service_hours", 1.5),
            price_min=cat_cfg.get("price_min"),
            price_max=cat_cfg.get("price_max"),
            search_strategy=cat_cfg.get("search_strategy", "broad"),
            image_count=cat_cfg.get("image_count", 3),
        )
        schedules[category_id] = ScheduleConfig(**cat_cfg["schedule"])

    return db, feedback_store, inference_backend, routing_backend, sources, categories, schedules


def merge_location_from_summary(detail, summary) -> None:
    """Some sources' detail endpoint omits coordinates that their search
    endpoint provides (see sources/sociavault.py) -- backfill from the
    summary that led us here rather than losing the data."""
    if detail.latitude is None:
        detail.latitude = summary.latitude
    if detail.longitude is None:
        detail.longitude = summary.longitude


async def run_poll_cycle(
    category_id: str,
    config: dict,
    db: Database,
    feedback_store: FeedbackStore,
    inference_backend,
    routing_backend,
    sources: dict,
    categories: dict,
    notifier,
) -> dict:
    cat_cfg = config["categories"][category_id]
    location = config["location"]
    category = categories[category_id]
    source = sources[cat_cfg["source"]]

    min_hourly_rate = cat_cfg.get("alert_min_hourly_rate", 20.0)
    item_count_confidence_threshold = cat_cfg.get("item_count_confidence_threshold", 0.6)
    require_photo = cat_cfg.get("require_photo", False)
    max_search_pages = cat_cfg.get("max_search_pages", 3)
    travel_time_basis = cat_cfg.get("travel_time_basis", config.get("routing", {}).get("travel_time_basis", "peak"))
    selling_overhead_hours = cat_cfg.get("selling_overhead_hours", 0.5)

    lifecycle_cfg = cat_cfg.get("lifecycle_tracking", {})
    lifecycle_enabled = lifecycle_cfg.get("enabled", True)
    max_lifecycle_checks = lifecycle_cfg.get("max_checks_per_poll", 10)
    lifecycle_backoff_days = lifecycle_cfg.get("recheck_backoff_days", [1, 2, 4, 7, 14])
    lifecycle_max_tracking_days = lifecycle_cfg.get("max_tracking_days", 45)
    lifecycle_first_check_delay = lifecycle_cfg.get("first_check_delay_days", 1)

    started = datetime.now()
    started_iso = started.isoformat()
    counts = dict(listings_seen=0, new_listings=0, passed_stage1=0, detail_calls_made=0, alerts_sent=0)
    lifecycle_result = {"checked": 0, "delisted": 0}
    routing_calls_made = 0
    error = None

    try:
        for spec in category.search_specs():
            cursor = None
            pages_fetched = 0
            while True:
                result = await asyncio.to_thread(source.search, spec, cursor)
                pages_fetched += 1
                counts["listings_seen"] += len(result.listings)

                # Pages are sorted newest-first (sort_by=creation_time_descend).
                # Capture whether the oldest listing on THIS page was already
                # known BEFORE the loop below marks anything processed: if so,
                # everything past it should already be known too, and paying
                # for another page would be pure waste. If it's still new, we
                # might be missing listings created since our last poll that
                # didn't fit on this page -- worth fetching the next one.
                caught_up = bool(result.listings) and db.has_processed(result.listings[-1].id, source.name)

                for summary in result.listings:
                    if db.has_processed(summary.id, source.name):
                        continue
                    counts["new_listings"] += 1

                    passed = passes_stage1(summary, category, require_photo)
                    db.mark_processed(
                        summary.id, source.name, category.category_id, summary.title,
                        summary.price, summary.url, passed,
                    )
                    if not passed:
                        continue
                    counts["passed_stage1"] += 1

                    detail = await asyncio.to_thread(source.get_detail, summary.id, category.category_id)
                    merge_location_from_summary(detail, summary)
                    counts["detail_calls_made"] += 1

                    estimate = evaluate_listing(detail, category, feedback_store, inference_backend, db)

                    travel = await asyncio.to_thread(
                        routing_backend.estimate_round_trip,
                        location["latitude"], location["longitude"], detail.latitude, detail.longitude,
                    )
                    routing_calls_made += travel.api_calls

                    # TEMP-COMPARISON: delete this block + flipfinder/routing/temp_comparison_logger.py
                    # once you've validated haversine accuracy against real Google Routes data.
                    if config.get("routing", {}).get("log_comparison", False):
                        from flipfinder.routing.temp_comparison_logger import log_comparison
                        routing_calls_made += await asyncio.to_thread(
                            log_comparison, config, detail.title,
                            location["latitude"], location["longitude"], detail.latitude, detail.longitude,
                        )

                    offer = compute_offer(
                        detail, estimate, category.base_service_cost, category.base_service_hours,
                        travel, travel_time_basis, selling_overhead_hours,
                        cat_cfg.get("min_profit_flat", 75.0), cat_cfg.get("min_profit_pct", 0.20),
                    )
                    features = category.feature_vector(detail)
                    db.record_estimate(
                        detail.id, source.name, category.category_id, features,
                        estimate.estimated_resale_value, estimate.estimated_repair_cost,
                        estimate.estimated_repair_hours, estimate.estimated_item_count,
                        estimate.confidence, estimate.reasoning,
                        estimate.raw_response, offer.max_offer, offer.pickup_travel_hours,
                        offer.pickup_travel_hours_peak, offer.pickup_travel_hours_offpeak, offer.traffic_aware,
                        offer.service_hours, offer.total_time_hours, offer.estimated_hourly_rate,
                    )

                    if lifecycle_enabled:
                        market_stats_mod.register_for_tracking(
                            db, detail.id, source.name, category.category_id, detail.price,
                            started_iso, lifecycle_first_check_delay,
                        )

                    logger.info(
                        "%s: resale=$%.0f repair=$%.0f/%.1fh rate=$%.0f/hr conf=%.0f%%%s",
                        detail.title, estimate.estimated_resale_value, estimate.estimated_repair_cost,
                        estimate.estimated_repair_hours, offer.estimated_hourly_rate, estimate.confidence * 100,
                        f" [{estimate.estimated_item_count} units]" if estimate.estimated_item_count > 1 else "",
                    )

                    if should_alert(
                        offer.estimated_hourly_rate, estimate.confidence, estimate.item_count_confidence,
                        min_hourly_rate, item_count_confidence_threshold,
                    ):
                        needs_confirmation = estimate.item_count_confidence < item_count_confidence_threshold
                        await notifier.send_alert(detail, estimate, offer, needs_confirmation)
                        counts["alerts_sent"] += 1

                if caught_up or not result.listings or not result.next_cursor:
                    break
                if pages_fetched >= max_search_pages:
                    logger.warning(
                        "%s/%s: hit max_search_pages=%d while still seeing only-new listings on "
                        "the last page -- some listings newer than our last poll may be missed "
                        "this cycle; raise max_search_pages if this happens often",
                        category.category_id, spec.query, max_search_pages,
                    )
                    break
                cursor = result.next_cursor

        if lifecycle_enabled:
            lifecycle_result = await asyncio.to_thread(
                market_stats_mod.run_due_lifecycle_checks,
                db, source, category.category_id, started_iso,
                max_lifecycle_checks, lifecycle_backoff_days, lifecycle_max_tracking_days,
            )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        logger.exception("Poll cycle failed for %s", category_id)
    finally:
        duration_ms = int((datetime.now() - started).total_seconds() * 1000)
        db.record_poll(
            category_id=category_id,
            source=cat_cfg["source"],
            started_at=started_iso,
            duration_ms=duration_ms,
            lifecycle_checks_made=lifecycle_result["checked"],
            lifecycle_newly_delisted=lifecycle_result["delisted"],
            routing_calls_made=routing_calls_made,
            error=error,
            **counts,
        )

    return {**counts, **{f"lifecycle_{k}": v for k, v in lifecycle_result.items()}}


async def run_long_running(config: dict) -> None:
    from flipfinder.notifier.discord_bot import FlipFinderBot  # local import: only needed here

    db, feedback_store, inference_backend, routing_backend, sources, categories, schedules = build_app(config)
    notifier = FlipFinderBot(db=db, feedback_store=feedback_store, channel_id=config["discord"]["channel_id"])

    async def on_poll(category_id: str) -> dict:
        return await run_poll_cycle(
            category_id, config, db, feedback_store, inference_backend, routing_backend,
            sources, categories, notifier,
        )

    scheduler = Scheduler(schedules, on_poll)

    async def start_scheduler_after_discord_ready() -> None:
        await notifier.wait_until_ready()
        logger.info("Discord ready, starting scheduler")
        await scheduler.run_forever()

    await asyncio.gather(
        notifier.start(config["discord"]["bot_token"]),
        start_scheduler_after_discord_ready(),
    )


async def run_once(config: dict, category_ids: list[str], use_discord: bool) -> None:
    db, feedback_store, inference_backend, routing_backend, sources, categories, schedules = build_app(config)

    async def _poll_all(notifier) -> None:
        for category_id in category_ids:
            logger.info("One-shot poll: %s", category_id)
            counts = await run_poll_cycle(
                category_id, config, db, feedback_store, inference_backend, routing_backend,
                sources, categories, notifier,
            )
            logger.info("%s: %s", category_id, counts)

    if use_discord:
        from flipfinder.notifier.discord_bot import FlipFinderBot  # local import: only needed here

        notifier = FlipFinderBot(db=db, feedback_store=feedback_store, channel_id=config["discord"]["channel_id"])

        async def _run() -> None:
            await notifier.wait_until_ready()
            await _poll_all(notifier)
            await notifier.close()

        await asyncio.gather(notifier.start(config["discord"]["bot_token"]), _run())
    else:
        await _poll_all(ConsoleNotifier())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="flipfinder")
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit, instead of the long-running scheduler+bot.")
    parser.add_argument("--category", action="append", help="Limit --once to this category (repeatable). Default: all configured categories.")
    parser.add_argument("--discord", action="store_true", help="With --once, actually deliver alerts to Discord instead of printing to console.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    setup_logging(level=args.log_level)
    load_dotenv()
    config = load_config(args.config)

    if args.once:
        category_ids = args.category or list(config["categories"].keys())
        await run_once(config, category_ids, use_discord=args.discord)
    else:
        await run_long_running(config)


if __name__ == "__main__":
    asyncio.run(main())
