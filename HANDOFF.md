# Handoff: deploying and testing flipfinder

Written at the point of moving from architecture/code (built here, in a
chat session with no access to real devices or live services) to actually
running this on your Pi + Jetson. Read this + README.md when picking the
project back up in Claude Code.

## Where this stands

- Full pipeline built: sources, categories, inference backends, routing
  backends, offer/hourly-rate math, feedback loop, lifecycle-based delisting
  detection, Discord bot, one-shot CLI mode. 76 unit tests, all passing --
  but all pure-logic tests (offer math, filtering, parsing, DB behavior). No
  test here has ever talked to a real external service.
- **Nothing has been run against a real SociaVault account, a real Discord
  bot, a real Jetson/Ollama instance, or a real Google Routes key.**
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
3. **(Optional) Google Cloud project + Routes API key** -- only needed if
   you want real traffic-aware routing from day one. `routing.backend:
   haversine` works with zero setup and is the config default.

## Physical setup (the original tonight's-plan items)

- **Pi**: headless OS, always-on, runs the app via Docker (`docker compose
  up -d --build`) or venv + the new `deploy/flipfinder.service` systemd
  unit (Docker existed before; the systemd path for a plain venv install
  didn't, until this handoff -- use whichever you already lean toward).
- **Jetson**: `ollama pull <a vision-capable model>` then `uvicorn
  jetson_service.server:app --host 0.0.0.0 --port 8000`. Intentionally not
  dockerized (see README for why).
- **Network**: both on the same LAN via your switch. Give the Jetson a
  DHCP reservation so `config.yaml`'s `inference.jetson.base_url` doesn't
  go stale if it gets a different IP after a reboot.

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

**Phase 2 -- bring up the Jetson, real AI valuations, still console-only**
`config.yaml`: `inference.backend: jetson`, correct `base_url`. Same
`--once` command as Phase 1. Watch whether the model actually follows the
photo-reasoning and item-count instructions in the prompt (see
`categories/outboard_motors.py`'s prompt), not just whether it returns
parseable JSON -- e.g. does `item_count_confidence` actually drop on a
genuinely ambiguous listing, or does it always come back near 1.0
regardless.

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
Drop `--once`. Either `sudo systemctl enable --now flipfinder` or `docker
compose up -d`. Let the scheduler run for real and start watching
`poll_log` and `logs/flipfinder.log` over the following days.

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
- Whether the Jetson's vision model actually uses attached photos as
  directed, or just returns numbers without visibly having looked at
  them -- worth spot-checking `reasoning` text against a few listings
  where the photos clearly show something the description doesn't
  mention.
- The entire Discord bot -- every code path in `notifier/discord_bot.py`
  is new and has zero live testing behind it.

## Suggested opening for the Claude Code session

Point it at the repo with something like: "Read HANDOFF.md and README.md,
then help me work through Phase 1." Claude Code has bash/file access on
the actual devices, which is exactly what was missing here -- debugging
real API response mismatches, tailing logs, querying the SQLite DB
directly, and iterating on `config.yaml` are all things that were
necessarily guesswork in a chat session without live access to anything.
