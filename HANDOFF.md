# Handoff: deploying and testing flipfinder

Written at the point of moving from architecture/code (built here, in a
chat session with no access to real devices or live services) to actually
running this on your Pi. Read this + README.md when picking the project
back up in Claude Code.

## Where this stands

- Full pipeline built: sources, categories, inference backends, routing
  backends, offer/hourly-rate math, feedback loop, lifecycle-based delisting
  detection, Discord bot, one-shot CLI mode. 76 unit tests, all passing --
  but all pure-logic tests (offer math, filtering, parsing, DB behavior). No
  test here has ever talked to a real external service.
- **Nothing has been run against a real SociaVault account, a real Discord
  bot, or a real Google Routes key.**
  Everything was built directly against published API docs and exercised
  with mocks. Closing that gap is the entire point of tonight.
- Developed and tested in an x86_64 Linux sandbox, not ARM. Nothing in the
  code is architecture-specific (pure Python + requests + sqlite3 +
  discord.py + PyYAML + FastAPI/uvicorn), but re-run `pytest tests/` fresh
  on the actual Pi as a first sanity check in case any dependency needs to
  build from source on ARM rather than installing a prebuilt wheel.

## Accounts/credentials to have ready

1. **SociaVault API key** -- sociavault.com signup.
2. **Discord bot** -- application + bot token + channel ID. The **Message
   Content Intent** must be enabled in the Discord developer portal or
   reply-based feedback will silently never fire (no error, it just won't
   see your replies).
3. **Anthropic API key** -- console.anthropic.com/settings/keys. Powers
   real AI valuations via `inference.backend: claude_api` -- see "Phase 2"
   below.
4. **(Optional) Google Cloud project + Routes API key** -- only needed if
   you want real traffic-aware routing from day one. `routing.backend:
   haversine` works with zero setup and is the config default.

## Physical setup (the original tonight's-plan items)

- **Pi**: headless OS, always-on, runs the app via venv + `deploy/flipfinder.service`
  systemd unit (Docker was an option earlier in this project but was dropped --
  running as a plain venv install was simpler for this setup).

## Recommended rollout order

Each phase adds exactly one new live dependency, so if something breaks
you know immediately which piece is responsible.

**Phase 0 -- environment sanity, no live services**
```
pip install -r requirements.txt
pytest tests/
```
Confirms nothing broke moving off the dev sandbox before touching anything real.

**Phase 1 -- real SociaVault, mock inference, console output**
`config.yaml`: `inference.backend: mock`, `routing.backend: haversine`.
```
python -m flipfinder.main --once --category outboard_motors
```
This is the first real test of whether SociaVault's actual API matches
what `flipfinder/sources/sociavault.py` assumes. Watch for field-shape
mismatches specifically, not just crashes -- e.g. does `search()` actually
return `location.latitude/longitude` the way the code expects, are prices
structured the way `(item.get("price") or {}).get("amount")` assumes.

**Phase 2 -- real AI valuations, still console-only**
Originally planned around a Jetson running a local vision model. The Jetson
actually available for this project turned out to be a 2019 Nano (Maxwell
GPU, JetPack 4.5, 4GB shared RAM) -- too weak for that (Ollama's GPU builds
only target JetPack 5/6, and 4GB isn't enough RAM for a real vision model
regardless). Since the Pi already has internet access for SociaVault and
Discord, there's no reason a second box needs to relay prompts to a model
-- it just calls the Claude API directly instead. (A Jetson-fallback path
was built and then deliberately scrapped again in this same session --
decided it wasn't worth the complexity right now; if local/self-hosted
inference is worth revisiting later, `flipfinder/inference/base.py`'s
`InferenceBackend` interface is the extension point.)

