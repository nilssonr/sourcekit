"""Command-line interface. Prints JSON on stdout; errors as JSON on stderr.

    python3 -m arxiv_client search --title "attention is all you need" --max 5
    python3 -m arxiv_client search --all "protein folding" --category q-bio.BM --sort submitted
    python3 -m arxiv_client fetch 1706.03762 hep-th/9901001v1
    python3 -m arxiv_client get 2301.00001
    python3 -m arxiv_client feed cs.AI cs.LG
    python3 -m arxiv_client pdf 2301.00001 --out ./papers

Exit codes: 0 ok, 2 validation, 3 not found, 4 throttled, 5 server error,
6 timeout, 7 network blocked.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from typing import Callable, List, Optional, Sequence

from .client import ArxivClient
from .errors import (
    ArxivError,
    ArxivTimeoutError,
    NetworkBlockedError,
    NotFoundError,
    ServerError,
    ThrottledError,
    ValidationError,
)
from .query import Q
from .transport import DEFAULT_USER_AGENT

EXIT_CODES = (
    (ValidationError, 2, "validation"),
    (NotFoundError, 3, "not_found"),
    (ThrottledError, 4, "throttled"),
    (ServerError, 5, "server_error"),
    (ArxivTimeoutError, 6, "timeout"),
    (NetworkBlockedError, 7, "network_blocked"),
)

SORTS = {"relevance": "relevance", "submitted": "submittedDate", "updated": "lastUpdatedDate"}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="arxiv_client", description="Query arXiv; prints JSON.")
    p.add_argument("--user-agent", help="User-Agent header (default: $ARXIV_USER_AGENT or %s)" % DEFAULT_USER_AGENT)
    sub = p.add_subparsers(dest="command")
    sub.required = True

    s = sub.add_parser("search", help="search the query API")
    for flag in ("title", "author", "abstract", "comment", "journal-ref", "category", "report-number"):
        s.add_argument("--" + flag, action="append", default=[], metavar="TEXT",
                       help="field search (repeatable; multi-word text is a phrase)")
    s.add_argument("--all", action="append", default=[], metavar="TEXT",
                   help="free text: words are ANDed; \"quoted\" parts stay phrases")
    s.add_argument("--raw", help="raw search_query, ANDed with the other filters")
    s.add_argument("--from", dest="date_from", metavar="YYYYMMDD", help="submitted on or after (GMT)")
    s.add_argument("--to", dest="date_to", metavar="YYYYMMDD", help="submitted on or before (GMT)")
    s.add_argument("--sort", choices=sorted(SORTS), default="relevance")
    s.add_argument("--order", choices=("descending", "ascending"), default="descending")
    s.add_argument("--max", type=int, default=10, help="maximum results (default 10)")
    s.add_argument("--strict", action="store_true", help="fail instead of truncating at 10000")

    f = sub.add_parser("fetch", help="fetch papers by ID")
    f.add_argument("ids", nargs="+")

    g = sub.add_parser("get", help="fetch one paper by ID")
    g.add_argument("id")

    fe = sub.add_parser("feed", help="today's announcements")
    fe.add_argument("categories", nargs="+")
    fe.add_argument("--rss", action="store_true", help="use the RSS feed instead of Atom")

    pd = sub.add_parser("pdf", help="download PDFs (personal/research use; max 10 per call)")
    pd.add_argument("ids", nargs="+")
    pd.add_argument("--out", required=True, help="destination directory")
    pd.add_argument("--overwrite", action="store_true")
    return p


def _search_query(args: argparse.Namespace) -> Q:
    parts: List[Q] = []
    fields = {
        "title": args.title, "author": args.author, "abstract": args.abstract,
        "comment": args.comment, "journal_ref": args.journal_ref,
        "category": args.category, "report_number": args.report_number,
    }
    for name, values in fields.items():
        for v in values:
            parts.append(Q.field(name, v))
    for v in args.all:
        parts.append(Q.from_text(v))
    if args.raw:
        parts.append(Q.raw(args.raw))
    if args.date_from or args.date_to:
        today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
        parts.append(Q.submitted_between(args.date_from or "19910101", args.date_to or today))
    if not parts:
        raise ValidationError("search needs at least one filter (--title, --all, --raw, ...)")
    q = parts[0]
    for part in parts[1:]:
        q = q & part
    return q


def _run(args: argparse.Namespace, client: ArxivClient) -> object:
    if args.command == "search":
        q = _search_query(args)
        results = client.search(
            q, max_results=args.max, sort_by=SORTS[args.sort], sort_order=args.order, strict=args.strict
        )
        papers = [p.to_dict() for p in results]
        return {
            "query": q.expr,
            "total_results": results.total_results,
            "truncated": results.truncated,
            "count": len(papers),
            "papers": papers,
        }
    if args.command == "fetch":
        return client.fetch(args.ids).to_dict()
    if args.command == "get":
        return client.get(args.id).to_dict()
    if args.command == "feed":
        return client.feed(args.categories, fmt="rss" if args.rss else "atom").to_dict()
    if args.command == "pdf":
        files = client.download_pdfs(args.ids, args.out, overwrite=args.overwrite)
        return {"files": [r.to_dict() for r in files]}
    raise ValidationError("unknown command %r" % args.command)


def main(
    argv: Optional[Sequence[str]] = None,
    client_factory: Optional[Callable[[str], ArxivClient]] = None,
    stdout=None,
    stderr=None,
) -> int:
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    args = _build_parser().parse_args(argv)
    user_agent = args.user_agent or os.environ.get("ARXIV_USER_AGENT") or DEFAULT_USER_AGENT
    try:
        client = (client_factory or (lambda ua: ArxivClient(ua)))(user_agent)
        result = _run(args, client)
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
        stderr.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return code
    stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
