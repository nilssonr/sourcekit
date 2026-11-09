# arXiv public APIs: empirical reference

Mapped on 2026-11-07 (a Saturday) from the official docs plus about 140 live requests. Every request was throttled to at most 1 per 3.5 s with one connection at a time, per arXiv's terms of use.

Extended on 2026-11-08 (a Sunday, 19:14 to 20:41 America/New_York) with about 450 more requests sent through the client: every CLI command, a matrix of query syntax (§2.2), and 32 loosely worded requests run through a ladder of queries (§8, §9).

Tags: **[obs]** observed in a live capture · **[doc]** stated in info.arxiv.org docs · **[src]** read in arXiv's open-source code · **[inf]** inference, not verified · **[unmeasured]** tried but no answer, or not tried.

Machine-readable spec for the query API and the feeds: [`arxiv.openapi.yaml`](arxiv.openapi.yaml).

arXiv publishes no OpenAPI spec for any of these interfaces. The only official machine-readable artifact is a small 2007 XSD for the `arxiv:` Atom extension elements (see §5).

---

## 1. The interfaces

| Interface | Base URL | Format | Use it for |
|---|---|---|---|
| Query API | `https://export.arxiv.org/api/query` | Atom 1.0 | Search; fetch metadata by ID |
| OAI-PMH | `https://oaipmh.arxiv.org/oai` | OAI-PMH 2.0 XML | Incremental metadata harvesting |
| RSS/Atom feeds | `https://rss.arxiv.org/{rss,atom}/<cats>` | RSS 2.0 / Atom 1.0 | Today's announcements, per category |
| Paper pages | `https://arxiv.org/abs/<id>`, `https://arxiv.org/pdf/<id>` | HTML / PDF | Linking, PDF download |
| Bulk S3 | `s3://arxiv` (requester pays, us-east-1) | tar + XML manifests | Full PDFs/sources (about 9.2 TB) |

**Terms that apply to all of them [doc]:**
- At most 1 request every 3 s, and a single connection at a time. The limit covers all your machines combined and all the legacy APIs together.
- Metadata is CC0. PDFs and source files are copyrighted: don't re-host them.
- Don't use arXiv branding. Acknowledge arXiv with: "Thank you to arXiv for use of its open access interoperability."

All three XML services sit behind a Fastly/varnish CDN (`via: 1.1 google, 1.1 varnish`). You **may receive cached responses** (see §6).

---

## 2. Query API

### 2.1 Request

