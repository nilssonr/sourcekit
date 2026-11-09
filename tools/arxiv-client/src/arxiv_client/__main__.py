"""Command-line interface. Prints JSON on stdout; errors as JSON on stderr.

    arxiv search --title "attention is all you need" --max 5
    arxiv search --all "protein folding" --category q-bio.BM --sort submitted
    arxiv search --abstract homogenization --any-category cs.HC --any-category cs.CL --format compact
    arxiv search --id "1706.037*" --format compact
    arxiv batch queries.json --format compact
    arxiv fetch 1706.03762 hep-th/9901001v1 --abstract-chars 300
    arxiv get 2301.00001 --format compact
    arxiv feed cs.AI cs.LG
    arxiv pdf 2301.00001 --out ./papers

Exit codes: 0 ok, 2 validation, 3 not found, 4 throttled, 5 server error,
6 timeout, 7 network blocked, 8 local file error.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Sequence, TextIO, Tuple

from .client import ArxivClient, _check_int
from .errors import (
    ArxivError,
    ArxivTimeoutError,
    LocalFileError,
    NetworkBlockedError,
    NotFoundError,
    ServerError,
    ThrottledError,
    ValidationError,
)
from .models import Paper
from .query import Q, parse_free_text
from .transport import DEFAULT_USER_AGENT

EXIT_CODES = (
    (ValidationError, 2, "validation"),
    (NotFoundError, 3, "not_found"),
    (ThrottledError, 4, "throttled"),
    (ServerError, 5, "server_error"),
    (ArxivTimeoutError, 6, "timeout"),
    (NetworkBlockedError, 7, "network_blocked"),
    (LocalFileError, 8, "local_io"),
)

SORTS = {"relevance": "relevance", "submitted": "submittedDate", "updated": "lastUpdatedDate"}
ORDERS = ("descending", "ascending")
FIELDS = ("title", "author", "abstract", "comment", "journal_ref", "category", "report_number", "id")
QUERY_KEYS = FIELDS + ("all", "raw", "any_category", "from", "to")
BATCH_KEYS = QUERY_KEYS + ("label", "max", "sort", "order")
MAX_BATCH_QUERIES = 20


def _add_query_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--explain",
        action="store_true",
        help="add request_url(s) and server_query (the query as arXiv parsed it) to the output",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="build and validate the query, print it with its URL, send nothing"
    )


def _add_output_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("json", "compact"),
        default="json",
        help="json (default), or compact: one line per paper (id | date | category | title)",
    )
    parser.add_argument(
        "--abstract-chars", type=int, metavar="N", help="JSON only: cut each abstract to N characters (0 drops it)"
    )


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arxiv", description="Query arXiv; prints JSON.")
    user_agent_help = "User-Agent header (default: $ARXIV_USER_AGENT or %s)" % DEFAULT_USER_AGENT
    p.add_argument("--user-agent", help=user_agent_help)
    # The flag is also accepted after the subcommand. SUPPRESS keeps a subparser that was not
    # given the flag from overwriting the value given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--user-agent", default=argparse.SUPPRESS, help=user_agent_help)
    sub = p.add_subparsers(dest="command")
    sub.required = True

    s = sub.add_parser("search", parents=[common], help="search the query API")
    for flag in ("title", "author", "abstract", "comment", "journal-ref", "category", "report-number"):
        s.add_argument(
            "--" + flag,
            action="append",
            default=[],
            metavar="TEXT",
            help="field search (repeatable; multi-word text is a phrase)",
        )
    s.add_argument(
        "--id",
        action="append",
        default=[],
        metavar="ID",
        help="an unversioned new-style ID, or the start of one with a trailing * (1706.037*); repeatable",
    )
    s.add_argument(
        "--all",
        action="append",
        default=[],
        metavar="TEXT",
        help='free text: stopwords are dropped, the other words are ANDed; "quoted" parts stay phrases',
    )
    s.add_argument(
        "--any-category",
        action="append",
        default=[],
        metavar="CAT",
        help="repeatable; matches papers in any of these categories (ORed)",
    )
    s.add_argument("--raw", help="raw search_query, ANDed with the other filters")
    s.add_argument("--from", dest="date_from", metavar="YYYYMMDD", help="submitted on or after (GMT)")
    s.add_argument("--to", dest="date_to", metavar="YYYYMMDD", help="submitted on or before (GMT)")
    s.add_argument("--sort", choices=sorted(SORTS), default="relevance")
    s.add_argument(
        "--order",
        choices=("descending", "ascending"),
        default="descending",
        help="ascending needs --sort submitted or --sort updated",
    )
    s.add_argument("--max", type=int, default=10, help="maximum results (default 10)")
    s.add_argument("--strict", action="store_true", help="fail instead of truncating at 10000")
    _add_query_flags(s)
    _add_output_flags(s)

    b = sub.add_parser(
        "batch", parents=[common], help="run several searches in one call; papers deduplicated across them"
    )
    b.add_argument("file", help="JSON list of query objects, or - for stdin (see the README)")
    _add_query_flags(b)
    _add_output_flags(b)

    f = sub.add_parser("fetch", parents=[common], help="fetch papers by ID")
    f.add_argument("ids", nargs="+")
    f.add_argument("--explain", action="store_true", help="add request_urls (every URL requested) to the output")
    _add_output_flags(f)

    g = sub.add_parser("get", parents=[common], help="fetch one paper by ID")
    g.add_argument("id")
    _add_output_flags(g)

    fe = sub.add_parser("feed", parents=[common], help="today's announcements")
    fe.add_argument("categories", nargs="+")
    fe.add_argument("--rss", action="store_true", help="use the RSS feed instead of Atom")

    pd = sub.add_parser("pdf", parents=[common], help="download PDFs (personal/research use; max 10 per call)")
    pd.add_argument("ids", nargs="+")
    pd.add_argument("--out", required=True, help="destination directory")
    pd.add_argument("--overwrite", action="store_true")
    return p


def _strings(key: str, value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    raise ValidationError("%s must be a string or a list of strings" % key)


def _build_query(spec: Dict[str, Any]) -> Q:
    """Build one query from field values (str or list of str), raw, any_category and dates.

    Every part is ANDed; the ``any_category`` values are ORed with each other.
    """
    parts: List[Q] = []
    for name in FIELDS:
        for v in _strings(name, spec.get(name)):
            parts.append(Q.field(name, v))
    for v in _strings("all", spec.get("all")):
        parts.append(Q.from_text(v))
    raw = spec.get("raw")
    if raw is not None:
        if not isinstance(raw, str):
            raise ValidationError("raw must be a string")
        parts.append(Q.raw(raw))
    cats = _strings("any_category", spec.get("any_category"))
    if cats:
        any_cat = Q.category(cats[0])
        for c in cats[1:]:
            any_cat = any_cat | Q.category(c)
        parts.append(any_cat)
    date_from, date_to = spec.get("from"), spec.get("to")
    if date_from or date_to:
        today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
        parts.append(Q.submitted_between(date_from or "19910101", date_to or today))
    if not parts:
        raise ValidationError("search needs at least one filter (--title, --all, --raw, ...)")
    q = parts[0]
    for part in parts[1:]:
        q = q & part
    return q


def _search_spec(args: argparse.Namespace) -> Dict[str, Any]:
    spec: Dict[str, Any] = {name: getattr(args, name) for name in FIELDS}
    spec.update(
        {
            "all": args.all,
            "raw": args.raw,
            "any_category": args.any_category,
            "from": args.date_from,
            "to": args.date_to,
        }
    )
    return spec


def _load_batch(path: str, stdin: TextIO) -> List[Dict[str, Any]]:
    try:
        if path == "-":
            data = json.load(stdin)
        else:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
    except OSError as e:
        raise ValidationError("could not read batch file %s: %s" % (path, e)) from None
    except ValueError as e:
        raise ValidationError("batch file is not valid JSON: %s" % e) from None
    if not isinstance(data, list) or not data:
        raise ValidationError("batch file must hold a non-empty JSON list of query objects")
    if len(data) > MAX_BATCH_QUERIES:
        raise ValidationError("at most %d queries per batch, got %d" % (MAX_BATCH_QUERIES, len(data)))
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValidationError("batch item %d must be an object" % (i + 1))
        unknown = sorted(set(item) - set(BATCH_KEYS))
        if unknown:
            raise ValidationError(
                "batch item %d has unknown keys %s; allowed: %s" % (i + 1, ", ".join(unknown), ", ".join(BATCH_KEYS))
            )
    return data


def _paper_dict(paper: Paper, abstract_chars: Optional[int] = None) -> Dict[str, Any]:
    """The paper's JSON, without empty affiliation lists. ``abstract_chars`` cuts the
    abstract to that many characters; 0 drops it."""
    d = paper.to_dict()
    d["authors"] = [a if a.get("affiliations") else {"name": a["name"]} for a in d["authors"]]
    if abstract_chars == 0:
        d.pop("summary", None)
    elif abstract_chars and d.get("summary") and len(d["summary"]) > abstract_chars:
        d["summary"] = d["summary"][:abstract_chars].rstrip() + "…"
    return d


def _compact_line(paper: Paper, marker: str = "") -> str:
    date = paper.published.strftime("%Y-%m-%d") if paper.published else "?"
    title = " ".join((paper.title or "").split())
    return "%s%s | %s | %s | %s" % (marker, paper.id, date, paper.primary_category or "?", title)


def _first_page_url(client: ArxivClient, q: Q, max_results: int, sort: str, order: str) -> str:
    return client.query_url(q, 0, min(client.page_size, max_results), SORTS[sort], order)


def _dropped_words(spec: Dict[str, Any]) -> List[str]:
    """The stopwords left out of the query's free text (``all``), in the order given."""
    return [w for v in _strings("all", spec.get("all")) for w in parse_free_text(v).dropped]


