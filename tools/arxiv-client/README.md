# arxiv-client

A Python client for arXiv's query API (`export.arxiv.org/api/query`) and its daily announcement feeds (`rss.arxiv.org`), plus single-PDF download. Part of [sourcekit](../../README.md).
- Python 3.14.
- No runtime dependencies (standard library only).
- Command: `arxiv`. Run it with `uv run --package arxiv-client arxiv …`, or put it on your PATH with `uv tool install --editable tools/arxiv-client`.

The behaviour follows the empirical reference in [`docs/arxiv-api.md`](docs/arxiv-api.md). Section 6 of that doc is the requirements list.

Thank you to arXiv for use of its open access interoperability.

## Layout

```
src/arxiv_client/  the package
tests/             unittest suite; fixtures in tests/fixtures/arxiv/
docs/              the empirical API reference and an OpenAPI description
```

## CLI (prints JSON)

```sh
arxiv search --title "attention is all you need" --max 5
arxiv search --all "protein folding" --category q-bio.BM --sort submitted --max 20
arxiv search --author Hinton --from 20230101 --to 20231231
arxiv search --abstract homogenization --any-category cs.HC --any-category cs.CL --format compact
arxiv search --all "protein folding" --explain    # adds the query as arXiv parsed it
arxiv search --title "low-rank adaptation" --dry-run   # prints the query and URL, sends nothing
arxiv batch queries.json --format compact   # or - to read the list from stdin
arxiv fetch 1706.03762 hep-th/9901001v1 --abstract-chars 300
arxiv get 2301.00001
arxiv feed cs.AI cs.LG          # add --rss for the RSS feed
arxiv pdf 2301.00001 --out ./papers
```

**Search filters:**
- Field flags (`--title`, `--author`, `--abstract`, `--comment`, `--journal-ref`, `--category`, `--report-number`) can be repeated. Their text becomes a quoted phrase when it has more than one word.
- `--all` is free text. Its words are joined with `AND`, and `"quoted"` parts stay phrases.
  - **A stopword among them returns 0 results**, because arXiv does not index stopwords: `--all "attention is all you need"` finds nothing, while `--all '"attention is all you need"'` finds 47 papers. See [`docs/arxiv-api.md`](docs/arxiv-api.md) §8.1.
- `--any-category` can be repeated. Its values are joined with `OR`, so `--any-category cs.HC --any-category cs.CV` matches papers in either. (`--category` values are joined with `AND`, like the other field flags.)
- `--raw` is passed through verbatim.
- All filters are joined with `AND`.

**Checking a query** (`search` and `batch`):
- `--explain` adds two fields to the output. `server_query` is the query as arXiv parsed it, read from the feed title. `request_urls` lists the URL of each page requested. In compact output, `server_query` is a second header line.
  - arXiv rewrites some input without an error, and `server_query` is the only place that shows it. For example, `lastUpdatedDate:[…]` becomes `submittedDate:"…"`, `au:Hinton_G` becomes `au:"Hinton G"`, and terms with no operator between them are joined with `OR`.
- `--dry-run` builds and validates the query, prints it with its first-page URL, and sends nothing.

**Batch:** `batch FILE` runs up to 20 searches in one call.
- **Input:** `FILE`, or `-` for stdin, holds a JSON list of query objects.
  - Query keys: `title`, `author`, `abstract`, `comment`, `journal_ref`, `category`, `report_number` and `all` (each a string or a list of strings), plus `raw`, `any_category`, `from` and `to`.
  - Run settings: `label` (unique; default `q1`, `q2`, …), `max` (a positive integer, default 10), `sort` (`relevance`, `submitted` or `updated`) and `order`.
- **Validation:** every query is checked before any request is sent, and unknown keys are rejected.
- **Output:** each query's IDs, and every paper once, with `found_by` listing the labels of the queries that returned it.
- **Errors partway:** if an error stops the batch, stderr carries the usual error JSON, plus `completed` with the queries that finished and `papers` with the papers they returned.

