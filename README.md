# research-client

A standard-library Python client for arXiv's query API (`export.arxiv.org/api/query`) and its daily announcement feeds (`rss.arxiv.org`), plus single-PDF download. It's built to ship in a Claude skill's `scripts/` folder:
- Python 3.9 or later.
- No dependencies.
- No install step.

The behaviour follows the empirical reference in [`docs/arxiv-api.md`](docs/arxiv-api.md). Section 6 of that doc is the requirements list.

Thank you to arXiv for use of its open access interoperability.

## Layout

```
arxiv_client/     the package (self-contained; a skill can ship a copy in its scripts/ folder)
tests/            unittest suite; fixtures in tests/fixtures/arxiv/
```

## CLI (prints JSON)

Run it from the folder that contains `arxiv_client/`:

```sh
python3 -m arxiv_client search --title "attention is all you need" --max 5
python3 -m arxiv_client search --all "protein folding" --category q-bio.BM --sort submitted --max 20
python3 -m arxiv_client search --author Hinton --from 20230101 --to 20231231
python3 -m arxiv_client fetch 1706.03762 hep-th/9901001v1
python3 -m arxiv_client get 2301.00001
python3 -m arxiv_client feed cs.AI cs.LG          # add --rss for the RSS feed
python3 -m arxiv_client pdf 2301.00001 --out ./papers
```

**Search filters:**
- Field flags (`--title`, `--author`, `--abstract`, `--comment`, `--journal-ref`, `--category`, `--report-number`) can be repeated. Their text becomes a quoted phrase when it has more than one word.
- `--all` is free text. Its words are joined with `AND`, and `"quoted"` parts stay phrases.
- `--raw` is passed through verbatim.
- All filters are joined with `AND`.

**Output and errors:** JSON goes to stdout. Errors go to stderr as `{"error": kind, "message": …}`, with `retry_after` added when arXiv throttled the request.

| Exit | `error` | Meaning |
|---|---|---|
| 0 | | success |
| 2 | `validation` | bad input, or HTTP 400 from arXiv |
| 3 | `not_found` | paper or PDF does not exist |
| 4 | `throttled` | 429, 503, 406, or a 200 whose body is `Rate exceeded.`. Wait `retry_after` seconds |
| 5 | `server_error` | other HTTP errors, malformed responses |
| 6 | `timeout` | the request or its 30 s budget timed out |
| 7 | `network_blocked` | DNS failure, refused connection, or a proxy refused the tunnel. In a sandbox, allow the domain |

**User-Agent:** set by `--user-agent`, then `$ARXIV_USER_AGENT`, then the default `research-client/0.1`.

## Library

```python
from arxiv_client import ArxivClient, Q

client = ArxivClient()
for paper in client.search(Q.title("diffusion") & Q.category("cs.CV"), max_results=50,
                           sort_by="submittedDate"):
    print(paper.versioned_id, paper.title)

result = client.fetch(["1706.03762", "hep-th/9901001v1", "2401.99999"])
result.papers      # keyed by the ID strings you passed in
result.not_found   # ["2401.99999"]

feed = client.feed(["cs.AI"])            # empty (not an error) at weekends
client.download_pdf("2301.00001", "./papers")
```

**Query builder `Q`:**
- Field constructors: `title`, `author`, `abstract`, `comment`, `journal_ref`, `category`, `report_number`, `id`, `all`.
- Other constructors: `phrase`, `submitted_between`, and `raw`.
- Operators: `&` for AND, `|` for OR, `-` for ANDNOT.
- A plain string becomes words joined with `AND`.

There is no `lastUpdatedDate` filter, because arXiv silently rewrites it to `submittedDate`. Use `client.scan_revisions(query, known)` instead. It pages through `sortBy=lastUpdatedDate` and stops after N pages with nothing new.

## Pacing: who is responsible for what

arXiv's terms allow **at most 1 request every 3 s, and one connection at a time**. The limit covers all your machines and all of arXiv's legacy APIs together.

**What the library does:**
- Within one call, it spaces its own requests at least 3 s apart, measured from request start. That covers search pages, ID chunks of 100, retries and the old-style fallback.
- PDFs in one `download_pdfs` call are spaced 15 s apart, matching the robots.txt `Crawl-delay: 15`.
- Each HTTP fetch gets a 30 s total budget, including pacing and retries. Per-request timeouts are 10 s.

**What the caller (the skill) must do:**
- Space *separate* calls at least 3 s apart.
- Never run calls in parallel.
- After a `throttled` error, wait `retry_after` seconds.

**The one state shared between calls:** a 406 means a roughly 10-minute IP block, and retrying extends it. So a 406 writes a marker file to `~/Library/Caches/arxiv_client/blocked_until` on macOS (`~/.cache/…` on Linux, `%LOCALAPPDATA%\…` on Windows). Every call in every process refuses to send anything until the block expires. If the marker can't be written, the client warns once on stderr and remembers the block in memory for that process only.

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

- **Claude Code:** the script runs locally with normal network access. If you enable Claude Code's sandbox, allow `export.arxiv.org`, `rss.arxiv.org` and `arxiv.org`.
- **claude.ai and Claude Desktop chat:** uploaded skills run in Anthropic's cloud sandbox, and its default network allowlist doesn't include arXiv.
  - Team/Enterprise owners can add the three hosts.
  - On Pro/Max, Anthropic's docs don't say whether you can.
  - Without access, calls fail with exit code 7 (`network_blocked`).
  - `HTTPS_PROXY` is honoured.

## Tests

```sh
python3 -m unittest discover -s tests -t .           # never touches the network
ARXIV_LIVE=1 python3 -m unittest tests.test_live -v  # 3 live requests, opt-in
```

The suite uses a fake clock and fake sleep, so nothing really waits.

## Caveats from the reference doc

- The feed item fixtures, and every throttling signal, are synthesised from the doc's descriptions, not taken from live captures. The doc's feed probe ran on a Saturday, when every feed is empty. Re-check the fixtures against a real weekday feed.
- **Abstract paragraphs:** the doc says a newline followed by indentation starts a paragraph. But arXiv also hard-wraps lines with `\n  `, mid-sentence, which looks identical. The client treats an indented line as a new paragraph only when the previous line ends a sentence. That misreads a hard wrap that happens to follow a full stop.
- The real `start` limit lies somewhere between 1000 (works) and 10000 (fails). The clamp at 10000 may still be too high.

## License

MIT. See [`LICENSE`](LICENSE). A copy also sits in `arxiv_client/LICENSE`, so the notice travels with the package when it is copied into a skill.