def _dropped_line(dropped: List[str]) -> List[str]:
    return ["   dropped [%s]" % " ".join(dropped)] if dropped else []


def _explain(target: Dict[str, Any], results, dropped: List[str]) -> None:
    target["server_query"] = results.server_query
    target["request_urls"] = list(results.request_urls)
    target["dropped_words"] = dropped


def _run_batch(args: argparse.Namespace, client: ArxivClient, stdin: TextIO) -> Tuple[object, Optional[str]]:
    items = _load_batch(args.file, stdin)
    # Build and validate every query before sending any request.
    planned = []
    labels = set()
    for i, item in enumerate(items):
        label = item.get("label", "q%d" % (i + 1))
        if not isinstance(label, str) or not label.strip():
            raise ValidationError("batch item %d: label must be a non-empty string" % (i + 1))
        if label in labels:
            raise ValidationError("batch item %d: duplicate label %r" % (i + 1, label))
        labels.add(label)
        sort = item.get("sort", "relevance")
        if not isinstance(sort, str) or sort not in SORTS:
            raise ValidationError("batch item %d: sort must be one of %s" % (i + 1, ", ".join(sorted(SORTS))))
        order = item.get("order", "descending")
        if order not in ORDERS:
            raise ValidationError("batch item %d: order must be descending or ascending" % (i + 1))
        try:
            max_results = _check_int("max", item.get("max", 10), 1)
            q = _build_query(item)
            results = client.search(q, max_results=max_results, sort_by=SORTS[sort], sort_order=order)
        except ValidationError as e:
            raise ValidationError("batch item %d (%s): %s" % (i + 1, label, e)) from None
        planned.append((label, q, results, max_results, sort, order, _dropped_words(item)))

    if args.dry_run:
        plan = [
            {
                "label": label,
                "query": q.expr,
                "dropped_words": dropped,
                "request_url": _first_page_url(client, q, m, sort, order),
            }
            for label, q, _results, m, sort, order, dropped in planned
        ]
        return {"dry_run": True, "queries": plan}, "\n".join(
            "\n".join(["## %s  [%s]" % (p["label"], p["query"]), *_dropped_line(p["dropped_words"]), p["request_url"]])
            for p in plan
        )

    queries: List[Dict[str, Any]] = []
    papers: Dict[str, Dict[str, Any]] = {}
    lines: List[str] = []
    for label, q, results, _max_results, _sort, _order, dropped in planned:
        try:
            found = list(results)
        except ArxivError as e:
            e.completed_queries = list(queries)  # type: ignore[attr-defined]
            e.completed_papers = dict(papers)  # type: ignore[attr-defined]
            raise
        ids = []
        lines.append(
            "## %s  [%s]  total=%s%s"
            % (label, q.expr, results.total_results, "  (truncated)" if results.truncated else "")
        )
        if args.explain:
            lines.append("   server [%s]" % results.server_query)
            lines.extend(_dropped_line(dropped))
        for paper in found:
            ids.append(paper.id)
            new = paper.id not in papers
            if new:
                papers[paper.id] = _paper_dict(paper, args.abstract_chars)
                papers[paper.id]["found_by"] = []
            papers[paper.id]["found_by"].append(label)
            lines.append(_compact_line(paper, "* " if new else "  "))
        lines.append("")
        entry = {
            "label": label,
            "query": q.expr,
            "total_results": results.total_results,
            "truncated": results.truncated,
            "count": len(ids),
            "ids": ids,
        }
        if args.explain:
            _explain(entry, results, dropped)
        queries.append(entry)
    lines.append("# %d unique papers from %d queries (* = first seen)" % (len(papers), len(queries)))
    result = {"queries": queries, "papers": papers, "unique": len(papers)}
    return result, "\n".join(lines)


