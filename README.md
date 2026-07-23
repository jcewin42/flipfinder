# flipfinder

Notifies you about flip-worthy Marketplace listings, filters out the noise,
estimates resale value, repair cost, and repair time, tells you what to
offer and what $/hour you can expect to make, and learns from your feedback
and from local market timing over time.

## Architecture

```
Source adapter (SociaVault, future: own monitor)
        |
Scheduler (polls sources, logs every event)
        |
Stage 1: quick filter (straight-line distance + cheap, source-agnostic screening)
        |
Stage 2: valuation (detail fetch + AI estimate, uses category profile + inference backend)
        |         ^                                    ^
        |    feedback store                     market-stats (time-on-market)
        |
Routing backend (real traffic-aware or free straight-line -- peak + off-peak)
        |
Offer & $/hour math (category profile + travel time drives the math)
        |
Notify you (Discord, or console for one-shot testing)
        |
Feedback store (your corrections) --loops back into--> Stage 2 valuation
        |
Lifecycle tracking (periodic get_detail rechecks) --feeds--> market-stats
```

Four things are pluggable without touching the pipeline itself:

- **Source** (`flipfinder/sources/`) -- where listings come from. SociaVault today.
- **Category** (`flipfinder/categories/`) -- what you're flipping. Outboard motors today.
- **Inference backend** (`flipfinder/inference/`) -- what answers "value this listing". The Jetson today.
- **Routing backend** (`flipfinder/routing/`) -- what answers "how long is the drive". Free straight-line estimate by default, real traffic-aware routing (Google Routes API) optionally.

## What each listing gets evaluated on

For every listing that passes stage 1, you get:

- **Estimated resale value** after your standard service (calibrated against similar past feedback and local time-on-market data, once enough exists)
- **Estimated additional repair cost and hours** beyond your standard service
- **Round-trip pickup time at both peak and off-peak traffic** (see "Real routing and traffic" below)
- **Total time investment** (pickup + your standard service + estimated extra repair + a small selling-overhead allowance)
- **Suggested max offer** and **profit if bought at asking price**
- **Estimated $/hour** -- the primary number alerts are sorted/filtered on. This is what naturally captures "worthwhile at 30 minutes away, not worthwhile at 2 hours away" for the exact same listing and profit, without needing a separate distance judgment call.

## Setup

### Option A: plain venv

```bash
cd flipfinder
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env
```

### Option B: Docker (Pi side only)

```bash
cd flipfinder
cp config.example.yaml config.yaml
cp .env.example .env
docker compose up -d --build
docker compose logs -f
```

`data/` and `logs/` are bind-mounted so they survive container rebuilds. `restart: unless-stopped` means it comes back up after a Pi reboot without a separate systemd unit.

The Jetson side (`jetson_service/`) is NOT dockerized -- see "Jetson side" below for why.

Either way, fill in `.env`:
```
DISCORD_BOT_TOKEN=...
SOCIAVAULT_API_KEY=...
GOOGLE_ROUTES_API_KEY=...   # only needed if routing.backend: google_routes
```

Get your SociaVault key at https://sociavault.com/signup (free tier: 100 requests/day -- note both search calls AND item-detail calls count against this, see "Tuning search cost" below).

Unresolved `${ENV_VAR}` placeholders only cause problems for whichever backend is actually selected in config.yaml -- an unset `GOOGLE_ROUTES_API_KEY` is harmless while `routing.backend: haversine` is selected, for instance. You'll get a warning in the logs either way.

### Discord bot

1. Create an application + bot at https://discord.com/developers/applications
2. Under **Bot**, enable the **Message Content Intent** (required -- this is how the bot reads your feedback replies)
3. Invite it to your server with the `bot` and `applications.commands` scopes, and at minimum `Send Messages` / `Read Message History` permissions in the channel you want alerts in
4. Copy the bot token into `.env`
5. Right-click your alerts channel (Developer Mode must be on in Discord settings) -> Copy Channel ID -> put it in `config.yaml`'s `discord.channel_id`