```json
[
  {"label": "homogenization", "abstract": ["homogenization", "web design"], "any_category": ["cs.HC", "cs.CL"]},
  {"label": "mode collapse", "raw": "ti:\"mode collapse\" OR ti:\"output diversity\"", "max": 25, "sort": "submitted"}
]
```

**Lighter output:**
- `--format compact` (for `search`, `fetch` and `batch`) prints one line per paper: `id | published date | primary category | title`. In `batch` it prints a header per query, marks a paper's first appearance with `*`, and ends with the unique count. Errors stay JSON on stderr.
- `--abstract-chars N` (JSON only) cuts each abstract to N characters; `0` drops it.
- JSON output leaves out empty author affiliation lists. An author with no affiliations is just `{"name": …}`.

**Output and errors:** JSON (or compact text) goes to stdout. Errors go to stderr as `{"error": kind, "message": …}`, with `retry_after` added when arXiv throttled the request.

| Exit | `error` | Meaning |
|---|---|---|
| 0 | | success |
| 2 | `validation` | bad input, or HTTP 400 from arXiv |
| 3 | `not_found` | paper or PDF does not exist |
| 4 | `throttled` | 429, 503, 406, or a 200 whose body is `Rate exceeded.`. Wait `retry_after` seconds |
| 5 | `server_error` | other HTTP errors, malformed responses |
| 6 | `timeout` | the request or its 30 s budget timed out |
| 7 | `network_blocked` | DNS failure, refused connection, or a proxy refused the tunnel. In a sandbox, allow the domain |
| 8 | `local_io` | a PDF could not be written: permissions, a full disk, or similar |

**User-Agent:** set by `--user-agent`, then `$ARXIV_USER_AGENT`, then the default `arxiv-client/0.1`.

## Library

```python
from arxiv_client import ArxivClient, Q

client = ArxivClient()
for paper in client.search(Q.title("diffusion") & Q.category("cs.CV"), max_results=50, sort_by="submittedDate"):
    print(paper.versioned_id, paper.title)

result = client.fetch(["1706.03762", "hep-th/9901001v1", "2401.99999"])
result.papers  # keyed by the ID strings you passed in
result.not_found  # ["2401.99999"]

feed = client.feed(["cs.AI"])  # empty (not an error) at weekends
client.download_pdf("2301.00001", "./papers")
```

**Query builder `Q`:**
- Field constructors: `title`, `author`, `abstract`, `comment`, `journal_ref`, `category`, `report_number`, `id`, `all`.
- Other constructors: `phrase`, `submitted_between`, and `raw`.
- Operators: `&` for AND, `|` for OR, `-` for ANDNOT.
- A plain string becomes words joined with `AND`.

There is no `lastUpdatedDate` filter, because arXiv silently rewrites it to `submittedDate`. Use `client.scan_revisions(query, known)` instead. It pages through `sortBy=lastUpdatedDate` and stops after N pages with nothing new.

## Pacing

arXiv's terms allow **at most 1 request every 3 s, and one connection at a time**. The limit covers all your machines and all of arXiv's legacy APIs together. The client enforces it across every call and process on this machine, so callers don't need to sleep between calls.

**The request gate:**
- Every request takes an OS file lock on `~/Library/Caches/arxiv_client/request.lock` on macOS (`~/.cache/…` on Linux).
- The lock is held for the whole connection, so only one request is in flight at a time, even across parallel processes such as Claude Code subagents. It is not held while waiting for pacing, so a PDF waiting out its 15 s doesn't hold up other requests.
- The file records when the last request started. The next request waits until 3 s have passed since the last request of any kind, and a PDF also waits until 15 s have passed since the last PDF, matching the robots.txt `Crawl-delay: 15`.
- The OS releases the lock when a process exits, so a crash can't leave it stuck.
- The gate is released while backing off after a throttling signal.
- If the file can't be opened, the client warns once on stderr and spaces only its own process's requests.

