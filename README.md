# sourcekit

Tools for finding source material and converting it to text. Each tool is its own Python package under
`tools/`, with its own dependencies, command, tests and README. Tools don't import each other, so any one
of them can be used on its own.

| Tool | Command | What it does |
|---|---|---|
| [arxiv-client](tools/arxiv-client/README.md) | `arxiv` | Search arXiv, fetch papers by ID, read the daily feeds, download PDFs. No runtime dependencies. |
| [doc-converter](tools/doc-converter/README.md) | `doc-converter` | Convert a born-digital PDF into verbatim Markdown, and verify the result against the PDF. |

## Setup

The repository is a [uv](https://docs.astral.sh/uv/) workspace on Python 3.14, with one lock file
(`uv.lock`) for all tools.

```sh
uv sync --all-packages                       # every tool, plus the dev tools (ruff)
```

## Using a tool

```sh
uv run --package arxiv-client arxiv search --title "attention is all you need" --max 5
uv run --package doc-converter doc-converter convert --input <pdf> --output <dir> --profile <name>
```

To put a tool's command on your PATH, install it in editable mode, so code changes take effect without
reinstalling:

```sh
uv tool install --editable tools/arxiv-client
uv tool install --editable tools/doc-converter
```

## Tests

Each tool runs its own suite from its own directory:

```sh
cd tools/arxiv-client  && uv run python -m unittest discover -s tests -t .
cd tools/doc-converter && uv run python -m unittest discover -s tests
```

The tool READMEs describe the opt-in live and regression tests.

## Lint

```sh
uv run ruff check .
uv run ruff format --check .
```

## Adding a tool

```sh
uv init --package tools/<name>
```

This creates `tools/<name>/` with a `src/` layout and adds it to the workspace through the
`members = ["tools/*"]` glob. Give it its own command under `[project.scripts]`, its own `tests/` and a
README, and add it to the table above. A tool that needs dependency versions that clash with the
workspace's can leave the workspace (`[tool.uv.workspace] exclude`) and keep its own lock.

## License

arxiv-client is MIT-licensed (see its `LICENSE`).
