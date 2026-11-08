# Project rules

This project generates a Python SDK from an OpenAPI spec using openapi-generator (see `main.py`).

## Do not run code

- Never execute Python or any other code yourself: no `python`, `py`, `uv run`, `uvx`, `pytest`, `pip`, `java`, `openapi-generator-cli`, or anything in `.venv\Scripts`.
- Do not run `main.py` or generated SDK code. The user runs everything.
- You may read and edit files, and tell the user which command to run (for example `uv run main.py`) and what to look for in the output.
- If you need output to proceed, ask the user to run the command and paste the result.

## Do not read secrets

- Never read, search, print or edit `.env` files or any `.env.*` variant (for example `.env.local`).
- If you need to know which variables exist, ask the user.

These rules are enforced by `permissions.deny` in `.claude/settings.json`.