def _run(args: argparse.Namespace, client: ArxivClient, stdin: TextIO) -> Tuple[object, Optional[str]]:
    """Returns (JSON result, compact text or None)."""
    if args.command == "search":
        spec = _search_spec(args)
        q = _build_query(spec)
        dropped = _dropped_words(spec)
        results = client.search(
            q, max_results=args.max, sort_by=SORTS[args.sort], sort_order=args.order, strict=args.strict
        )
        if args.dry_run:
            url = _first_page_url(client, q, args.max, args.sort, args.order)
            result = {"dry_run": True, "query": q.expr, "dropped_words": dropped, "request_url": url}
            return result, "\n".join(["## [%s]" % q.expr, *_dropped_line(dropped), url])
        found = list(results)
        result = {
            "query": q.expr,
            "total_results": results.total_results,
            "truncated": results.truncated,
            "count": len(found),
            "papers": [_paper_dict(p, args.abstract_chars) for p in found],
        }
        if args.explain:
            _explain(result, results, dropped)
        header = "## [%s]  total=%s  shown=%d%s" % (
            q.expr,
            results.total_results,
            len(found),
            "  (truncated)" if results.truncated else "",
        )
        lines = [header]
        if args.explain:
            lines.append("   server [%s]" % results.server_query)
            lines.extend(_dropped_line(dropped))
        return result, "\n".join(lines + [_compact_line(p) for p in found])
    if args.command == "batch":
        return _run_batch(args, client, stdin)
    if args.command == "fetch":
        fetched = client.fetch(args.ids)
        result = {
            "papers": {k: _paper_dict(v, args.abstract_chars) for k, v in fetched.papers.items()},
            "not_found": list(fetched.not_found),
        }
        lines = [_compact_line(p) for p in fetched.papers.values()]
        if fetched.not_found:
            lines.append("# not found: %s" % " ".join(fetched.not_found))
        if args.explain:
            result["request_urls"] = list(fetched.request_urls)
            lines.extend("# request: %s" % url for url in fetched.request_urls)
        return result, "\n".join(lines)
    if args.command == "get":
        paper = client.get(args.id)
        return _paper_dict(paper, args.abstract_chars), _compact_line(paper)
    if args.command == "feed":
        return client.feed(args.categories, fmt="rss" if args.rss else "atom").to_dict(), None
    if args.command == "pdf":
        files = client.download_pdfs(args.ids, args.out, overwrite=args.overwrite)
        return {"files": [r.to_dict() for r in files]}, None
    raise ValidationError("unknown command %r" % args.command)