### Location

Resolve your coordinates once, by hand -- right-click your area on Google Maps -> "What's here?" gives you lat/lng directly. Not worth spending SociaVault credits on a lookup you only ever need once. Put the values into `config.yaml`'s `location:` block.

### Real routing and traffic

By default (`routing.backend: haversine`), travel time is a free straight-line-distance / average-speed estimate -- no real roads, no traffic, same number for peak and off-peak. Good enough to separate "nearby" from "far," not precise.

For the real thing (`routing.backend: google_routes`): Google's Routes API supports traffic-aware routing for a specified future departure time, using a historical-pattern prediction model -- so "what's a typical Tuesday 8am drive look like" is answerable, not just "what's traffic doing right now." Setup:

1. Create a Google Cloud project, attach a billing account (required even for free-tier usage), enable the **Routes API**
2. Create an API key, restrict it to the Routes API
3. Put it in `.env` as `GOOGLE_ROUTES_API_KEY`, set `routing.backend: google_routes` in `config.yaml`

**Cost (corrected from an earlier version of this doc):** Google changed its pricing model in March 2025 -- the old $200/month recurring credit was replaced by free monthly usage caps per SKU. `TRAFFIC_AWARE_OPTIMAL` (what this uses) bills under the **Pro** SKU, which gets **5,000 free Compute Routes calls/month**, then roughly $10/1000 after that. This only runs for listings that already passed stage 1 -- a much smaller set than raw search hits -- which is what makes it affordable at all. Whether you'll actually stay under 5,000/month depends entirely on your real listing volume, which is exactly why the call tracking below exists rather than you having to guess: check `SUM(routing_calls_made)` in `poll_log` after a week or two and you'll know for certain, instead of estimating from a hobby-project ballpark like this one.

**Do you need both peak and off-peak?** Worth being honest about this: only ONE of the two numbers (`travel_time_basis`) ever drives the actual $/hour decision math -- the other is purely informational, shown in the alert but not acted on. So if you don't specifically want to see "how much better would off-peak be," `compute_both: false` halves the API calls for zero loss of decision quality.

