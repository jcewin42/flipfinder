# flipfinder

> Ready to actually deploy and test this on real devices? Start with `HANDOFF.md` instead of this file -- it has the phased rollout plan and the list of things that are still unverified against real services.

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
Stage 1: quick filter (cheap, source-agnostic screening -- keyword/price/photo, no distance cutoff)
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
- **Inference backend** (`flipfinder/inference/`) -- what answers "value this listing". The Claude API today.
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

```bash
cd flipfinder
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp config.example.yaml config.yaml
cp .env.example .env
```

For always-on operation, see `deploy/flipfinder.service` (systemd unit -- copy to `/etc/systemd/system/`, adjust the user/paths, `systemctl enable --now flipfinder`).

Fill in `.env`:
```
DISCORD_BOT_TOKEN=...
SOCIAVAULT_API_KEY=...
ANTHROPIC_API_KEY=...       # for inference.backend: claude_api
GOOGLE_ROUTES_API_KEY=...   # only needed if routing.backend: google_routes
```

Get your SociaVault key at https://sociavault.com/signup (free tier: 100 requests/day -- note both search calls AND item-detail calls count against this, see "Tuning search cost" below). Get your Anthropic key at https://console.anthropic.com/settings/keys.

Unresolved `${ENV_VAR}` placeholders only cause problems for whichever backend is actually selected in config.yaml -- an unset `GOOGLE_ROUTES_API_KEY` is harmless while `routing.backend: haversine` is selected, for instance. You'll get a warning in the logs either way.

### Discord bot

1. Create an application + bot at https://discord.com/developers/applications
2. Under **Bot**, enable the **Message Content Intent** (required -- this is how the bot reads your feedback replies)
3. Invite it to your server with the `bot` and `applications.commands` scopes, and at minimum `Send Messages` / `Read Message History` permissions in every channel you want it posting to
4. Copy the bot token into `.env`
5. Right-click your alerts channel (Developer Mode must be on in Discord settings) -> Copy Channel ID -> put it in `config.yaml`'s `discord.channel_id`
6. Optional: create a second channel for stage-1 rejects, same Copy Channel ID steps -> `discord.rejects_channel_id` (see "Seeing everything while you calibrate" below). Leave it unset if you don't want this.

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

### Inference: Claude API

`inference.backend: claude_api` calls the Anthropic API directly from the Pi -- no second box needed, since the Pi already has internet access for SociaVault and Discord. Put your key in `.env` as `ANTHROPIC_API_KEY`, and pick a model in `config.yaml`'s `inference.claude_api.model` (default `claude-sonnet-5`, chosen deliberately for cost/latency at this call volume -- every stage-1 survivor, automatically -- not defaulted to Opus; roughly $0.01/listing on Haiku 4.5, $0.02-0.03/listing on Sonnet 5 for a typical prompt plus a few photos).

An earlier version of this project planned to run a local vision model on a Jetson instead. That was dropped -- the Jetson available for this project turned out too weak for real local vision inference -- in favor of calling Claude directly. If local/self-hosted inference is worth revisiting later (cost, latency, or just wanting an offline option), `flipfinder/inference/base.py`'s `InferenceBackend` interface is what a new backend would implement; nothing else in the pipeline needs to change.

**Before you've got a key wired up**, you can dry-run the whole pipeline with `inference.backend: mock` in config.yaml -- it returns a canned valuation so you can confirm scheduling, filtering, routing, and alerts all work end to end.

### Resilience: a failed valuation doesn't lose the listing

