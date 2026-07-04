# Contributing

Thanks for your interest in improving `homebox-mcp`. It's a small, single-module
MCP server, so contributing is lightweight.

## Development setup

You need [`uv`](https://docs.astral.sh/uv/). The server is one module
(`homebox_mcp.py`) with [PEP 723](https://peps.python.org/pep-0723/) inline
dependencies, so there's nothing to install up front — `uv` resolves everything
on demand.

```bash
git clone https://github.com/dgahagan/homebox-mcp
cd homebox-mcp
```

## Tests

Tests use `pytest` with [`respx`](https://lundberg.github.io/respx/) to mock the
Homebox HTTP API — **no live instance is required or should be used** for the
suite:

```bash
uv run --with pytest --with respx --with . pytest
```

> **Stale-cache trap:** `--with .` caches the built package keyed on the
> version string. If you edit `homebox_mcp.py` without bumping `__version__`,
> pytest may import the stale cached build and miss your changes. Force a
> rebuild with:
>
> ```bash
> uv run --refresh-package homebox-mcp --reinstall-package homebox-mcp \
>   --with pytest --with respx --with . pytest
> ```

## Lint

Code is linted with [`ruff`](https://docs.astral.sh/ruff/) (config in
`pyproject.toml`; line length 100):

```bash
uv run --with ruff ruff check .
```

## Pull requests

- **Add tests for behavior changes.** The full-body-PUT preserve logic and the
  typed custom-field handling are exactly the kind of thing that regresses
  silently — cover new or changed behavior with a mocked-httpx test.
- **Update [`CHANGELOG.md`](CHANGELOG.md)** under the `Unreleased` section
  ([Keep a Changelog](https://keepachangelog.com/) format).
- **Keep it lint-clean** (`ruff check .` passes).
- **Keep tool docstrings accurate** — they are the source of truth for the tool
  reference in the README and for how MCP clients present each tool.

## Testing against a live Homebox instance

If you validate a change against a real Homebox instance, **use disposable data
only** — create clearly-marked throwaway items/locations and delete them
afterward. Never test against inventory you care about, and never point the
suite or ad-hoc scripts at a production instance's real data.

By contributing you agree that your contributions are licensed under the
project's [MIT License](LICENSE).
