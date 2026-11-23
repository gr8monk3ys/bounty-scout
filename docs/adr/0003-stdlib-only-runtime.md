# Stdlib-only runtime

`pyproject.toml` declares no runtime dependencies. The scout runs uninstalled with `python3 scripts/scout.py`, shelling out to `gh` for GitHub data and using `urllib`-level access for the tinygrad sheet. Dev tools (pytest, ruff) live in the `dev` dependency group only.

Consequence: adding a runtime dependency needs a reason strong enough to give up "clone and run".
