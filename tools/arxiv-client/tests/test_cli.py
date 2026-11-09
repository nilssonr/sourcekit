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
    opener: FakeOpener  # the requests the command sent
    user_agents: List[str]  # the User-Agent each client was built with


def run_cli(argv: List[str], steps: List[Step], stdin: Optional[str] = None, **client_kw) -> CliRun:
    """Run the command against canned responses. Nothing touches the network."""
    clock = FakeClock()
    opener = FakeOpener(clock, steps)
    out, err = io.StringIO(), io.StringIO()
    user_agents: List[str] = []

    def factory(ua: str):
        user_agents.append(ua)
        return make_client(clock, opener, user_agent=ua, **client_kw)

    code = main(
        argv, client_factory=factory, stdout=out, stderr=err, stdin=io.StringIO(stdin) if stdin is not None else None
    )
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

    def test_free_text_stopwords_are_left_out_of_the_request(self):
        run = run_cli(["search", "--all", "attention is all you need"], [xml_response(make_feed([], total=0))])
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(
            run.opener.requests[0].params["search_query"], ["all:attention AND all:all AND all:you AND all:need"]
        )

    def test_an_operator_word_in_free_text_is_a_validation_error_and_nothing_is_sent(self):
        run = run_cli(["search", "--all", "attention OR transformer"], [])
        self.assertEqual(run.code, 2)
        self.assertEqual(error_payload(run)["error"], "validation")
        self.assertIn("OR is not an operator", error_payload(run)["message"])
        self.assertIn("--raw", error_payload(run)["message"])
        self.assertEqual(run.opener.requests, [])

    def test_a_date_that_is_not_on_the_calendar_is_a_validation_error_and_nothing_is_sent(self):
        run = run_cli(["search", "--title", "transformer", "--from", "20231399", "--to", "20231399"], [])
        self.assertEqual(run.code, 2)
        self.assertEqual(error_payload(run)["error"], "validation")
        self.assertIn("20231399 is not a calendar date", error_payload(run)["message"])
        self.assertEqual(run.opener.requests, [])

    def test_search_by_id_prefix_sends_an_id_term(self):
        run = run_cli(["search", "--id", "1706.037*", "--category", "cs.CL"], [feed_of("1706.03762")])
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.opener.requests[0].params["search_query"], ["cat:cs.CL AND id:1706.037*"])

    def test_search_by_a_versioned_or_old_style_id_is_a_validation_error_naming_fetch(self):
        for bad in ("1706.03762v5", "hep-th/9901001"):
            with self.subTest(bad=bad):
                run = run_cli(["search", "--id", bad], [])
                self.assertEqual(run.code, 2)
                self.assertIn("arxiv fetch %s" % bad, error_payload(run)["message"])
                self.assertEqual(run.opener.requests, [])

    def test_ascending_order_without_a_date_sort_is_a_validation_error_and_nothing_is_sent(self):
        for extra in ([], ["--dry-run"], ["--sort", "relevance"]):
            with self.subTest(extra=extra):
                run = run_cli(["search", "--title", "x", "--order", "ascending", *extra], [])
                self.assertEqual(run.code, 2)
                self.assertEqual(error_payload(run)["error"], "validation")
                self.assertIn("--sort submitted", error_payload(run)["message"])
                self.assertEqual(run.opener.requests, [])

    def test_ascending_order_with_a_date_sort_is_sent(self):
        run = run_cli(
            ["search", "--title", "x", "--sort", "submitted", "--order", "ascending"], [feed_of("2401.00001")]
        )
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.opener.requests[0].params["sortOrder"], ["ascending"])

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
            run = run_cli(
                ["pdf", "2301.00001v1", "2301.00002v1", "--out", out],
                [FakeResponse(200, PDF, PDF_HEADERS), FakeResponse(200, PDF, PDF_HEADERS)],
                create_part_file=part_file_failing_after(1),
            )
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

    def test_the_user_agent_flag_is_accepted_after_the_subcommand(self):
        argvs = (
            ["get", "2401.00001", "--user-agent", "flag-agent/3"],
            ["search", "--title", "x", "--user-agent", "flag-agent/3"],
            ["fetch", "--user-agent", "flag-agent/3", "2401.00001"],
            ["feed", "cs.AI", "--user-agent", "flag-agent/3"],
        )
        for argv in argvs:
            with self.subTest(command=argv[0]), mock.patch.dict(os.environ, {"ARXIV_USER_AGENT": "env-agent/2"}):
                step = xml_response(fixture("atom_weekday.xml")) if argv[0] == "feed" else feed_of("2401.00001")
                run = run_cli(argv, [step])
                self.assertEqual(run.code, 0, run.err)
                self.assertEqual(run.user_agents, ["flag-agent/3"])

    def test_a_user_agent_after_the_subcommand_beats_one_before_it(self):
        run = run_cli(
            ["--user-agent", "before/1", "get", "2401.00001", "--user-agent", "after/2"], [feed_of("2401.00001")]
        )
        self.assertEqual(run.user_agents, ["after/2"])

    def test_python_dash_m_entry_point_prints_help(self):
        # --help needs no network.
        proc = subprocess.run(
            [sys.executable, "-m", "arxiv_client", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("search", proc.stdout)


class ExplainAndDryRunTests(unittest.TestCase):
    def test_explain_adds_the_request_urls_and_the_servers_parsed_query(self):
        feed = xml_response(make_feed([make_entry("2401.00001")], server_query="all:bar AND all:foo"))
        run = run_cli(["search", "--all", "foo bar", "--explain"], [feed])
        self.assertEqual(run.code, 0, run.err)
        data = json.loads(run.out)
        self.assertEqual(data["query"], "all:foo AND all:bar")
        self.assertEqual(data["server_query"], "all:bar AND all:foo")
        self.assertEqual(len(data["request_urls"]), 1)
        self.assertIn("search_query=all:foo%20AND%20all:bar", data["request_urls"][0])

    def test_explain_lists_the_stopwords_dropped_from_free_text(self):
        feed = xml_response(make_feed([make_entry("2401.00001")], server_query="all:attention AND all:need"))
        run = run_cli(["search", "--all", "attention is what you need", "--all", "the end", "--explain"], [feed])
        self.assertEqual(run.code, 0, run.err)
        data = json.loads(run.out)
        self.assertEqual(data["query"], "all:attention AND all:what AND all:you AND all:need AND all:end")
        self.assertEqual(data["dropped_words"], ["is", "the"])

    def test_explain_reports_no_dropped_words_as_an_empty_list(self):
        feed = xml_response(make_feed([make_entry("2401.00001")], server_query="all:foo"))
        run = run_cli(["search", "--all", "foo", "--explain"], [feed])
        self.assertEqual(json.loads(run.out)["dropped_words"], [])

    def test_compact_explain_adds_a_dropped_line_when_words_were_dropped(self):
        feed = xml_response(make_feed([make_entry("2401.00001")], server_query="all:transformer"))
        run = run_cli(["search", "--all", "the transformer", "--explain", "--format", "compact"], [feed])
        lines = run.out.splitlines()
        self.assertEqual(lines[1], "   server [all:transformer]")
        self.assertEqual(lines[2], "   dropped [the]")
        self.assertTrue(lines[3].startswith("2401.00001 |"))

    def test_dry_run_lists_the_dropped_words(self):
        run = run_cli(["search", "--all", "transformers for protein folding", "--dry-run"], [])
        data = json.loads(run.out)
        self.assertEqual(data["query"], "all:transformers AND all:protein AND all:folding")
        self.assertEqual(data["dropped_words"], ["for"])
        self.assertEqual(run.opener.requests, [])

    def test_batch_reports_dropped_words_per_query(self):
        stdin = json.dumps([{"label": "a", "all": "the transformer"}, {"label": "b", "title": "x"}])
        run = run_cli(["batch", "-", "--dry-run"], [], stdin=stdin)
        data = json.loads(run.out)
        self.assertEqual([q["dropped_words"] for q in data["queries"]], [["the"], []])

    def test_a_batch_item_with_ascending_order_and_no_date_sort_is_named_and_nothing_is_sent(self):
        stdin = json.dumps([{"label": "fine", "title": "x"}, {"label": "bad", "title": "y", "order": "ascending"}])
        run = run_cli(["batch", "-"], [], stdin=stdin)
        self.assertEqual(run.code, 2)
        self.assertIn("batch item 2 (bad): ascending order needs a date sort", error_payload(run)["message"])
        self.assertEqual(run.opener.requests, [])

    def test_a_batch_item_can_search_by_id(self):
        run = run_cli(["batch", "-", "--dry-run"], [], stdin=json.dumps([{"id": "1706.037*"}]))
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(json.loads(run.out)["queries"][0]["query"], "id:1706.037*")

    def test_fetch_explain_lists_the_failed_request_and_the_unversioned_retry(self):
        steps = [
            xml_response(fixture("query_error_500.xml"), status=500),
            xml_response(make_feed([make_entry("hep-th/9901001", 3, category="hep-th")])),
        ]
        run = run_cli(["fetch", "hep-th/9901001v1", "--explain"], steps)
        self.assertEqual(run.code, 0, run.err)
        urls = json.loads(run.out)["request_urls"]
        self.assertEqual(urls, [r.url for r in run.opener.requests])
        self.assertIn("id_list=hep-th%2F9901001v1&", urls[0])
        self.assertIn("id_list=hep-th%2F9901001&", urls[1])

    def test_compact_fetch_explain_ends_with_a_line_per_request(self):
        run = run_cli(["fetch", "2401.00001", "--explain", "--format", "compact"], [feed_of("2401.00001")])
        self.assertEqual(
            run.out.splitlines(),
            ["2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001", "# request: %s" % run.opener.requests[0].url],
        )

    def test_fetch_without_explain_has_no_request_urls(self):
        run = run_cli(["fetch", "2401.00001"], [feed_of("2401.00001")])
        self.assertEqual(sorted(json.loads(run.out)), ["not_found", "papers"])

    def test_a_batch_item_with_an_operator_word_is_named_and_nothing_is_sent(self):
        stdin = json.dumps([{"label": "fine", "all": "transformer"}, {"label": "bad", "all": "attention OR vision"}])
        run = run_cli(["batch", "-"], [], stdin=stdin)
        self.assertEqual(run.code, 2)
        self.assertIn("batch item 2 (bad): OR is not an operator", error_payload(run)["message"])
        self.assertEqual(run.opener.requests, [])

    def test_without_explain_the_output_is_unchanged(self):
        run = run_cli(["search", "--all", "foo"], [feed_of("2401.00001")])
        data = json.loads(run.out)
        self.assertNotIn("server_query", data)
        self.assertNotIn("request_urls", data)

    def test_compact_explain_adds_a_server_line_under_the_header(self):
        feed = xml_response(make_feed([make_entry("2401.00001")], server_query="all:foo"))
        run = run_cli(["search", "--all", "foo", "--explain", "--format", "compact"], [feed])
        lines = run.out.splitlines()
        self.assertTrue(lines[0].startswith("## [all:foo]"))
        self.assertEqual(lines[1], "   server [all:foo]")
        self.assertTrue(lines[2].startswith("2401.00001 |"))

    def test_dry_run_prints_the_query_and_url_and_sends_nothing(self):
        run = run_cli(["search", "--title", "a b", "--category", "cs.LG", "--max", "3", "--dry-run"], [])
        self.assertEqual(run.code, 0, run.err)
        data = json.loads(run.out)
        self.assertEqual(data["dry_run"], True)
        self.assertEqual(data["query"], 'ti:"a b" AND cat:cs.LG')
        self.assertIn("max_results=3", data["request_url"])
        self.assertEqual(run.opener.requests, [])

    def test_dry_run_still_validates(self):
        run = run_cli(["search", "--title", "tr*ansform", "--dry-run"], [])
        self.assertEqual(run.code, 2)
        self.assertEqual(error_payload(run)["error"], "validation")

    def test_batch_dry_run_lists_every_query_and_sends_nothing(self):
        stdin = json.dumps([{"label": "a", "title": "x"}, {"abstract": ["y", "z"], "max": 3}])
        run = run_cli(["batch", "-", "--dry-run"], [], stdin=stdin)
        self.assertEqual(run.code, 0, run.err)
        data = json.loads(run.out)
        self.assertEqual([q["label"] for q in data["queries"]], ["a", "q2"])
        self.assertEqual(data["queries"][1]["query"], "abs:y AND abs:z")
        self.assertIn("max_results=3", data["queries"][1]["request_url"])
        self.assertEqual(run.opener.requests, [])

    def test_batch_explain_adds_server_query_per_query(self):
        stdin = json.dumps([{"title": "x"}])
        feed = xml_response(make_feed([make_entry("2401.00001")], server_query="ti:x"))
        run = run_cli(["batch", "-", "--explain"], [feed], stdin=stdin)
        data = json.loads(run.out)
        self.assertEqual(data["queries"][0]["server_query"], "ti:x")
        self.assertEqual(len(data["queries"][0]["request_urls"]), 1)


class BatchTests(unittest.TestCase):
    def batch_file(self, items) -> str:
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        path = os.path.join(tmp, "q.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f)
        return path

    def test_batch_deduplicates_papers_and_records_which_queries_found_them(self):
        path = self.batch_file(
            [
                {"label": "first", "title": "diffusion", "any_category": ["cs.CV", "cs.LG"], "max": 5},
                {"abstract": "mode collapse", "sort": "submitted"},
            ]
        )
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
            {"title": "x", "sort": "newest"},
            {"title": "x", "colour": "red"},
            {"max": 3},
            {"title": "x", "max": 0},
            {"title": 5},
            "not an object",
            {"title": "x", "max": None},
            {"title": "x", "max": "10"},
            {"title": "x", "max": True},
            {"title": "x", "label": 0},
            {"title": "x", "label": ""},
            {"title": "x", "label": False},
            {"title": "x", "label": "q1"},  # q1 is the first item's default label
            {"title": "x", "sort": ["submitted"]},
            {"title": "x", "sort": {"a": 1}},
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
        run = run_cli(
            ["batch", path, "--format", "compact"],
            [feed_of("2401.00001", total=40), feed_of("2401.00001", "2401.00002")],
        )
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
            [
                "search",
                "--abstract",
                "homogenization",
                "--any-category",
                "cs.HC",
                "--any-category",
                "cs.CL",
                "--format",
                "compact",
                "--max",
                "1",
            ],
            [feed_of("2401.00001", total=7)],
        )
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(
            run.out.splitlines(),
            [
                "## [abs:homogenization AND (cat:cs.HC OR cat:cs.CL)]  total=7  shown=1",
                "2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001",
            ],
        )

    def test_compact_fetch_output_lists_the_ids_not_found(self):
        run = run_cli(["fetch", "2401.00001", "2401.00002", "--format", "compact"], [feed_of("2401.00001")])
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(
            run.out.splitlines(), ["2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001", "# not found: 2401.00002"]
        )

    def test_abstract_chars_zero_drops_the_abstract(self):
        run = run_cli(
            ["search", "--title", "x", "--abstract-chars", "0"],
            [xml_response(make_feed([make_entry("2401.00001", summary="word " * 100)]))],
        )
        paper = json.loads(run.out)["papers"][0]
        self.assertNotIn("summary", paper)
        self.assertEqual(paper["authors"], [{"name": "Someone"}])

    def test_abstract_chars_cuts_the_abstract_and_marks_the_cut(self):
        run = run_cli(
            ["fetch", "2401.00001", "--abstract-chars", "200"],
            [xml_response(make_feed([make_entry("2401.00001", summary="word " * 100)]))],
        )
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

    def test_compact_get_output_is_one_line(self):
        run = run_cli(["get", "2401.00001", "--format", "compact"], [feed_of("2401.00001")])
        self.assertEqual(run.code, 0, run.err)
        self.assertEqual(run.out.splitlines(), ["2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001"])

    def test_get_abstract_chars_cuts_or_drops_the_abstract(self):
        entry = [make_entry("2401.00001", summary="word " * 100)]
        cut = run_cli(["get", "2401.00001", "--abstract-chars", "200"], [xml_response(make_feed(entry))])
        self.assertTrue(json.loads(cut.out)["summary"].endswith("…"))
        self.assertLessEqual(len(json.loads(cut.out)["summary"]), 201)
        dropped = run_cli(["get", "2401.00001", "--abstract-chars", "0"], [xml_response(make_feed(entry))])
        self.assertNotIn("summary", json.loads(dropped.out))

    def test_get_json_drops_empty_affiliations(self):
        run = run_cli(["get", "2401.00001"], [feed_of("2401.00001")])
        self.assertEqual(json.loads(run.out)["authors"], [{"name": "Someone"}])


if __name__ == "__main__":
    unittest.main()
