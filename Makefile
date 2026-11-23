.PHONY: check lint test

check: lint test

lint:
	uv run ruff check .

test:
	uv run pytest -q
