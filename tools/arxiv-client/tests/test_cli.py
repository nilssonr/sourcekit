import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from typing import List, NamedTuple, Optional
from unittest import mock

from arxiv_client.__main__ import main

from tests.helpers import (
    FakeClock,
    FakeOpener,
    FakeResponse,
    Step,
    fixture,
    make_client,
    make_entry,
    make_feed,
    part_file_failing_after,
    xml_response,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PDF = b"%PDF-1.5\n" + b"x" * 100
PDF_HEADERS = {"content-type": "application/pdf"}


class CliRun(NamedTuple):
    code: int
    out: str
    err: str
    opener: FakeOpener            # the requests the command sent
    user_agents: List[str]        # the User-Agent each client was built with


def run_cli(argv: List[str], steps: List[Step], stdin: Optional[str] = None, **client_kw) -> CliRun:
    """Run the command against canned responses. Nothing touches the network."""
    clock = FakeClock()
    opener = FakeOpener(clock, steps)
    out, err = io.StringIO(), io.StringIO()
    user_agents: List[str] = []

    def factory(ua: str):
        user_agents.append(ua)
        return make_client(clock, opener, user_agent=ua, **client_kw)

    code = main(argv, client_factory=factory, stdout=out, stderr=err,
                stdin=io.StringIO(stdin) if stdin is not None else None)
    return CliRun(code, out.getvalue(), err.getvalue(), opener, user_agents)


def feed_of(*ids, total=None):
    return xml_response(make_feed([make_entry(i, title="Paper %s" % i) for i in ids], total=total))


def error_payload(run: CliRun) -> dict:
    return json.loads(run.err)


class CommandTests(unittest.TestCase):
    def test_search_prints_the_query_and_its_papers_as_json(self):
        run = run_cli(
            ["search", "--title", "attention is all you need", "--category", "cs.CL", "--max", "2"],
            [xml_response(make_feed([make_entry("1706.03762", 7), make_entry("2401.00001")], total=2))],
        )
        self.assertEqual(run.code, 0, run.err)
        data = json.loads(run.out)
        self.assertEqual(data["count"], 2)
        self.assertFalse(data["truncated"])
        self.assertEqual(data["query"], 'ti:"attention is all you need" AND cat:cs.CL')
        self.assertEqual(data["papers"][0]["versioned_id"], "1706.03762v7")

    def test_free_text_and_dates_become_one_query(self):
        run = run_cli(
            ["search", "--all", "protein folding", "--from", "20230101", "--to", "20230131", "--sort", "submitted"],
            [xml_response(make_feed([], total=0))],
        )
        self.assertEqual(run.code, 0, run.err)
        p = run.opener.requests[0].params
        self.assertEqual(p["search_query"], ["all:protein AND all:folding AND submittedDate:[20230101 TO 20230131]"])
        self.assertEqual(p["sortBy"], ["submittedDate"])

    def test_search_without_a_filter_is_a_validation_error(self):
        run = run_cli(["search"], [])
        self.assertEqual(run.code, 2)
        self.assertEqual(error_payload(run)["error"], "validation")

    def test_fetch_lists_missing_ids_as_not_found(self):
        run = run_cli(["fetch", "2401.00001", "2401.00002"], [xml_response(make_feed([make_entry("2401.00001")]))])
        self.assertEqual(run.code, 0, run.err)
        data = json.loads(run.out)
        self.assertEqual(list(data["papers"]), ["2401.00001"])
        self.assertEqual(data["not_found"], ["2401.00002"])

    def test_feed_prints_the_days_items(self):
        run = run_cli(["feed", "cs.AI", "--rss"], [xml_response(fixture("rss_weekday.xml"), ct="application/rss+xml")])
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(json.loads(run.out)["count"], 2)

    def test_each_error_kind_has_its_exit_code_and_json_on_stderr(self):
        cases = [
            (["get", "foo"], [], 2, "validation"),
            (["get", "2401.99999"], [xml_response(make_feed([], total=0))], 3, "not_found"),
            (["get", "2401.00001"], [FakeResponse(406, b"", {})], 4, "throttled"),
            (["get", "2401.00001"], [xml_response(fixture("query_error_500.xml"), status=500)], 5, "server_error"),
            (["get", "2401.00001"], [socket.timeout("timed out")], 6, "timeout"),
            (["get", "2401.00001"], [urllib.error.URLError(socket.gaierror(8, "no dns"))], 7, "network_blocked"),
        ]
        for argv, steps, code, kind in cases:
            with self.subTest(kind=kind):
                run = run_cli(argv, steps)
                self.assertEqual(run.code, code, run.err)
                self.assertEqual(error_payload(run)["error"], kind)
                self.assertEqual(run.out, "")

    def test_a_throttled_error_carries_retry_after(self):
        run = run_cli(["get", "2401.00001"], [FakeResponse(406, b"", {})])
        self.assertEqual(error_payload(run)["retry_after"], 600)

    def test_a_pdf_that_cannot_be_written_is_a_local_io_error_listing_the_completed_files(self):
        with tempfile.TemporaryDirectory() as out:
            run = run_cli(["pdf", "2301.00001v1", "2301.00002v1", "--out", out],
                          [FakeResponse(200, PDF, PDF_HEADERS), FakeResponse(200, PDF, PDF_HEADERS)],
                          create_part_file=part_file_failing_after(1))
        self.assertEqual(run.code, 8)
        payload = error_payload(run)
        self.assertEqual(payload["error"], "local_io")
        self.assertEqual([c["id"] for c in payload["completed"]], ["2301.00001"])

    def test_the_default_user_agent_is_used_without_flag_or_environment(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("ARXIV_USER_AGENT", None)
            run = run_cli(["get", "2401.00001"], [feed_of("2401.00001")])
        self.assertEqual(run.user_agents, ["arxiv-client/0.1"])

    def test_the_environment_user_agent_beats_the_default(self):
        with mock.patch.dict(os.environ, {"ARXIV_USER_AGENT": "env-agent/2"}):
            run = run_cli(["get", "2401.00001"], [feed_of("2401.00001")])
        self.assertEqual(run.user_agents, ["env-agent/2"])

    def test_the_user_agent_flag_beats_the_environment_and_is_sent(self):
        with mock.patch.dict(os.environ, {"ARXIV_USER_AGENT": "env-agent/2"}):
            run = run_cli(["--user-agent", "flag-agent/3", "get", "2401.00001"], [feed_of("2401.00001")])
        self.assertEqual(run.user_agents, ["flag-agent/3"])
        self.assertEqual(run.opener.requests[0].headers["user-agent"], "flag-agent/3")

    def test_python_dash_m_entry_point_prints_help(self):
        # --help needs no network.
        proc = subprocess.run(
            [sys.executable, "-m", "arxiv_client", "--help"],
            cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("search", proc.stdout)


class BatchTests(unittest.TestCase):
    def batch_file(self, items) -> str:
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "q.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f)
        return path

    def test_batch_deduplicates_papers_and_records_which_queries_found_them(self):
        path = self.batch_file([
            {"label": "first", "title": "diffusion", "any_category": ["cs.CV", "cs.LG"], "max": 5},
            {"abstract": "mode collapse", "sort": "submitted"},
        ])
        run = run_cli(["batch", path], [feed_of("2401.00001", "2401.00002"), feed_of("2401.00002", "2401.00003")])
        self.assertEqual(run.code, 0, run.err)
        data = json.loads(run.out)
        self.assertEqual(data["unique"], 3)
        self.assertEqual([q["label"] for q in data["queries"]], ["first", "q2"])
        self.assertEqual(data["queries"][0]["query"], "ti:diffusion AND (cat:cs.CV OR cat:cs.LG)")
        self.assertEqual(data["queries"][1]["ids"], ["2401.00002", "2401.00003"])
        self.assertEqual(data["papers"]["2401.00002"]["found_by"], ["first", "q2"])
        self.assertEqual(data["papers"]["2401.00001"]["found_by"], ["first"])
        self.assertEqual(run.opener.requests[1].params["sortBy"], ["submittedDate"])
        starts = [r.at for r in run.opener.requests]
        self.assertGreaterEqual(starts[1] - starts[0], 3.0)

    def test_every_query_is_validated_before_any_request_is_sent(self):
        bad_items = [
            {"title": "x", "sort": "newest"}, {"title": "x", "colour": "red"}, {"max": 3},
            {"title": "x", "max": 0}, {"title": 5}, "not an object",
            {"title": "x", "max": None}, {"title": "x", "max": "10"}, {"title": "x", "max": True},
            {"title": "x", "label": 0}, {"title": "x", "label": ""}, {"title": "x", "label": False},
            {"title": "x", "label": "q1"},  # q1 is the first item's default label
            {"title": "x", "sort": ["submitted"]}, {"title": "x", "sort": {"a": 1}},
        ]
        for bad in bad_items:
            with self.subTest(bad=bad):
                run = run_cli(["batch", self.batch_file([{"title": "fine"}, bad])], [])
                self.assertEqual(run.code, 2, run.err)
                self.assertEqual(error_payload(run)["error"], "validation")
                self.assertEqual(run.opener.requests, [])

    def test_duplicate_labels_are_rejected(self):
        run = run_cli(["batch", self.batch_file([{"label": "x", "title": "a"}, {"label": "x", "title": "b"}])], [])
        self.assertEqual(run.code, 2)
        self.assertIn("duplicate label", error_payload(run)["message"])
        self.assertEqual(run.opener.requests, [])

    def test_more_than_20_queries_are_rejected(self):
        run = run_cli(["batch", self.batch_file([{"title": "x"}] * 21)], [])
        self.assertEqual(run.code, 2)
        self.assertIn("at most 20", error_payload(run)["message"])

    def test_an_empty_list_an_object_or_invalid_json_is_rejected(self):
        for content in ("[]", "{}", "not json"):
            with self.subTest(content=content):
                run = run_cli(["batch", "-"], [], stdin=content)
                self.assertEqual(run.code, 2)
                self.assertEqual(error_payload(run)["error"], "validation")

    def test_a_missing_batch_file_is_a_validation_error(self):
        run = run_cli(["batch", os.path.join(tempfile.gettempdir(), "arxiv-client-missing-batch.json")], [])
        self.assertEqual(run.code, 2)
        self.assertEqual(error_payload(run)["error"], "validation")

    def test_a_dash_reads_the_queries_from_stdin(self):
        run = run_cli(["batch", "-"], [feed_of("2401.00001")], stdin=json.dumps([{"all": "protein folding"}]))
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(json.loads(run.out)["queries"][0]["query"], "all:protein AND all:folding")

    def test_throttling_midway_reports_the_completed_queries_on_stderr(self):
        path = self.batch_file([{"label": "a", "title": "x"}, {"label": "b", "title": "y"}])
        run = run_cli(["batch", path], [feed_of("2401.00001"), FakeResponse(406, b"", {})])
        self.assertEqual(run.code, 4)
        self.assertEqual(run.out, "")
        payload = error_payload(run)
        self.assertEqual(payload["error"], "throttled")
        self.assertEqual([q["label"] for q in payload["completed"]], ["a"])
        self.assertEqual(payload["completed"][0]["ids"], ["2401.00001"])
        self.assertEqual(payload["papers"]["2401.00001"]["found_by"], ["a"])
        self.assertIn("title", payload["papers"]["2401.00001"])

    def test_compact_batch_output_marks_first_appearances_and_counts_unique_papers(self):
        path = self.batch_file([{"label": "a", "title": "x", "max": 1}, {"label": "b", "title": "y"}])
        run = run_cli(["batch", path, "--format", "compact"],
                      [feed_of("2401.00001", total=40), feed_of("2401.00001", "2401.00002")])
        self.assertEqual(run.code, 0, run.err)
        lines = run.out.splitlines()
        self.assertEqual(lines[0], "## a  [ti:x]  total=40")
        self.assertEqual(lines[1], "* 2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001")
        self.assertIn("  2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001", lines)
        self.assertIn("* 2401.00002 | 2024-01-01 | cs.LG | Paper 2401.00002", lines)
        self.assertEqual(lines[-1], "# 2 unique papers from 2 queries (* = first seen)")


class OutputFormatTests(unittest.TestCase):
    def test_compact_search_output_has_a_header_and_one_line_per_paper(self):
        run = run_cli(
            ["search", "--abstract", "homogenization", "--any-category", "cs.HC", "--any-category", "cs.CL",
             "--format", "compact", "--max", "1"],
            [feed_of("2401.00001", total=7)],
        )
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.out.splitlines(), [
            "## [abs:homogenization AND (cat:cs.HC OR cat:cs.CL)]  total=7  shown=1",
            "2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001",
        ])

    def test_compact_fetch_output_lists_the_ids_not_found(self):
        run = run_cli(["fetch", "2401.00001", "2401.00002", "--format", "compact"], [feed_of("2401.00001")])
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.out.splitlines(), ["2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001",
                                                "# not found: 2401.00002"])

    def test_abstract_chars_zero_drops_the_abstract(self):
        run = run_cli(["search", "--title", "x", "--abstract-chars", "0"],
                      [xml_response(make_feed([make_entry("2401.00001", summary="word " * 100)]))])
        paper = json.loads(run.out)["papers"][0]
        self.assertNotIn("summary", paper)
        self.assertEqual(paper["authors"], [{"name": "Someone"}])

    def test_abstract_chars_cuts_the_abstract_and_marks_the_cut(self):
        run = run_cli(["fetch", "2401.00001", "--abstract-chars", "200"],
                      [xml_response(make_feed([make_entry("2401.00001", summary="word " * 100)]))])
        summary = json.loads(run.out)["papers"]["2401.00001"]["summary"]
        self.assertLessEqual(len(summary), 201)
        self.assertTrue(summary.endswith("…"))

    def test_a_negative_abstract_chars_is_rejected(self):
        run = run_cli(["search", "--title", "x", "--abstract-chars", "-1"], [])
        self.assertEqual(run.code, 2)

    def test_search_json_keeps_the_abstract_and_drops_empty_affiliations(self):
        run = run_cli(["search", "--title", "x"], [feed_of("2401.00001")])
        paper = json.loads(run.out)["papers"][0]
        self.assertEqual(paper["summary"], "An abstract.")
        self.assertEqual(paper["authors"], [{"name": "Someone"}])

    def test_get_json_drops_empty_affiliations(self):
        run = run_cli(["get", "2401.00001"], [feed_of("2401.00001")])
        self.assertEqual(json.loads(run.out)["authors"], [{"name": "Someone"}])


if __name__ == "__main__":
    unittest.main()