`config.yaml`: `inference.backend: claude_api`, `ANTHROPIC_API_KEY` set in
`.env`. Same `--once` command as Phase 1:
```
python -m flipfinder.main --once --category outboard_motors
```
Watch whether the model actually follows the photo-reasoning and
item-count instructions in the prompt (see `categories/outboard_motors.py`'s
prompt), not just whether it returns parseable JSON -- e.g. does
`item_count_confidence` actually drop on a genuinely ambiguous listing, or
does it always come back near 1.0 regardless. (Already spot-checked once
against a real listing during this rollout -- the model correctly reasoned
about a "blown powerhead, like-new lower unit" parts motor from the
description, not just a generic guess. Worth checking a few more,
especially multi-unit and photo-driven cases.)

**Resilience against Claude API outages/credit exhaustion** was added
instead of a Jetson fallback: a failed valuation for one listing is caught
per-listing (not left to abort the whole poll) and deliberately left
unmarked-processed, so the next poll retries it automatically rather than
losing it. See README's "Resilience: a failed valuation doesn't lose the
listing." Not yet exercised against a real outage -- worth testing by
temporarily pointing `claude_api.api_key` at something invalid for one
poll and confirming the listing gets picked back up on the next one rather
than vanishing.

**Also new this session**: `alert_min_hourly_rate: null` (see everything
while calibrating a real threshold) and `discord.rejects_channel_id` (a
digest of stage-1 rejects to a separate channel) -- see README's "Seeing
everything while you calibrate."

**Phase 3 -- Discord, for the first time ever**
```
python -m flipfinder.main --once --category outboard_motors --discord
```
Check the embed renders correctly (photos, fields, the gold "please
confirm" styling if a low-confidence listing comes up), then specifically
exercise the feedback loop: reply with a casual cost/sale correction
("spent $40, sold for $380"), an item-count correction ("actually there's
2"), a condition-at-sale phrase ("as-is, didn't service it"), and the
`/feedback` slash command. Confirm each one actually lands correctly in
the `feedback` table (`sqlite3 data/flipfinder.db "select * from
feedback"` or any SQLite browser -- WAL mode means this is safe to do
while the app is running).

**Phase 4 -- Google routing, if you want it**
`routing.backend: google_routes`. Consider `log_comparison: true` for a
while first to see how far off haversine actually was before fully
committing either way.

**Phase 5 -- go live**
Drop `--once`. `sudo systemctl enable --now flipfinder`. Let the scheduler
run for real and start watching `poll_log` and `logs/flipfinder.log` over
the following days.

## Specific unverified assumptions worth watching first

Compiled from caveats raised over the course of this build -- these are
the highest-value things to confirm or fix early, before trusting the
system unattended:

- SociaVault search results' `status`/`listed_at` fields being null --
  already confirmed by you directly, and the delisting-detection design
  was built around that finding.
- `check_still_active()`'s reliance on a 404 response as the "this
  listing is gone" signal -- reasoned through, never confirmed against an
  actual removed listing. If SociaVault does something else (redirects,
  a different status code, a "sold" flag that still 200s), this needs
  adjusting in `flipfinder/sources/sociavault.py`.
- `thumbnail_url` reliability for the `require_photo` stage-1 filter --
  defaults to `false` specifically because this is unverified; watch
  stage-1 reject logs before turning it on.
- Whether Claude actually uses attached photos as directed, or just
  returns numbers without visibly having looked at them -- spot-checked
  once during Phase 2 (correctly reasoned about condition specifics not
  obviously implied by the title alone), worth checking a few more,
  especially multi-unit and photo-vs-description-mismatch cases.
- The per-listing retry-on-failure logic (see Phase 2) -- built, but never
  exercised against a real Claude API failure. Worth a deliberate test
  (temporarily invalid `claude_api.api_key` for one poll) before trusting
  it unattended.
- The entire Discord bot -- every code path in `notifier/discord_bot.py`
  is new and has zero live testing behind it. This now includes the
  rejects-channel digest, also untested end-to-end.

## Suggested opening for the Claude Code session

Point it at the repo with something like: "Read HANDOFF.md and README.md,
then help me work through Phase 1." Claude Code has bash/file access on
the actual devices, which is exactly what was missing here -- debugging
real API response mismatches, tailing logs, querying the SQLite DB
directly, and iterating on `config.yaml` are all things that were
necessarily guesswork in a chat session without live access to anything.
