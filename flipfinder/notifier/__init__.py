"""
Notifier implementations. Deliberately NOT imported eagerly here -- import
FlipFinderBot from flipfinder.notifier.discord_bot or ConsoleNotifier from
flipfinder.notifier.console directly. That keeps `python -m flipfinder.main
--once` (console mode) usable without discord.py installed at all.
"""