If the inference call for one listing throws -- Claude API down, rate-limited, out of credits, a network blip -- that failure is caught per-listing rather than aborting the rest of the poll. The listing is deliberately left un-marked-processed, so the *next* poll picks it back up and retries it automatically, rather than it being silently lost forever. (Concretely: `db.mark_processed()` only fires after a listing is fully evaluated; a mid-evaluation exception skips it, so `has_processed()` still returns false for it next time.) Watch `logs/flipfinder.log` for "leaving unprocessed so the next poll retries it" if you want to confirm this is actually happening during an outage; the returned poll summary also includes an `evaluation_failures` count (not persisted to `poll_log` -- that table's schema is fixed -- but visible in the console/log output of each poll).

### Seeing everything while you calibrate

`alert_min_hourly_rate: null` (instead of a number) disables the alert threshold entirely -- every listing with a usable valuation gets sent, regardless of $/hour, so you can watch real results and decide where the bar should actually be before locking in a number. `should_alert()`'s confidence gate still applies (a totally-failed-to-parse valuation still doesn't alert). Set it back to a real number once you've seen enough.

Stage-1 rejects (the much larger volume of listings that never even get valued) can go to a **separate** channel: `discord.rejects_channel_id`. One digest message per poll (title/price/link for each reject), not one message per listing -- stage 1 can reject dozens per poll, and a message-per-reject would spam/rate-limit fast. Leave it `null` to skip the digest (just logs a warning). Between this and the un-thresholded alerts channel, you can see the entire pipeline's output while calibrating, then mute either channel once you don't need that visibility anymore.

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

### Pagination: only fetched when we might actually be missing something

Every search is sorted newest-first (`sort_by=creation_time_descend`). Normally only the first page is fetched -- but if the OLDEST listing on that page is still new (not seen in a prior poll), that's a sign more new listings might exist past the edge of the page (e.g. after downtime, or a burst of new listings between polls), so the next page gets fetched too, and so on until either a page's oldest listing is one we've already seen, or `max_search_pages` (default 3, per query per poll) is hit. Each extra page costs one more search credit, which is what the cap is for. If you see the `max_search_pages` warning in the logs regularly, it means polls are consistently behind -- worth raising the cap or polling more often rather than assuming it'll catch up on its own.

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

### Uncertain unit counts get checked with you, not guessed silently

The AI reports a separate confidence specifically for the unit count (`item_count_confidence`), distinct from its overall valuation confidence -- a listing can be "clearly a Yamaha 40hp in good shape" (high confidence) while still being genuinely ambiguous about whether it's 1 or 3 motors (low item-count confidence).