**Time budget:**
- Each HTTP fetch gets a 30 s total budget, including waiting for the gate, pacing and retries. Per-request timeouts are 10 s.
- When the gate is busy for longer than the budget allows, the call fails with `timeout` (exit 6) instead of hanging.
- OS locks aren't first-come-first-served. With more than about 9 processes queued at once, the later ones may time out. Retry them.

**What the caller must do:** after a `throttled` error, wait `retry_after` seconds.

**The 406 marker:** a 406 means a roughly 10-minute IP block, and retrying extends it. So a 406 writes a marker file, `blocked_until`, next to the gate. Every call in every process refuses to send anything until the block expires. If the marker can't be written, the client warns once on stderr and remembers the block in memory for that process only.

The client isn't thread-safe. Use one call at a time.

## Behaviour notes

**Throttling signals:**
- 429, 503 and a 200 with body `Rate exceeded.` are retried with 5 s and 10 s backoff, but only while the 30 s budget allows. After that the call raises `ThrottledError`.
- A 406 is never retried.
- A 400, a 500 or a timeout is never retried. A 200 with an empty body and no Content-Type is treated as transient.

**Paging:**
- The client advances by the raw `<entry>` count.
- It ignores `itemsPerPage`.
- An empty page that appears before `totalResults` is retried 3 times, and then raises `ServerError`.

**The 10000-result limit:** the client never sends `start` ≥ 10000, and it clamps `start + max_results` to ≤ 10000. When that limit cuts results short, `SearchResults.truncated` is `True` and the CLI prints `"truncated": true`. With `strict=True`, the call raises instead.

**Fetching by ID:**
- IDs are validated with the regexes in doc §5 before any request is sent.
- Results are matched by ID, because arXiv doesn't preserve order.
- If a request with an old-style versioned ID returns 500, it's retried once with that ID unversioned. The paper then carries `requested_version`, and its JSON includes `"note": "requested v1, served v3"`.

**PDFs:**
- They're for personal and research use. arXiv PDFs are copyrighted, so don't re-host them.
- For bulk downloads, use arXiv's S3 buckets, not this client.
- A download is accepted only with an `application/pdf` Content-Type and a `%PDF-` header.
- It's written to a temporary `.part` file and renamed into place once complete.
- The default size cap is 50 MB.

## Where it runs

The client is built for the **Claude Code CLI**. It runs locally with normal network access, and the request gate keeps parallel subagents within arXiv's limits.
- If you enable Claude Code's sandbox, allow `export.arxiv.org`, `rss.arxiv.org` and `arxiv.org`. Without that, calls fail with exit code 7 (`network_blocked`).
- `HTTPS_PROXY` is honoured.
- The cache folder (see [Pacing](#pacing)) must be writable for the gate to work across processes.

## Tests

```sh
uv run python -m unittest discover -s tests -t .           # never touches the network
ARXIV_LIVE=1 uv run python -m unittest tests.test_live -v  # 5 live requests, opt-in
```

The suite uses a fake clock and fake sleep, so almost nothing really waits. The exception is one test that runs two real processes through the gate, which takes about a second.

## Caveats from the reference doc

- The feed item fixtures, and every throttling signal, are synthesised from the doc's descriptions, not taken from live captures. The doc's feed probe ran on a Saturday, when every feed is empty. Re-check the fixtures against a real weekday feed.
- **Abstract paragraphs:** the doc says a newline followed by indentation starts a paragraph. But arXiv also hard-wraps lines with `\n  `, mid-sentence, which looks identical. The client treats an indented line as a new paragraph only when the previous line ends a sentence. That misreads a hard wrap that happens to follow a full stop.
- The real `start` limit lies somewhere between 1000 (works) and 10000 (fails). The clamp at 10000 may still be too high.

## License

MIT. See [`LICENSE`](LICENSE).