`GET` or `POST` (form-encoded, same parameters) to `https://export.arxiv.org/api/query`. `HEAD` also works.
- **Use https only.** Our plain `http://` requests were redirected to https [obs], but a 2026 report says http sometimes hangs instead (openags/paper-search-mcp #101).
- **Prefer GET.** POST works [obs], but according to the arxiv.py maintainer it bypasses the CDN cache, so every request reaches the server and hits rate limits sooner (arxiv.py #209) [issue].
- **Split long `id_list`s.** A GET with about 650–700 IDs returns 414 URI Too Long (arxiv.py #15, #209) [issue].
- **Send your own User-Agent with a contact URL.** In 2023, the default `feedparser` User-Agent got 0 of 3000 requests through (arxiv.py #129) [issue]. Every maintained client sends its own.
- Unknown parameters are ignored [obs].

| Param | Type | Default | Valid | Invalid → |
|---|---|---|---|---|
| `search_query` | string | — | see §2.2 | — |
| `id_list` | comma-separated IDs | — | see §2.3 | malformed ID → 400 |
| `start` | int | `0` | `0`–`9999` | negative or non-int → 400; `10000` or more → **500** |
| `max_results` | int | `10` | `1`–`10000` at least | `0` → **500**; negative or non-int → 400; ≥29999 → **500** |
| `sortBy` | enum | `relevance` | `relevance`, `lastUpdatedDate`, `submittedDate` | 400 |
| `sortOrder` | enum | `descending` | `ascending`, `descending` | 400 |

- At least one of `search_query` and `id_list` is required. Otherwise you get 400: *"Either a search_query or id_list must be specified for the classic API."* [obs]
- **Limits [obs]:**
  - `start=9999` works and `start=10000` returns 500.
  - `max_results=10000` works and `29999` returns 500.
  - The documented 2000-per-call slice is **not enforced**: `max_results=2001` returned 2,001 entries in 6 s.
  - The docs say `max_results > 30000` gives 400. It actually gives **500**.
- **Paging advice [inf]:** for paging, use `max_results ≤ 2000` and keep `start` well below 10000. For bulk work, use OAI-PMH.
- **Count only:** `max_results=0` fails, so use `max_results=1` and read `opensearch:totalResults` [obs].
- **Sorting [obs]:** relevance sorting of a bare `cat:` query does **not** return recent papers. Use `sortBy=submittedDate` for those.

### 2.2 `search_query` syntax [obs]

- **Field prefixes:** `ti` (title), `au` (author), `abs` (abstract), `co` (comment), `jr` (journal ref), `cat` (category), `rn` (report number), `id`, `all`. Each was exercised on its own, `abs` included.
- **No prefix, or an unknown one:** searched as `all:`. So `foo:bar` becomes `all:foo:bar` (no error, 0 hits).
- **Operators:** `AND`, `OR`, `ANDNOT`, **uppercase only**.
  - `NOT` is accepted and rewritten to `ANDNOT`.
  - Lowercase operators become search terms. `ti:x andnot cat:y` is parsed as `ti:x OR all:andnot OR cat:y`.
  - A leading `-` is not negation. `ti:x -cat:y` is parsed as `ti:x OR all:-cat:y`.
  - The **implicit operator between terms is OR**, and the echo spells it out: `all:attention is all you need` comes back as `all:attention OR all:is OR all:all OR all:you OR all:need` (713,701 hits).
- **Phrases and grouping:** `"quoted phrases"` and `( … )` grouping work.
- **Wildcards:** a trailing `*` works in `ti`, `au`, `all`, `cat` and `id` (`id:1706.037*` returns the 100 papers with that prefix).
- **Date filter:** `submittedDate:[FROM TO TO]`. It accepts `YYYYMMDD`, `YYYYMMDDHHMM` or `YYYYMMDDHHMMSS`, all in GMT, and normalises them to 12 digits. A date-only value covers the whole day: `[20240115 TO 20240115]` is echoed as `"202401150000 TO 202401152359"`.
  - **Anything else in the range is an HTTP 500**, not a 400: the `T` form (`20240115T0000`), dashes (`2024-01-15`), a date that isn't on the calendar (`20231399`), and an unclosed range.
    - The client rejects all but the last before sending anything (exit 2), for `--from`, `--to` and `Q.submitted_between`. A range written by hand in `--raw` is sent as typed.
  - A range end in the future is fine.
  - **`lastUpdatedDate:[…]` is silently rewritten to `submittedDate`.** There is no way to filter by last update.
- **Debugging:** the feed `<title>` echoes the query as the server parsed it. `arxiv search --explain` prints it as `server_query`.

#### How terms are matched [obs]

Measured on 2026-11-09 by comparing `totalResults` and the echoed query for pairs of queries. Totals are from that day.

| Input | What the server does | Evidence | So |
|---|---|---|---|
| A word | **Stems it.** `transformer`, `transformers`, `transformation` and `transform` are one term. So are `diffuse` and `diffusion`. | `ti:` each of the four returns the same 34,913 papers. | A single word can't separate "transformer" from "transformation". Add a second term or a phrase. |
| A stopword | **Not indexed in titles and abstracts.** There are 33: a, an, and, are, as, at, be, but, by, for, if, in, into, is, it, no, not, of, on, or, such, that, the, their, then, there, these, they, this, to, was, will, with. `from`, `we`, `all` and `you` are not stopwords. | `ti:WORD` matches 0 papers for each of the 33. `all:is` matches 26, from other fields. `all:attention AND all:is` matches 0. | **Never AND a stopword.** `all:attention AND all:is AND all:all AND all:you AND all:need` returns 0, while the phrase `all:"attention is all you need"` returns 47. |
| A stopword inside a phrase | Dropped from the phrase. | `ti:"the transformer"` returns the same 34,913 as `ti:transformer`. | Phrases are safe with stopwords. |
| A stopword inside a hyphenated word | Ignored. | `ti:state-of-the-art` 1,166. `ti:"state of the art"` 1,128. | No need to quote it. |
| A trailing `?` | **A wildcard for one character.** | `ti:attention?` 2. `ti:attention` 14,795. | Remove a question mark from a person's words. |
| A trailing comma | Ignored. | `ti:attention,` 14,795. | |
| A possessive `'s` | Narrows the match. | `all:Maldacena's` 982. `all:Maldacena` 1,171. | Remove the `'s`. |
| Upper or lower case | Ignored, for words, names and categories. | `cat:cs.lg` = `cat:CS.LG` = `cat:cs.LG` (297,729). | |
| A hyphenated word, unquoted | **Split and ANDed** in that field. | `ti:self-attention` = `ti:self AND ti:attention` (1,254). | Quote it to keep the parts adjacent. |
| A hyphenated word, quoted | A phrase of the parts. | `ti:"self-attention"` = `ti:"self attention"` (1,036). | |
| The same word joined | A different term. | `ti:GPT-4` 259, `ti:"GPT 4"` 254, `ti:GPT4` 15. `ti:selfattention` 0. | OR the spellings. |
| An apostrophe | Splits the word. | `au:O'Brien` 1,891, `au:OBrien` 50. | Keep the apostrophe. |
| Symbols such as `+` | Dropped. | `ti:C++` and `ti:"C++"` both match 16,844 papers: the letter C. | `C++` can't be searched as a title word. Use the abstract plus other terms. |
| A Greek letter | Its own term. | `ti:α-synuclein` 5, `ti:alpha-synuclein` 4. | OR the spellings. |
| Diacritics in a **title** | **Kept.** The accented and plain spellings are different terms, and both occur. | `ti:Fréchet` 457, `ti:Frechet` 119, the OR of the two 576. `ti:Schrödinger` 7,539, `ti:Schrodinger` 1,609. | OR both spellings. |
| Diacritics in an **author** | **Folded**, including the German `ue` form. | `au:Müller` = `au:Muller` = `au:Mueller` (12,233, same papers). | Any spelling works. |
| A full author name | Phrase in either order. | `au:"Geoffrey Hinton"` = `au:"Hinton, Geoffrey"` (59). `au:Hinton AND au:Geoffrey` 60. `au:Hinton` alone 775. | Use surname and first name. |
| Author initials | **Almost never match.** `_` is rewritten to a space. | `au:Hinton_G` is echoed as `au:"Hinton G"` and finds 1. `au:"G Hinton"` 1. `au:"G. E. Hinton"` 0. `au:"Geoffrey E Hinton"` 17. | Don't use initials or a middle initial. |
| `cat:` with an archive that has subcategories | Matches nothing. | `cat:cs` 0. `cat:cs.*` works. `cat:hep-th` works, because it has no subcategories. | Use `cat:cs.*` for a whole archive. |
| `cat:` | Matches **any** of a paper's categories, not only the primary. | `cat:cs.LG` finds 1706.03762, whose primary is cs.CL. | Two `cat:` terms ANDed mean "listed in both". |
| An unknown category | 0 results, no error. | `cat:cs.ZZ`. | |
| `id:` | Matches the unversioned new-style ID only. | `id:1706.03762` 1. `id:1706.03762v5` 0. `id:hep-th/9901001` 0. `id:A OR id:B` 2. | For old-style or versioned IDs use `id_list`. `arxiv search --id` rejects them and names `fetch`. |

- **`sortOrder=ascending` with `sortBy=relevance`** returns the least relevant papers first. The client rejects that pair (exit 2). Ascending order needs a date sort.

#### Claims from other clients' issue trackers, rechecked

- **"Encode the date range's spaces as `+` or `%20`, never `%2B`"** (blazickjp/arxiv-mcp-server #13) [issue]. `%20` works [obs]. `%2B` was not retried.
- **"Never strip diacritics"** (JabRef #16772) [issue]. Half right [obs]: it matters in titles and not in author names. And stripping is not the only risk, because the plain spelling also occurs in titles. OR the two.
- **"An unquoted multi-word `all:` query hangs"** (paper-search-mcp #101) [issue]. **Did not reproduce** [obs]: `all:attention is all you need` answered in normal time, as an OR of the five words.
- **"Expect noise from implicit OR"** (blazickjp #159) [issue]. Confirmed [obs]: 713,701 hits, and the paper with that exact title is not in the first five.

### 2.3 `id_list` [obs]

- **Accepted forms:** new style `2301.00001` (4 digits for 0704–1412, 5 from 1501), old style `hep-th/9901001`, and either with an optional `vN`.
  - An unversioned ID returns the latest version.
  - `vN` returns that version. Metadata (comment, journal_ref, DOI) is **per version**.
- **Bug: some old-style IDs with a non-latest version return HTTP 500** [obs].
  - Fail: `cond-mat/0207270v1`, `hep-th/9711200v1` and `hep-th/9901001v1` (latest is v3 for all three), and `cond-mat/0501001v2` (per cyanheads #53).
  - Also fail: `hep-ph/0307001v1` (latest v2).
  - Work: `astro-ph/0703001v1` and `v2` (latest v4), `quant-ph/9508027v1` (latest v2) and `math/0702001v1` (latest v2). The latest version always works, so a `v1` that is the only version works.
  - The rule is **unknown**. The failing papers were submitted from 1997 to 2005, and the working ones in 1995 and 2007, so it may be a band of submission dates [inf].
  - New-style non-latest versions work.
  - One failing ID fails the **whole request**.
  - **Workaround** (cyanheads/arxiv-mcp-server #53): on a 500, re-request with old-style IDs unversioned, and mark each affected result as "requested vN, served latest". That costs at most 2 requests. `arxiv fetch --explain` lists both request URLs.
- **Missing IDs:** an ID in a valid format that doesn't exist returns 200 and is silently left out. It is not an error.
- **Order and duplicates:** duplicates are removed, and results **do not come back in input order**. Match results to requests by ID.
- **Paging:** `max_results` still applies. 50 IDs with the default `max_results` return only 10 entries.
- **With `search_query`:** you get the intersection, i.e. only the listed papers that match the query.

### 2.4 Response: Atom 1.0

`content-type: application/atom+xml; charset=utf-8` (the header name is lowercase, so look headers up case-insensitively). No `Cache-Control`, `ETag`, `Last-Modified`, `Retry-After` or rate-limit headers [obs].

Namespaces: Atom `http://www.w3.org/2005/Atom` (default), `opensearch` `http://a9.com/-/spec/opensearch/1.1/`, `arxiv` `http://arxiv.org/schemas/atom`.

**Feed** (children in this fixed order) [obs]:

| Element | Card. | Notes |
|---|---|---|
| `id` | 1 | `https://arxiv.org/api/<27-char base64>`, a stable hash of the normalised query. Opaque; may contain `/` and `+`. |
| `title` | 1 | `arXiv Query: search_query=…&id_list=…&start=N&max_results=N` (sort params not shown) |
| `updated` | 1 | Generation time, so it can be stale if CDN-cached. (The docs say midnight; it isn't.) |
| `link` | 1 | `@href` (on `https://arxiv.org/api/query…`) and `@type`. **No `rel`.** Absent in error feeds. |
| `opensearch:itemsPerPage` | 1 | **Echoes the requested `max_results`**, not the number of entries returned |
| `opensearch:totalResults` | 1 | Total matches |
| `opensearch:startIndex` | 1 | Echoes `start`, even past the end |
| `entry` | 0..n | |

**Entry** (fixed order; frequencies from 2,100 sampled entries) [obs]:

| Element | Card. | Freq | Format |
|---|---|---|---|
| `id` | 1 | 100% | `http://arxiv.org/abs/<id>v<N>`: **http, always versioned** |
| `title` | 1 | 100% | Single line; may contain TeX and UTF-8 |
| `updated` | 1 | 100% | Date of the **returned version**. `YYYY-MM-DDTHH:MM:SSZ` |
| `link` rel=alternate | 1 | 100% | `https://arxiv.org/abs/<id>vN`, `type=text/html` |
| `link` rel=related title=pdf | 1 | 100% | `https://arxiv.org/pdf/<id>vN` (no `.pdf`), `type=application/pdf` |
| `summary` | 1 | 100% | Abstract; see whitespace quirks |
| `category` | 1..n | 100% | `@term` (arXiv code) and `@scheme="http://arxiv.org/schemas/atom"`. No ACM/MSC codes seen. |
| `published` | 1 | 100% | **v1** date, the same for every version |
| `arxiv:comment` | 0..1 | 78% | Free text |
| `arxiv:primary_category` | 1 | 100% | `@term` only, **no `@scheme`**; always equals the first `category` |
| `arxiv:journal_ref` | 0..1 | 32% | Free text |
| `author` | 1..n | 100% | `name` (1) and `arxiv:affiliation` (0..n, about 8% of entries). Up to 1,662 authors seen. |
| `arxiv:doi` | **0..n** | 30% | **Bare DOI** `10.x/…`, never a URL. Can repeat (seen twice with the same value), so de-duplicate. |
| `link` rel=related title=doi | = #doi | 30% | `https://doi.org/<doi>`, **no `type`** |

Order: `id, title, updated, link(alt), link(pdf), summary, category+, published, comment?, primary_category, journal_ref?, author+, (arxiv:doi, link[doi])*`. The DOI pairs come after the authors, so an XSD that groups the links in one sequence will reject real responses.

**Data quirks [obs]:**
- Most pre-2010 abstracts start with 2 spaces. Some abstracts, including 2026 ones, contain `\n  ` hard wraps, sometimes mid-sentence. Trim them, and normalise the whitespace.
- 140 of 13,771 author names start with a space (mononyms, collaborations). Some names contain leftover TeX (`Kova{č}i{ć}`).
- **Withdrawn papers have no marker** beyond the comment text, and they keep a PDF link.
- `published` can predate the ID's YYMM by months. Don't derive dates from IDs.
- Old retired archives keep their prefix in `id` (`chao-dyn/…`), but their categories are remapped (`nlin.CD`).
- **Abstract line breaks can be real paragraphs.** arXiv tells authors to indent a line to start a new paragraph (arxiv.py #48). Join hard-wrapped lines, but keep a newline followed by indentation as a paragraph break. Don't collapse all whitespace.
- **Don't trust `totalResults`.** The same query returned 1,000 to 23,000 results across runs (arxiv.py #43), and missing IDs in an `id_list` were once counted (arxiv.py #82) [issue].
- **Page by the raw number of `<entry>` elements**, not by how many you parsed. Otherwise a skipped entry causes repeats or an early stop (arxiv.py #221, open) [issue].
- **Expect elements to be missing.** The paper titled "0" once came back with no `<title>` (arxiv.py #207, since fixed) [issue].
- **Server bugs happen.** In November 2025, `sortBy=submittedDate` returned unsorted results until arXiv fixed it on 2025-11-14 (arxiv.py #184) [issue].

### 2.5 Errors

All errors are an Atom feed (`content-type: application/atom+xml`) with a single error entry [obs]:

```xml
<feed>  <!-- same namespaces -->
  <id>https://arxiv.org/</id>
  <title>arXiv Search Results</title>
  <updated>…</updated>                      <!-- no feed <link> -->
  <opensearch:itemsPerPage>1</opensearch:itemsPerPage>
  <opensearch:totalResults>1</opensearch:totalResults>
  <opensearch:startIndex>0</opensearch:startIndex>
  <entry>
    <id>https://arxiv.org/api/errors#incorrect_id_format_for_foo</id>
    <title>Error</title>
    <updated>…</updated>
    <link href="(same as id)" rel="alternate" type="text/html"/>
    <summary>incorrect id format for foo</summary>
    <author><name>arXiv api core</name></author>
  </entry>
</feed>
```

Detection: check the HTTP status first. In the body, an error entry's `id` starts with `https://arxiv.org/api/errors` or `https://arxiv.org/help/api/user-manual`. It has no `published` and no `category`.

| Status | Cases (entry `id` / `summary`) |
|---|---|
| 400 | no query: `…/api/errors` "Either a search_query or id_list must be specified…"; `…/user-manual#sort` "sortBy must be in: relevance, lastUpdatedDate, submittedDate" / "sortOrder must be in: ascending, descending"; `…/errors#start_must_be_non-negative`, `#start_must_be_an_integer`, `#max_results_must_be_non-negative`, `#max_results_must_be_an_integer`; `…/errors#incorrect_id_format_for_<id>` |
| 500 | `…/api/errors` with the generic text "The server encountered an internal error…". Returned for `max_results=0`, `max_results ≥ 29999`, `start ≥ 10000`, and old-style non-latest versioned IDs. Repeatable, not transient. |
| 200 + 0 entries | Nonexistent but valid-format ID or version; `start` past the end |

**Throttling: several signals.** On 2026-11-09 we observed the 429 and the hung connection [obs]. The rest are reported by other clients [issue].

| Signal | Meaning and handling |
|---|---|
| 429 | Rate limited. arXiv staff (API group, 2025-11-17) describe a 429 with body `Rate exceeded.` as server-wide capacity. Can occur even under 1 request per 3 s (arxiv.py #203). |
| 503 | Staff: "you'd see a 503 for excessive use". Sometimes comes in bursts before 429s (arxiv.py #195). |
| 406, empty body, no Retry-After | Varnish block on your IP for about 10 minutes. **Retrying extends it** (blazickjp #277, 2026-09-19). Wait at least 10 minutes. |
| **200** with plain-text body `Rate exceeded.` | Throttling disguised as success (paper-search-mcp #81; cyanheads detects it). **Check the body or Content-Type before parsing any 200.** |
| 200 with empty body, no Content-Type | Treat as transient (cyanheads #30). |
| Results page with 0 entries partway through paging | Known intermittent behaviour (arxiv.py #43). arxiv.py retries it 3 times. Empty or partial pages were rare at page size 100, versus 1000. |

Per arXiv staff on the API group, limits change over time (2026-02-27), blocks "expire automatically" (2026-05-14), and limits were added on new traffic, bringing more 429s and 503s (2026-06-03). Problems come in **waves** (2023-10, 2024-03, 2026-03): retries during a wave tend to fail again.

**Waves, as observed [obs].** One session on Sunday 2026-11-08, from 19:14 to 20:41 America/New_York, sent about 450 requests through the client, never faster than one per 3 s and never two at once. It still met five bad spells:

| When (UTC, 2026-11-09) | What happened |
|---|---|
| 00:14–00:17 | 13 requests got no response within 10 s, and one got 429. Then normal service: 100 requests with one timeout. |
| 00:33–00:42 | Three different queries in a row got 429, each again after a 65 s wait. Nine throttled calls. |
| 00:49–00:59 | One query got 429 four times across 10 minutes, with waits of 65 s and then 185 s. The queries sent just before and just after it succeeded. |
| 01:00–01:17 | Most requests got no response within 10 s. From 01:18, more than 60 requests in a row were answered. |
| 01:26–01:29 | Two queries not yet in the CDN got 429, and again after a 65 s wait. A query that was in the CDN was answered in the same minute. |

- Between the spells, a request took 0.5–3 s, and a repeated URL took about 0.2 s (a CDN hit).
- **Our own pace did not cause these.** They match what arXiv staff describe as server-wide capacity. The last spell began at 01:00 UTC, which is 20:00 Eastern, the hour §6 already says to avoid.
- **A request that got no response in 10 s still completed at arXiv.** Two abandoned URLs were sent again 12 minutes later. Both were CDN hits (`Age: 729` and `Age: 758`) and answered in 0.2 s.
- **A wait of 60 s was not enough.** A second 429 on the same query calls for a wait of minutes.
- **A timeout was usually followed by more timeouts.** One retry after 20 s worked for an isolated timeout. In a spell it did not.

**Hung connections [issue]:** a pooled keep-alive connection can go silent. Without a read timeout, it blocks forever and holds any global lock (blazickjp #155, 2026-08-17). cyanheads deliberately doesn't retry timed-out requests, because a throttled connection just hangs again (cyanheads #8: retry storms of 108–126 s).

---

## 3. OAI-PMH

`GET https://oaipmh.arxiv.org/oai?verb=…`. It moved here in March 2025; `export.arxiv.org/oai2` is retired [doc].

> **Latency warning [obs].** Responses served from the CDN cache came back in about 0.1 s, but they were **3 to 24 days old** (going by the `Age` header). The `<responseDate>` inside them is the cached date. Every uncached request that touched data took **7 s, 57 s, 64 s, or more than 120 s**: 7 of 19 requests timed out. A request the client gave up on still finished at the server about 124 s later, and the CDN cached it, so retrying the *same URL* minutes later can return instantly. A follow-up `verb=Identify&nocache=…`, which skips the cache and should return a trivial answer, also timed out (20 s), and the same uncached URLs failed in a browser. So the slowness is on the server, not in our client [obs]. Our client never waited long enough to see a 503, because our 120 s timeout is shorter than the CDN's. In a browser, between 17:53:34 and 17:53:56 UTC, four uncached URLs all ended in Fastly's own **HTML** error page `Error 503 first byte timeout` / `Error 54113` / `Varnish cache server`: GetRecord `arXivOld`, ListIdentifiers with `from`, ListIdentifiers with `until`, and ListRecords [obs]. That page is produced by the CDN when arXiv's server sends no first byte. It is not OAI-PMH XML, so check the HTTP status before parsing. An April 2026 report on arXiv's API group describes a `ListRecords` request hanging for about 183 s and then returning `503 "first byte timeout"` with `Retry-After: 0`. A reply from the arXiv side blamed overall API load and cache state, and advised avoiding about 8pm ET, after the daily announcement (https://groups.google.com/a/arxiv.org/g/api/c/6cU-pUjuUAo). Whether this slowness is normal or was peculiar to that day is [unmeasured]. **Clients: use a read timeout of at least 180 s; never base the next `from` on `responseDate`; retry the identical URL after a pause.**

### 3.0 Why it's slow (source code and other users' reports, 2026-11-07)

- **Server code:** [arXiv/oaipmh](https://github.com/arXiv/oaipmh) at `b94fd55` is the live code; its error strings match our captures.
  - `cicd/Dockerfile:44` runs `gunicorn --workers 1 --threads 8 --timeout 0`, and `config.py:14-15` allows 10 database connections with no overflow. There's no request or SQL timeout, so the CDN's first-byte timeout is the only limit.
  - A few slow list queries can occupy every thread, and then even an indexed GetRecord waits. Abandoned requests keep running on the server.
  - List queries filter and sort on a modification-time range, page with OFFSET, and re-run the full query for every page. The database model declares no index on that column; the production schema is unverified.
- **Cache lifetimes**, set via `Surrogate-Control`, which clients never see:
  - GetRecord: about 9 days.
  - Lists whose range runs to today: 1 hour.
  - Lists over a closed past range: 4 days, purged at each announcement (`processors/fetch_list.py:85-88`).
  - Errors: until UTC midnight.
  - Identify, ListSets: 1 year.
- **Default dates:** `until` without `from` defaults `from` to 2005-09-16, so it scans the full history. `from` without `until` runs to today, so it gets only the 1-hour cache.
- **Not just us:**
  - Users reported 503s on the arXiv API group on 5–6 Nov 2026. arXiv staff replied: "This looks like server load, following the daily paper announcement time. We'll look into it." ([thread](https://groups.google.com/a/arxiv.org/g/api/c/11LTCLJdjkE))
  - Independent harvesters failed the same way on 23 Oct, 31 Oct–1 Nov and 2–3 Nov 2026: [alignment-graph #51](https://github.com/ai-safety-graph/alignment-graph/pull/51), [paper-watch #105](https://github.com/enricoroncuzzi/paper-watch/pull/105), [arXiv-digest #9](https://github.com/ManekiMeow/arXiv-digest/pull/9). Each cleared within hours to a day.
  - The identical `Error 503 first byte timeout / Error 54113` was reported in April 2025, and arXiv fixed it by raising the CDN timeout on their side ([thread](https://groups.google.com/a/arxiv.org/g/api/c/hnf-LVxUbrs)).
- **Other clients:** working clients send essentially the same requests as ours. The server never reads request headers, and `:` and `%3A` decode identically. They differ only in tolerance: 60 s timeouts and retries spread over minutes to hours.
  - paper-watch sends POST to avoid stale CDN copies. That trades staleness for always hitting the slow server.
- **Usage that helps:**
  - Use closed past date ranges, with both `from` and `until`, ending yesterday or earlier.
  - Never send `until` alone.
  - Send one request at a time.
  - Avoid about 01:00 UTC, the daily announcement.

- **What popular harvesters do** (fatcat, oadoi/Unpaywall, ResearchHub, arxiv-public-datasets, arxivscraper, summarizepaper; read in code):
  - None gets reliable increments within 30 s per request. They set no timeout, allow 60–120 s, or retry over hours. Most still use the old `/oai2` URL.
  - **fatcat:** one closed day per request (`from` = `until` = D, with D no later than yesterday). Keeps a per-day record of finished days and re-runs any gap in the last 14 days.
  - **oadoi:** never advances its starting point after a failure. It schedules a retry 5 minutes later and doubles the wait each time, spreading retries over hours.
  - **ResearchHub:** overlaps each run by 1 day. It also uses open ranges and reports partial results as success on a page error; don't copy those parts.
  - **summarizepaper:** uses OAI only for licence info, via GetRecord with an 8 s timeout, ignoring failures.
  - **Pitfalls:**
    - **`Retry-After: 0`.** The CDN's 503 carries it, and Sickle, oadoi and arxiv-public-datasets retry immediately; the last one with no limit. **Put a floor under the wait (at least 60 s) and treat 0 as unknown.**
    - **Resumption tokens.** They are just `from` plus an offset, recomputed on every request. Records modified mid-harvest shift, and others get skipped (cyanheads #62). A token stored for a month failed (arxiv-public-datasets #26). **Persist date windows, never tokens.**
    - **Deleted records.** No popular harvester propagates them, although Identify says arXiv keeps them (`persistent`).
    - **Bootstrapping.** Nobody seeds from the weekly Kaggle snapshot (`Cornell-University/arxiv`; mirrored by HF `librarian-bots/arxiv-metadata-snapshot`). They harvest everything over OAI instead.

### 3.1 Verbs

| Verb | Params | Observed |
|---|---|---|
| `Identify` | — | granularity `YYYY-MM-DD`, earliestDatestamp `2005-09-16`, deletedRecord `persistent`, no `<compression>`, adminEmail `help@arxiv.org` [obs] |
| `ListMetadataFormats` | `identifier`? | **4** formats (the docs list 3): `oai_dc`, `arXiv`, `arXivOld`, `arXivRaw`. Schemas at `https://oaipmh.arxiv.org/OAI/<fmt>.xsd`; namespaces `http://arxiv.org/OAI/<fmt>/` [obs] |
| `ListSets` | — | 183 entries, 174 distinct (9 physics archives repeated, so de-duplicate). One page [obs] |
| `GetRecord` | `identifier`, `metadataPrefix` | Works. Identifier is `oai:arXiv.org:<id>` with no version; old-style IDs keep the slash [obs] |
| `ListIdentifiers` / `ListRecords` | `metadataPrefix`, `from`?, `until`?, `set`?, or `resumptionToken` alone | **[unmeasured]**: `ListRecords` was tried once (`set=math:math:NA&from=2026-11-05`, timed out at 120 s). `ListIdentifiers` was tried once normally (`set=cs:cs:AI&from=2026-11-05`, timed out at 120 s) and twice with unusual arguments (`until` only; a `from` that includes a time), both timing out at 75 s. Small sample, all within about 15 minutes on one day. Page size, the `resumptionToken` format and deleted headers are all unknown. Per the docs, tokens have no size or cursor and expire daily. |

**Sets [obs]:** `group[:archive[:SUB]]`. The 8 groups are `physics`, `math`, `cs`, `q-bio`, `q-fin`, `stat`, `eess` and `econ`. Mapping examples: `cs.AI` → `cs:cs:AI`, `math.NA` → `math:math:NA`, `astro-ph.CO` → `physics:astro-ph:CO`, `hep-th` → `physics:hep-th`. Record headers carry only the most specific set.

**Record header [obs]:** `identifier`, `datestamp` (YYYY-MM-DD, last metadata change), `setSpec` 1..n.

### 3.2 Metadata formats [obs]

| Field | `arXivRaw` | `arXiv` | `oai_dc` |
|---|---|---|---|
| ID | `id` | `id` | two `dc:identifier` abs URLs (https and http) |
| Authors | **one flat string** "A and B" | `authors/author/{keyname, forenames, suffix?, affiliation*}` | `dc:creator` "Keyname, Forenames", one per author |
| Versions | `version@version="vN"` → `date` (RFC 822), `size` ("376kb"), `source_type` | none | `dc:date` per version |
| Dates | per version | `created` = **latest version's date, not v1** (cross-checked); `updated` = unclear | — |
| Submitter | `submitter` | — | — |
| Categories | `categories` (space-separated) | `categories` | `dc:subject` category *names* plus the raw MSC string |
| Other | comments, msc-class, license; proxy/report-no/acm-class/journal-ref/doi unobserved | comments, report-no, msc-class, acm-class, journal-ref, doi, license (all 0..1) | comments arrive as a second `dc:description` |

- **Recommendation:** use `arXiv` for structured metadata. Use `arXivRaw` for the version history and the true v1 date. Avoid `oai_dc`.
- **[unmeasured]** `arXivOld` contents: the request timed out.

### 3.3 Errors [obs]

- **Format:** HTTP **200** with `<error code='…'>`. The attribute uses single quotes, so use an XML parser rather than matching text.
- **Message:** the standard OAI-PMH wording for the code, then ` Reason: …`.
- **Seen:**
  - `badVerb`, for a bad verb or no verb.
  - `badArgument` (`from date format must be YYYY-MM-DD`), answered in 153 ms.
- **A `from` that includes a time is rejected correctly:** `from=2026-11-06T00:00:00Z` returns `badArgument` ("from date format must be YYYY-MM-DD"), as the spec requires. But that answer can be slow. Our request timed out at 75 s, and the server's answer is dated about 10 minutes after our request was sent; it's unclear whether it answered our abandoned request or a later browser request. So even validation errors range from 153 ms to many minutes. `until` without `from` timed out at 75 s and is otherwise unmeasured (the spec allows it).
- **[unmeasured]:** cannotDisseminateFormat, idDoesNotExist, noRecordsMatch, badResumptionToken, POST, gzip.

Transport: `content-type: text/xml`, with no charset in the header; the XML declares UTF-8. `<request>` echoes an `http://` base URL [obs].

---

## 4. RSS/Atom announcement feeds

**Caveat:** probed on a Saturday and again on a Sunday, so every feed was empty. The channel structure and the errors were observed. The item structure is from the docs and arXiv's [arxiv-feed](https://github.com/arXiv/arxiv-feed) source. **Re-check on a weekday.**

### Requests

- **URLs:** `https://rss.arxiv.org/rss/<q>` (RSS 2.0) and `https://rss.arxiv.org/atom/<q>` (Atom). `<q>` is an archive (`cs`), a category (`cs.AI`), or several joined with `+`. Case is normalised.
- **Health check:** `https://rss.arxiv.org/feed/status` returns `Status: GOOD Version: 1.1`.
- **Contents:** today's announcements only. The feed rebuilds at 00:00 America/New_York, so a missed day can't be recovered from it. Max 2000 items, with `replace-cross` dropped first [src]. **Replacements of v6 and above are never listed** [src].
- **Weekends and holidays:** 200 with 0 items. RSS has `skipDays` Sat/Sun; Atom has no equivalent. Seen on both days for a category, an archive and a two-category query, in both formats [obs].
- **Errors:** an invalid query returns **HTTP 400 with a valid, empty feed** [obs]. The message is in RSS `channel/description` or Atom `subtitle`, and it lists the valid archive names. One bad part fails the whole `+` query. Always check the status code.
- **Caching:** headers include an unquoted SHA-256 `ETag` and `Cache-Control: max-age=<seconds until next midnight ET>`. There is no `Last-Modified` [obs]. Whether a request with `If-None-Match` gets a 304 is **[unmeasured]**.

### Items [doc/src]

- **ID:** `guid` / Atom `id` = `oai:arXiv.org:<id>v<N>`. `link` = `https://arxiv.org/abs/<id>`, unversioned.
- **Description / summary:** `arXiv:<id>vN Announce Type: <t> \nAbstract: <abstract>`.
- **`arxiv:announce_type`:** `new` | `cross` | `replace` | `replace-cross`.
- **Authors:** `dc:creator` is **one comma-joined string**, with affiliations in parentheses. There are no structured authors.
- **Categories:** `category` 1..n, with the primary not marked. Atom categories have no `scheme`.
- **Licence:** `dc:rights` holds the licence URI.
- **Naming clash with the query API, in the same namespace:** feeds use `arxiv:DOI` and `arxiv:journal_reference`. The query API uses `arxiv:doi` and `arxiv:journal_ref`.
- **Atom entry `updated`:** the feed generation time, not paper data.

---

## 5. Identifiers, links, schema

**ID regexes** [doc-derived, tested against all doc examples plus 10 negatives]:

```
NEW ^(?:arXiv:)?(?P<id>(?P<yymm>\d\d(?:0[1-9]|1[0-2]))\.(?P<num>\d{4,5}))(?:v(?P<version>[1-9]\d*))?$
OLD ^(?:arXiv:)?(?P<id>(?P<archive>[a-z]+(?:-[a-z]+)*)(?:\.(?P<subj>[A-Z]{2}))?/(?P<yymm>\d\d(?:0[1-9]|1[0-2]))(?P<num>\d{3}))(?:v(?P<version>[1-9]\d*))?$
```

Checks a regex can't express:
- **NEW:** 4 digits for 0704–1412, 5 digits from 1501. `YY` 07–99 means 20YY, and 00–06 means 21YY.
- **OLD:** valid for 9107–0703.
- **Both:** the sequence number must be non-zero.
- **Canonical form:** drop the old-style `.SC` subject class (`math.CA/0611800` → `math/0611800`).

**Link targets** (HEAD, 2301.00001) [obs]:
- `/abs/<id>`, `/pdf/<id>` and `/pdf/<id>v1` all return 200 with no redirect.
- The unversioned PDF shows the version it serves only in `Content-Disposition: filename="2301.00001v1.pdf"`.

**`arxiv` namespace schema [obs]:**
- `http://arxiv.org/schemas/atom` redirects to `http://arxiv.org/schemas/atom.xsd`. The https URL is downgraded to http.
- It's an XSD from 2007/2009 defining only `primary_category` (`@term` required; `@scheme` and `@label` optional), `comment`, `affiliation`, `journal_ref` and `doi`.
- It doesn't cover the feeds' `announce_type`, `DOI` or `journal_reference`.

---

## 6. Client design implications

1. **One global throttle** for all arXiv hosts: ≤1 request per 3 s, one connection at a time.
2. **Query API:**
   - **Transport:**
     - https only, GET, our own User-Agent with a contact URL.
     - Connect timeout about 10 s, a total budget of at most 30 s across retries, and a fresh connection per request (`Connection: close`) or dropping the pooled connection after a timeout.
     - Release the global throttle while sleeping in backoff, and pace from request start.
   - **Throttling:**
     - Treat 429, 503, 406 and a 200 `Rate exceeded.` body as throttling.
     - After a 406, wait at least 10 minutes.
     - Check the body before parsing any 200.
     - Don't retry 400s or the known deterministic 500s.
   - **Paging:**
     - Page size 100–200, paging by raw entry count.
     - Retry an empty page that appears partway through.
     - Stop when you reach `totalResults`, but don't otherwise trust it.
   - **`id_list`:**
     - Split it, and send `max_results` equal to the number of IDs.
     - Match results by ID, and report which IDs were not found.
     - On a 500 with old-style versioned IDs, use the unversioned fallback (§2.3).
   - **Queries:**
     - Quote plain multi-word input. Never AND a stopword (§2.2, §8.1).
     - Keep diacritics, and in titles also try the plain spelling.
     - After a timeout, retry the identical URL after a pause, not a changed one (§2.5).
     - After a second 429 on the same request, wait minutes, not 60 s.
     - Detect revisions the arxiv-sanity way: `sortBy=lastUpdatedDate`, stopping after N pages with nothing new, and upserting by ID with the higher version.
   - **Parsing:**
     - Ignore `itemsPerPage`; model `arxiv:doi` as a list.
     - Strip author names; join wrapped abstract lines but keep indented paragraph breaks.
3. **CDN caching:**
   - Query API responses can be at least 19 minutes old (a HEAD hit the cache at Age 1130 s).
   - OAI-PMH responses can be weeks old.
   - Don't use `feed/updated` or `responseDate` as "now".
4. **OAI-PMH: not usable as a live dependency within a 10 s budget.** Uncached requests that need data either hang or end in an HTML 503 "first byte timeout" from the CDN; fast answers are cached copies up to weeks old. Use the query API (search and backfill) plus the RSS feeds (daily changes) instead. If OAI-PMH is used at all, confine it to an optional background job in the fatcat style:
   - One closed day per request, ending yesterday or earlier.
   - A per-day ledger, plus a one-day overlap.
   - On failure, oadoi-style escalating retries that never mark the day done.
   - Abandon each request at 30 s, then retry the identical URL minutes later (the CDN caches the late answer). Wait at least 60 s whatever `Retry-After` says.
   - Avoid about 8pm ET.

   Seed history from the weekly Kaggle snapshot, not a full OAI harvest. Resume from your own last record datestamp. Retry the identical URL after a pause, because a request you abandoned still populates the CDN.
5. **Feeds:**
   - Poll once a day, just after 00:00 America/New_York.
   - Check the HTTP status, because a 400 parses as an empty feed.
   - Backfill missed days from the query API or OAI-PMH.
6. **Dates:**
   - v1 date: query API `published`, or `arXivRaw` `version[v1]/date`.
   - Latest version: query API `updated`, or `arXiv` `created`.

## 7. Open gaps

- **OAI-PMH:** `ListIdentifiers`/`ListRecords` (page size, `resumptionToken`, deleted records), most error codes, POST, gzip, and `arXivOld` contents.
- **Feeds:** item-level structure on a weekday, and 304 handling. To close it, run `arxiv feed cs.AI`, `arxiv feed cs.AI --rss` and `arxiv feed cs` on a weekday and compare the items with §4.
- **Query API:**
  - The exact `max_results` threshold between 10000 and 29999.
  - The redirect status code for http → https.
  - What decides which old-style versioned IDs return 500 (§2.3).
  - Whether a 429 can stick to one URL. One query was throttled for 10 minutes while its neighbours were served (§2.5).
  - Whether `%2B` in a date range still returns 0 results.

Raw captures from both sessions are in the session scratchpads and are not part of the repo.

---

## 8. Query strategy for agents

This section turns §2.2 into a procedure for a caller that starts from a person's loosely worded request. It was measured on 2026-11-09 with 32 such requests from machine learning, HCI and software, physics and maths, and biology. Each request was run through a ladder of queries, from the words as typed up to a hand-built query. "Rank" is the position of the intended paper among the first 10 results.

### 8.1 Never send the request as typed

25 of the requests were a phrase or a sentence. Sent as `all:` words joined with `AND`:
- **19 returned 0 results.** 18 of them contained a stopword. The 19th ANDed four content words.
- **The other 6 returned 1 to 745 results**, and none had the intended paper in the first 10.

**The client has since changed.** Free text (`--all`, or a plain string in the library) now has its stopwords dropped, and an uppercase operator word in it is rejected. The 19 requests were sent again as typed:
- **14 returned results**, from 1 to 471.
- **The intended paper was in the first 10 for 4 of the 13** that had one.
- The words that still sink a query are the ones about the request: *paper*, *study*, *recent*, *latest*. So the rest of this section still applies.

Build the query instead:
1. **Drop stopwords** (§2.2), and drop the words that describe the request and not the paper: *paper*, *study*, *review*, *work*, *latest*, *recent*, *new*, *arXiv*.
2. **Keep two or three content words or phrases.** Each further ANDed word cut the count by 5 to 10 times (335 → 66 → 4, and 13 → 1). The last step can remove every relevant paper.
3. **Put each word where it belongs.** The most distinctive words go in `ti:`. A qualifier goes in `abs:`. A person goes in `au:`, as a surname or as surname and first name (§2.2).
4. **Read `server_query` back** (`--explain`) and check that it is the query you meant.

### 8.2 Which query for which request

| The request has | Send | Measured |
|---|---|---|
| An arXiv ID | `fetch` or `get` | 3 of 3 found in one request, with or without a version. |
| A coined name, such as *Design2Code* or *ProtTrans* | `--all NAME` | Rank 1 of 8, and rank 1 of 3. |
| A coined name that later papers reuse, such as *SWE-bench*, *BERT* or *Mamba* | `--title NAME --any-category FIELD --sort submitted --order ascending` | By relevance the original was not in the first 10 (`all:SWE-bench` 745 results, `ti:SWE-bench` 39). With the field and earliest first it was rank 1 for all three, out of 37, 1,244 and 307. |
| The same, and the name is a unique string, such as *LK-99* | `--raw 'ti:NAME OR abs:NAME' --sort submitted --order ascending` | Rank 1 of 60. This original does not have the name in its title, so `ti:` alone (19 results) cannot find it. **Not for a name that is also a word or an older acronym:** for *BERT* (7,343 results) and *Mamba* (2,354) the earliest results were unrelated papers from before the name existed. |
| An author and a topic | `--author SURNAME --title WORD` | Rank 1 in 4 of 5 and rank 3 in the fifth, with 1 to 6 results. |
| An author and a topic, and no word from the title | `--author SURNAME --all WORD` | Rank 1 or 2 in 4 of 5, with 3 to 8 results. The fifth missed: *AdS/CFT* was coined after the paper it names, so the paper does not contain it. |
| Two authors | `--author A --author B` | 7 results, rank 4. With a title phrase added: rank 1 of 4. |
| The exact title | `--title "PHRASE"` | Rank 1 of 36, rank 2 of 3, and not in the first 10 of 438. Later papers reuse a famous title. Earliest first (§9.3): rank 1, rank 1 and rank 2. |
| A description with no name | The longest phrase the description could share with the title, in `--title`. Failing that, the two most distinctive words, each in `--title` | With the content words in `--all`: 85 to 1,925 results, and rank 3 to 5 or not in the first 10. With two of them in the title: 5 results and rank 1, 11 results and rank 4, and 151 results and rank 4. |
| A topic | `--title "CORE PHRASE" --abstract QUALIFIER` | 27 to 152 results, on topic from the first one, for 4 topics. With the content words in `--all`: 29 to 471 results, and an off-topic paper in the first two for 2 of the 4. |
| A topic, newest wanted | The same with `--sort submitted` | Newest first. For 4 of 4 topics the first six results were from the current year, with no date filter. |
| One bare word | See §9. | |

- **A description can share almost nothing with the title.** "The paper that replaced RNNs with attention" shares one word with *Attention Is All You Need*. The query that found it used a word from its abstract. A caller that does not already know the paper has to ask (§9).
- **A date window bounds the count.** It is not needed for recency. "This year" is `--from YYYY0101`.
- **A category rarely helps a query that already has a title phrase**: 28 → 22, 152 → 149, 27 → 27. It is the only fix for a word that several fields use: `ti:homogenization` went from 9,006 to 124 with three `cs` categories.

### 8.3 When to stop

- **Looking for one paper:** stop when it is in the first 3 results. Skipping the as-typed query, that took at most two queries for 14 of the 16 such requests. The other two needed earliest-first ordering.
- **Looking for a topic:** 20 to 150 results is a list worth reading. Above about 200, add an `--abstract` qualifier or a date. Below about 20, remove the least distinctive term.
- **0 results:** look for a stopword, a term too many, or an accented title word (§2.2). Then remove one term.

### 8.4 Checked on requests not used to build it

The table in §8.2 was then run, one query each, on 11 requests that had not been used to build it.

| Kind | Result |
|---|---|
| Author and title word (2) | Rank 1 of 2, both times. |
| Exact title (2) | Rank 1 of 1. Earliest first: rank 1 of 2,220. |
| Topic (2) | 43 results and 5 results. On topic, except one of the 5. |
| Topic, newest wanted (1) | 729 results, the first six from the last three weeks. |
| Description (2) | Two title words: rank 3 of 13, and not in the first 5 of 35. For the second, a title phrase gave 2 results: the paper and its appendix. |
| Coined name that later papers reuse (2) | **Failed** as first written, with `ti:NAME OR abs:NAME`. Rank 1 for both after the name moved to the title and the field was added. §8.2 has the corrected rule. |

### 8.5 Cost and failure

- **Up to 100 results cost one request**, because the client's page is 100. To see how results spread across fields or authors, ask for 100 with `--format compact` or `--abstract-chars 0`.
- **To count, send `--max 1`** and read `total_results`.
- **`batch` costs 3 s per query.** 20 queries took 61 s.
- **After exit 4 (throttled):** wait `retry_after`. If the same command is throttled again, wait at least 3 minutes (§2.5).
- **After exit 6 (timeout):** wait 20 s and send the identical command once more. A request the client gave up on often still completes at arXiv and is cached: the identical URL then answered in 0.2 s with `Age: 729`. Changing the query throws that away.
- **Avoid the hour around 20:00 America/New_York**, Sunday to Thursday (§2.5).

---

## 9. Disambiguation

When a result set forks, a caller working for a person should ask one multiple-choice question and not guess. In Claude Code that is an `AskUserQuestion` call. This section says what shows each fork, what to offer, and what each answer does to the query. Measured on 2026-11-09.

### 9.1 The forks

| Fork | What shows it | Offer | Each answer becomes |
|---|---|---|---|
| Several papers carry the title | `--title "PHRASE"` returns more than one paper. | The earliest paper, then the top ones by relevance, as `id · year · first author · title`. | `fetch ID` |
| A surname matches several people | `--author SURNAME --max 100` returns that surname with different first names. | The most frequent people, each with their main archive and one title. | `--author "FIRST LAST"` |
| A word is used in several fields | Counts per archive (§9.2), where more than one archive has a real share. | The three or four largest archives, each with its count and one title. | `--category 'ARCHIVE.*'` |
| A topic returns too many | `total_results` above about 200 after the topic query of §8.2. | The commonest primary categories on the first page, or a date window. | `--any-category`, `--from` |
| The request names no paper | Two content-word queries with no plausible hit. | Ask what else the person remembers: an author, a year, a phrase from it. | `--author`, `--from` and `--to`, `--title` |
| An ID with no version | `version` above 1 in the result. | Usually nothing: use the latest. Ask only when the person cites a particular version. | `fetch IDvN` |

### 9.2 The first page is not a sample [obs]

Relevance order over-represents one field. Build field options from counts, not from the first page.

| `ti:` word | Total | Primary archive of the first 50 by relevance | Papers per archive, from `cat:ARCHIVE.*` counts |
|---|---|---|---|
| homogenization | 9,006 | math 39, cs 2, quant-ph 2, stat 2 | math 5,607 · physics 889 · cond-mat 869 · cs 655 · astro-ph 436 |
| transport | 25,396 | math 20, physics 7, cond-mat 7, cs 5 | cond-mat 12,640 · physics 4,628 · math 3,722 · cs 3,150 · quant-ph 1,521 |
| attention | 14,795 | cs 47, econ 2, stat 1 | cs 14,275 · eess 2,348 · stat 782 · q-bio 337 · physics 258 |

- For *transport*, the largest field holds half the papers and 7 of the first 50.
- For *attention*, 337 `q-bio` papers and 2,348 `eess` papers never appear in the first 50.
- The first 100 are no better: for *homogenization* they are 71 math and 4 cs.
- The counts overlap, because `cat:` matches secondary categories. They sum to more than the total.
- **Cost:** one `--max 1` query per archive. Twelve archives took about 40 s. `batch` runs them as one command.

The same holds for people. `au:Hinton` returns 775 papers:

| First N by relevance | Commonest name strings |
|---|---|
| 10 | Jim Hinton 6, Geoffrey Hinton 2 |
| 50 | J. A. Hinton 12, Jim Hinton 10, Samuel R. Hinton 5. Geoffrey Hinton is not in the top five. |
| 100 | Geoffrey Hinton 24, Jim Hinton 23, J. A. Hinton 15 |

- The first 100 hold 20 name strings. Some are plainly one person [inf]: *Jim Hinton*, *J. A. Hinton* and *J. Hinton* all write on gamma-ray astronomy in `astro-ph`.
- **Ask for 100, and merge name strings** that share a first initial and an archive.
- **The archive is the clearest label**: one Hinton is in `cs`, another in `astro-ph`.

### 9.3 When not to ask

- **"The X paper" usually means the original.** Sort earliest first before asking: `--sort submitted --order ascending`. That put the original at rank 1 for 8 of 9 well-known papers. The ninth was at rank 2, behind an unrelated 2017 paper: `ti:"low-rank adaptation"` also matches *Low-Rank Adaptive*, because of stemming. A name that is also a word needs the field as well (§8.2).
- **"Recent", "latest", "new":** sort newest first. Ask for a window only when the count is too large to read.
- **One result, or a first result whose title is the phrase:** fetch it.
- **Ask once per search**, offer at most four options, put the best-supported one first, and show the counts.