There's also a case for using neither generic "peak" nor "offpeak" as labeled and instead pointing `peak_time` (with `compute_both: false`, `travel_time_basis: peak`) at whatever time you *actually* do pickups -- e.g. `"18:00"` right after work, or `"10:00"` on a weekend if `weekday_only: false`. An 8am rush-hour estimate isn't more accurate than your real schedule just because it's labeled "peak" -- it's a generic proxy, and your actual pattern (if it's consistent) is strictly better data for the same one API call.

If a Google Routes call fails for any reason (quota, network, bad key), it silently falls back to the haversine estimate for that listing rather than blocking the valuation -- you'll see a warning in the logs, not a crashed poll cycle. The attempted call still counts toward `routing_calls_made` either way, since Google's quota accounting doesn't care whether your client successfully parsed the response.

### API call tracking

Every poll cycle logs (and stores in `poll_log`) how many calls it made to each metered thing: `detail_calls_made` (SociaVault item-detail, stage 2), `lifecycle_checks_made` (delisting rechecks, also SociaVault item-detail under the hood), and `routing_calls_made` (Google Routes, if that backend is active). Query cumulative totals any time:

```sql
SELECT SUM(detail_calls_made), SUM(lifecycle_checks_made), SUM(routing_calls_made)
FROM poll_log
WHERE started_at > date('now', '-30 days');
```

### Turning things off

Both delisting detection and paid routing already have config-level off switches -- no code changes needed:

- **Delisting/lifecycle tracking**: `categories.<name>.lifecycle_tracking.enabled: false` -- no rechecks, no API calls for it, no time-on-market stats.
- **Paid routing**: `routing.backend: haversine` IS the off switch -- zero Google API calls, ever, while it's selected. Switch to `routing.backend: google_routes` to turn the real thing back on.

### Temporary: comparing haversine against real routing

`routing.log_comparison: true` logs haversine's estimate side by side with a real Google Routes call for every listing, regardless of which backend is actually driving decisions -- so you can build confidence in (or catch problems with) the free estimate before committing to it, or before deciding the paid API isn't worth it. This is scaffolding, not a permanent feature: delete `flipfinder/routing/temp_comparison_logger.py` and the `TEMP-COMPARISON`-tagged block in `main.py` once you've seen enough. It requires `routing.google_routes.api_key` to be set even if `routing.backend: haversine` is what's actually active, and it DOES cost real Google API calls (folded into `routing_calls_made`) the moment it's turned on -- it's not free just because it's diagnostic.

### Jetson side

```bash
# On the Jetson:
pip install -r jetson_service/requirements.txt
ollama pull llama3.2-vision   # or whatever vision-capable model you settle on
uvicorn jetson_service.server:app --host 0.0.0.0 --port 8000
```

Put the Jetson's LAN IP into `config.yaml`'s `inference.jetson.base_url`.

This is intentionally NOT dockerized. Containerizing GPU-backed inference on Jetson's L4T means NVIDIA-specific base images and container runtime config -- real complexity that isn't worth taking on yet for a single-box hobby setup.

**Before the Jetson is wired up**, you can dry-run the whole pipeline with `inference.backend: mock` in config.yaml -- it returns a canned valuation so you can confirm scheduling, filtering, routing, and alerts all work end to end.

### Run it

Long-running mode (normal operation -- scheduler + Discord bot):
```bash
python -m flipfinder.main
```

One-shot mode (see below) for everything else.

## Testing and one-shot mode

```bash
# All categories, prints results to console -- no Discord token needed at all
python -m flipfinder.main --once

# Just one category
python -m flipfinder.main --once --category outboard_motors

# Actually deliver these alerts to Discord instead of printing them
python -m flipfinder.main --once --discord
```

Console mode (the default for `--once`) doesn't import `discord.py` at all, so you can iterate on stage 1 filtering, category prompts, routing, and offer math against real SociaVault listings without a bot token, a configured channel, or even Discord installed.

### Logging

Both modes log to the console AND to `logs/flipfinder.log` (rotating, 5MB x 5 files). `--log-level DEBUG` gets you per-listing stage 1 accept/reject reasoning; `poll_log` in the database has structured per-poll counts (listings seen, new, passed stage 1, detail calls made, alerts sent, lifecycle checks made/newly delisted) if you want to query history directly.

## Giving feedback

Reply directly to any alert message in Discord with actual numbers, e.g.:

> spent $40 on a carb kit, sold it for $380

The bot parses `spent`/`cost`/`paid`/`repair` for repair cost and `sold`/`sale`/`got`/`flipped` for sale price, and reacts with ✅ once logged. If your phrasing doesn't parse, it'll tell you and you can fall back to the explicit slash command:

```
/feedback listing_id:1234567890123456 actual_repair_cost:40 actual_resale_value:380
```

Every piece of feedback improves future valuations in that category immediately -- there's no training step. See "How the learning loop works" below.

## Tuning search cost

SociaVault charges a credit per search call (regardless of how many results it returns) AND a credit per item-detail call. The category profile's `search_strategy` controls the first cost:

- `broad` (default): one search per poll ("outboard motor"). Cheapest.
- `thorough`: one search per brand keyword (~11x the search credits per poll).

`cost_per_search_call`/`cost_per_detail_call` are both exposed in config so this is a visible, tunable tradeoff. Start broad; switch to thorough only if you notice you're missing listings you'd find by browsing manually.

## How delisting detection actually works (and why it changed)

An earlier version of this tried to infer "delisted" from a listing's absence in search results (if we stop seeing it in polls, it's probably gone). **That doesn't work on SociaVault** -- confirmed through testing:

- The `status` and `listed_at` fields search returns are always null, despite being present in the schema.
- `search()` only reliably returns the first page or so of results per query. Sorting by newest doesn't fully fix this -- FB's own ranking mixes in older "suggested" listings unpredictably (a query scoped to the last 24 hours can still surface week-old listings you simply hadn't seen before), so "not in this page" doesn't mean "gone," it might just mean "ranked lower this time."

So delisting detection now works differently: **only listings that pass stage 1** (not the raw search firehose) get registered for periodic lifecycle rechecks. Each check calls `SourceAdapter.check_still_active()` -- on SociaVault, this means a `get_detail()` call, checking for a 404 (the most likely reliable "it's gone" signal) and speculatively checking a couple of plausible status-ish fields in case SociaVault exposes one for sold-but-still-resolvable listings. **Verify this against your real account** -- the "plausible status fields" part is a guess since SociaVault's docs don't show an explicit status field; the 404 case is the part I'd trust without verification.

To keep this from becoming its own API cost problem:
- Each check schedules the next one with backoff (`lifecycle_tracking.recheck_backoff_days`, default `[1, 2, 4, 7, 14]` days) -- daily-ish resolution is precise enough to tell "sold in hours" from "sat for weeks" apart, and checking every 45-minute poll would burn calls for no real precision gain.
- Tracking stops entirely after `max_tracking_days` (default 45) -- a listing still up that long is probably stale and not worth further spend either way.
- `max_checks_per_poll` (default 10) caps how many rechecks happen in a single poll cycle regardless of how large the backlog gets, spreading the cost out rather than spiking it.

Once enough listings have gone through a full lifecycle (`min_sample`, default 5, in `get_time_on_market_stats`), the median days-on-market for similarly-priced listings gets folded into the valuation prompt as local market calibration. Before that, expect it to be quiet -- it deliberately omits the context rather than presenting an unreliable stat as fact.

This still doesn't distinguish "sold" from "removed for some other reason" -- it's a rough proxy on purpose, same as before.

## Multi-unit listings (one trip, several motors)

Some listings sell several motors at once -- an estate clear-out, a shop closing, "3 outboards, take all for $600." These can justify a much longer drive than any single motor would, since the trip's travel cost gets divided across several motors' worth of profit instead of just one. That's handled without any special-case routing logic:

- The valuation prompt asks the AI for **total** resale value, extra repair cost, and extra repair hours **across all units in the listing**, plus how many units it counted (`estimated_item_count`) -- this is a judgment call suited to reading the full description/photos, same as everything else stage 2 does, not a job for regex.
- In the offer math, `base_service_cost` and `base_service_hours` are per-unit fixed costs, so they get multiplied by the unit count. **Travel time is charged exactly once, regardless of unit count** -- one stop either way. That's the entire mechanism: it naturally lets a 3-motor lot tolerate ~3x the drive time a single motor would, without a separate "is this multi-unit" rule anywhere in the alerting logic.
- `min_profit_flat`/`min_profit_pct` remain whole-deal thresholds either way -- "$75 minimum profit to bother with the trip," not "$75 per motor."
- `feature_vector()` also does a cheap regex-based item-count *guess* (`guessed_item_count`) purely so feedback similarity search doesn't blend single-motor and multi-motor past outcomes together -- this is a coarse heuristic for bucketing only, not the source of truth for pricing (that's the AI's own `estimated_item_count`, which reads far more context).

**Practically**: if you widen `max_distance_km` to let more multi-unit listings surface, `alert_min_hourly_rate` is what keeps genuinely-too-far single-motor listings from alerting anyway -- you shouldn't need a separate distance rule for "unless it's a lot." Worth watching the first few multi-unit alerts closely, since the AI's unit-count judgment is new and unverified against your real listings.

**What this does NOT do**: combine separate listings from different sellers into one hypothetical trip because they happen to be geographically close to each other. That's a meaningfully bigger feature (clustering active candidates, route planning, tracking a pending multi-stop trip across polls) and isn't built here -- if that turns out to be what you actually need on top of this, it's a different, separate piece of work.

## Adding a new category

Say you're ready to add snowblowers. Create `flipfinder/categories/snowblowers.py`:

```python
class SnowblowerProfile(CategoryProfile):
    category_id = "snowblowers"
    # implement search_specs, quick_filter, build_valuation_prompt,
    # parse_valuation_response, feature_vector -- see outboard_motors.py
```

Register it in `flipfinder/categories/__init__.py`, add a block to `config.yaml`
under `categories:` with its own `base_service_cost`, `base_service_hours`,
price range, `lifecycle_tracking`, and schedule. Nothing else changes.

## Adding a new source

Create `flipfinder/sources/own_monitor.py` implementing `search()`,
`get_detail()`, and `check_still_active()` (see `sources/base.py` for the
contract, `sources/sociavault.py` for a worked example). Register it in
`flipfinder/sources/__init__.py`, reference it by name in a category's
`source:` config field.

One thing worth reconsidering at that point: if your own monitor can tell
you definitively when a listing is removed (rather than needing a periodic
recheck), `check_still_active()` is where that shortcut belongs -- callers
don't need to know or care that it's cheaper than SociaVault's approach.

## How the learning loop works (and how it should evolve)

Every new valuation pulls the most similar few past feedback entries (by
brand/hp/year/condition -- see `feature_vector()`) plus any available local
time-on-market stats, and hands them to the AI as "here's what actually
happened" context -- `feedback_store.py`'s `find_similar()` and
`market_stats.py`'s `get_time_on_market_stats()`, both plain queries, no ML
dependency.

Once you've got real volume (order of 30-50+ logged outcomes per category),
it's worth revisiting whether a proper learned correction model beats
retrieval. Don't build that now -- there's no data to validate it against yet.

## How the scheduler is meant to evolve

The schedule in `config.yaml` is a starting point, not a considered answer.
Every poll gets logged to `poll_log` regardless of mode. Once you've got a
few weeks of that log, you'll be able to see actual posting patterns and
decide whether to adjust the schedule, or eventually build something that
adjusts itself.

## Single database, all categories

Yes, deliberately -- one SQLite file at `database.path` covers every
category and source. `category_id` is just a column, not a schema boundary
worth splitting on. WAL mode is on, so it's safe to inspect with any SQLite
browser while the app is running.

## Known limitations / things to sanity-check before relying on this

- **The Discord bot is untested end-to-end** -- give it a real run
  (`--once --discord` is a good first test) before trusting it unattended.
- **The Jetson service assumes Ollama** with a vision-capable model.
- **SociaVault's exact response field names and behavior** were taken from
  their public API reference docs as of mid-2026, PLUS your own testing
  which already caught the status/listed_at fields being unreliable in
  practice -- worth a `--once` smoke test before the first overnight run,
  especially to confirm `check_still_active()`'s 404-based detection
  actually behaves the way this assumes.
- **Google Routes API setup requires a Google Cloud billing account** even
  though hobby-scale usage will likely stay within the free 5,000
  calls/month Pro-tier cap -- if that's friction you'd rather not deal
  with, `routing.backend: haversine` is a completely reasonable default;
  you lose traffic-awareness, not correctness.
- **Delisting detection is a proxy, not certainty.**
- **The reply-parsing regex for feedback is intentionally simple.**

## Running tests

```bash
pip install pytest
pytest tests/
```

Covers offer/hourly-rate math (including peak/off-peak selection), stage 1
filtering (including distance), feedback similarity ranking, scheduler
timing, routing backends (including the Google->haversine fallback path),
and the full lifecycle-tracking flow (registration, backoff scheduling,
delisting, staleness) -- the parts of the system that don't require a live
Discord bot, SociaVault key, Google Routes key, or Jetson to verify.