def main(
    argv: Optional[Sequence[str]] = None,
    client_factory: Optional[Callable[[str], ArxivClient]] = None,
    stdout=None,
    stderr=None,
    stdin=None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    stdin = stdin or sys.stdin
    args = _build_parser().parse_args(argv)
    if getattr(args, "abstract_chars", None) is not None and args.abstract_chars < 0:
        stderr.write(json.dumps({"error": "validation", "message": "--abstract-chars must be 0 or more"}) + "\n")
        return 2
    user_agent = args.user_agent or os.environ.get("ARXIV_USER_AGENT") or DEFAULT_USER_AGENT
    try:
        client = (client_factory or ArxivClient)(user_agent)
        result, compact = _run(args, client, stdin)
    except ArxivError as e:
        code, kind = 1, "error"
        for cls, c, k in EXIT_CODES:
            if isinstance(e, cls):
                code, kind = c, k
                break
        payload = {"error": kind, "message": str(e)}
        if isinstance(e, ThrottledError):
            payload["retry_after"] = e.retry_after
            payload["throttle_kind"] = e.kind
        completed = getattr(e, "completed", None)
        if completed:
            payload["completed"] = [r.to_dict() for r in completed]
        completed_queries = getattr(e, "completed_queries", None)
        if completed_queries:
            payload["completed"] = completed_queries
            payload["papers"] = getattr(e, "completed_papers", {})
        stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return code
    if getattr(args, "format", "json") == "compact" and compact is not None:
        stdout.write(compact + "\n")
    else:
        stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