Below `item_count_confidence_threshold` (default 0.6), the listing gets alerted **regardless of what the hourly rate says** -- not just when it looks like a good deal. This matters: if the AI undercounts (guesses 1 when it's actually 3) and that undercounted version doesn't clear your rate threshold, the listing would otherwise never reach you at all, which is worse than one extra "not sure, can you check?" alert. The Discord embed for these is tagged distinctly (gold color, an explicit callout) so you know to double-check before trusting the numbers.

Reply with the actual count once you know it (`"actually there's 2"`, or `/feedback actual_item_count:2`), the same reply-to-alert mechanism as cost/sale feedback. That correction gets stored and retrieved the exact same way as repair-cost/resale-value feedback (see "How the learning loop works") -- future ambiguous listings get "you were unsure about a similar one and guessed wrong" as context, which is the actual mechanism behind "improves over time," not a separate training step.

### Condition at sale matters for feedback, not just the price

`estimated_resale_value` always assumes the standard service (and any estimated extra repair) gets fully done before resale. If you sell something as-is, decide it's not worth fixing after a closer look, or sell it for parts instead, the resulting `actual_resale_value` is real data but under a **different** assumption than what was predicted -- treating it as directly comparable to a "fully serviced, running" sale would quietly corrupt future calibration.

`condition_at_sale` captures this (free text, not a rigid enum -- e.g. `"serviced and running"`, `"as-is, not serviced"`, `"parts only"`). It's surfaced alongside every retrieved comp in the valuation prompt, so the AI can weight each one appropriately rather than averaging incompatible outcomes together. A few common phrasings parse casually from a reply (`"as-is, didn't service it"`, `"fully serviced and running"`, `"parts only"`, `"not running"`); anything more specific should go through `/feedback condition_at_sale:"..."` since natural-language condition descriptions are far more open-ended than a dollar amount or a count, and this parser is intentionally rough. If you leave it unrecorded, the prompt explicitly flags that comp as "weight cautiously" rather than presenting incomplete ground truth as reliable.

### Feedback is one row per listing, not a growing pile of fragments

Item count, repair cost, resale value, and condition typically arrive at different times (confirm the count today, log the repair cost next week, the sale price a month later). `record_feedback` is an upsert keyed on `(listing_id, source)` -- each new piece of information fills in that listing's one row rather than creating a new fragment, and existing values are never overwritten with nothing just because a later reply happened to omit them. This is what makes retrieval actually useful: one coherent comp per past listing instead of scattered partial rows that would otherwise dilute or duplicate in similarity search.

**On keeping this integrated rather than a separate app**: the entire value of tracking sale price here is feeding calibration for this specific valuation system -- splitting it into a separate app/database would mean building a sync layer just to get the data back into the one place it's actually used, for no real benefit. Same reasoning as the single-database decision earlier: this is data belonging to the existing feedback loop, not a separate system. If what's actually wanted later is a nicer *interface* for entering/reviewing this (beyond Discord replies), that's a smaller, separate question about UX -- the SQLite file is already directly inspectable, and a lightweight review UI could sit on top of the same DB without needing separate storage.

## How listing photos are used (and where they're deliberately not)

Photos flow into stage 2 already -- `evaluate_listing` sends up to `image_count` (default 3, configurable per category) photos to the inference backend alongside the text prompt; `flipfinder/inference/claude_backend.py` downloads and base64-encodes them as image content blocks in the Anthropic Messages API call. What actually matters is whether the prompt *directs* the model to use them for anything, not just whether they're attached -- so the outboard motor prompt explicitly asks the AI to weigh visible condition (corrosion, missing/damaged parts) against the text description, and to use the photos to help confirm or deny the unit count, lowering `item_count_confidence` when photos don't clearly resolve it.

Two smaller changes: `image_count` is now a per-category config knob rather than hardcoded (more photos is better grounding but more inference cost/latency per listing -- the same tradeoff as everything else cost-related here), and Discord alerts now show the primary photo larger (`set_image` instead of a small thumbnail) plus up to 2 more as additional embeds in the same message, instead of one small thumbnail.

**New, opt-in, and explicitly cautious**: `require_photo` rejects listings with no thumbnail at stage 1 -- a free, well-known spam/placeholder signal, since `thumbnail_url` comes back with every search result at zero extra cost. It defaults to `false` because this project has already been burned once by a SociaVault field that looked reliably populated in their docs but was null in practice (search-result `status`/`listed_at`). Watch stage 1 reject logs for false positives before turning this on for real.

**Deliberately NOT changed, and why:**
- **No vision in stage 1.** Stage 1's entire purpose is to be free/cheap so stage 2 (the actually expensive step) only runs on survivors. Running any image inference at stage 1 would erase that cost structure for every raw search hit, not just the ones worth valuing.
- **No photos persisted into the feedback store for future few-shot comparison.** FB CDN photo URLs are likely to go stale by the time a comp would be retrieved weeks or months later, and attaching reference images from 3-5 past feedback entries to every single new valuation would meaningfully increase inference load on every call, not just the listing being evaluated. Text-based calibration ("predicted $X, actual $Y") already carries most of the useful signal without that cost.

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

Covers offer/hourly-rate math (including peak/off-peak selection and
multi-unit scaling), stage 1 filtering, feedback similarity ranking and
upsert semantics, scheduler timing, routing backends (including the
Google->haversine fallback path), the full lifecycle-tracking flow
(registration, backoff scheduling, delisting, staleness), reply parsing
(cost/sale/item-count/condition, all discord.py-free for testability), and
the alert-gating decision (including the item-count-uncertainty bypass and
the null-threshold "alert on everything" mode) -- the parts of the system
that don't require a live Discord bot, SociaVault key, Google Routes key, or
Anthropic key to verify.
