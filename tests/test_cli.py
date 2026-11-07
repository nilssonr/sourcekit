import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

from arxiv_client.__main__ import main
from tests.helpers import FakeClock, FakeOpener, FakeResponse, fixture, make_client, make_entry, make_feed, xml_response

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CliTests(unittest.TestCase):
    def run_cli(self, argv, steps, stdin=None):
        clock = FakeClock()
        self.opener = FakeOpener(clock, steps)
        out, err = io.StringIO(), io.StringIO()
        self.user_agents = []

        def factory(ua):
            self.user_agents.append(ua)
            return make_client(clock, self.opener, user_agent=ua)

        code = main(argv, client_factory=factory, stdout=out, stderr=err,
                    stdin=io.StringIO(stdin) if stdin is not None else None)
        return code, out.getvalue(), err.getvalue()

    def test_search_json(self):
        code, out, err = self.run_cli(
            ["search", "--title", "attention is all you need", "--category", "cs.CL", "--max", "2"],
            [xml_response(make_feed([make_entry("1706.03762", 7), make_entry("2401.00001")], total=2))],
        )
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["count"], 2)
        self.assertFalse(data["truncated"])
        self.assertEqual(data["query"], 'ti:"attention is all you need" AND cat:cs.CL')
        self.assertEqual(data["papers"][0]["versioned_id"], "1706.03762v7")

    def test_search_all_and_dates(self):
        code, out, err = self.run_cli(
            ["search", "--all", "protein folding", "--from", "20230101", "--to", "20230131", "--sort", "submitted"],
            [xml_response(make_feed([], total=0))],
        )
        self.assertEqual(code, 0, err)
        p = self.opener.requests[0].params
        self.assertEqual(p["search_query"], ["all:protein AND all:folding AND submittedDate:[20230101 TO 20230131]"])
        self.assertEqual(p["sortBy"], ["submittedDate"])

    def test_search_needs_a_filter(self):
        code, out, err = self.run_cli(["search"], [])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"], "validation")

    def test_fetch(self):
        code, out, err = self.run_cli(["fetch", "2401.00001", "2401.00002"],
                                      [xml_response(make_feed([make_entry("2401.00001")]))])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(list(data["papers"]), ["2401.00001"])
        self.assertEqual(data["not_found"], ["2401.00002"])

    def test_exit_codes(self):
        cases = [
            (["get", "foo"], [], 2, "validation"),
            (["get", "2401.99999"], [xml_response(make_feed([], total=0))], 3, "not_found"),
            (["get", "2401.00001"], [FakeResponse(406, b"", {})], 4, "throttled"),
            (["get", "2401.00001"], [xml_response(fixture("query_error_500.xml"), status=500)], 5, "server_error"),
        ]
        for argv, steps, code_expected, kind in cases:
            code, out, err = self.run_cli(argv, steps)
            self.assertEqual(code, code_expected, (argv, err))
            payload = json.loads(err)
            self.assertEqual(payload["error"], kind)
            self.assertEqual(out, "")
        code, out, err = self.run_cli(["get", "2401.00001"], [FakeResponse(406, b"", {})])
        self.assertEqual(json.loads(err)["retry_after"], 600)

    def test_timeout_and_network_exit_codes(self):
        import socket
        import urllib.error
        code, _, err = self.run_cli(["get", "2401.00001"], [socket.timeout("timed out")])
        self.assertEqual((code, json.loads(err)["error"]), (6, "timeout"))
        code, _, err = self.run_cli(["get", "2401.00001"], [urllib.error.URLError(socket.gaierror(8, "no dns"))])
        self.assertEqual((code, json.loads(err)["error"]), (7, "network_blocked"))

    def test_feed(self):
        code, out, err = self.run_cli(["feed", "cs.AI", "--rss"],
                                      [xml_response(fixture("rss_weekday.xml"), ct="application/rss+xml")])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["count"], 2)

    def test_user_agent_precedence(self):
        steps = lambda: [xml_response(make_feed([make_entry("2401.00001")]))]
        old = os.environ.pop("ARXIV_USER_AGENT", None)
        try:
            self.run_cli(["get", "2401.00001"], steps())
            self.assertEqual(self.user_agents, ["research-client/0.1"])
            os.environ["ARXIV_USER_AGENT"] = "env-agent/2"
            self.run_cli(["get", "2401.00001"], steps())
            self.assertEqual(self.user_agents, ["env-agent/2"])
            self.run_cli(["--user-agent", "flag-agent/3", "get", "2401.00001"], steps())
            self.assertEqual(self.user_agents, ["flag-agent/3"])
            self.assertEqual(self.opener.requests[0].headers["user-agent"], "flag-agent/3")
        finally:
            os.environ.pop("ARXIV_USER_AGENT", None)
            if old is not None:
                os.environ["ARXIV_USER_AGENT"] = old

    def test_python_dash_m_entry_point(self):
        # --help needs no network.
        proc = subprocess.run(
            [sys.executable, "-m", "arxiv_client", "--help"],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("search", proc.stdout)


def feed_of(*ids, total=None):
    return xml_response(make_feed([make_entry(i, title="Paper %s" % i) for i in ids], total=total))


class BatchAndOutputTests(CliTests):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def batch_file(self, items):
        path = os.path.join(self.tmp.name, "q.json")
        with open(path, "w") as f:
            json.dump(items, f)
        return path

    def test_batch_dedups_and_records_found_by(self):
        path = self.batch_file([
            {"label": "first", "title": "diffusion", "any_category": ["cs.CV", "cs.LG"], "max": 5},
            {"abstract": "mode collapse", "sort": "submitted"},
        ])
        code, out, err = self.run_cli(["batch", path], [feed_of("2401.00001", "2401.00002"),
                                                         feed_of("2401.00002", "2401.00003")])
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertEqual(data["unique"], 3)
        self.assertEqual([q["label"] for q in data["queries"]], ["first", "q2"])
        self.assertEqual(data["queries"][0]["query"], "ti:diffusion AND (cat:cs.CV OR cat:cs.LG)")
        self.assertEqual(data["queries"][1]["ids"], ["2401.00002", "2401.00003"])
        self.assertEqual(data["papers"]["2401.00002"]["found_by"], ["first", "q2"])
        self.assertEqual(data["papers"]["2401.00001"]["found_by"], ["first"])
        self.assertEqual(self.opener.requests[1].params["sortBy"], ["submittedDate"])
        starts = [r.at for r in self.opener.requests]
        self.assertGreaterEqual(starts[1] - starts[0], 3.0)

    def test_batch_validates_every_query_before_sending(self):
        for bad in ({"title": "x", "sort": "newest"}, {"title": "x", "colour": "red"}, {"max": 3},
                    {"title": "x", "max": 0}, {"title": 5}, "not an object",
                    {"title": "x", "max": None}, {"title": "x", "max": "10"}, {"title": "x", "max": True},
                    {"title": "x", "label": 0}, {"title": "x", "label": ""}, {"title": "x", "label": False},
                    {"title": "x", "label": "q1"}):  # q1 is the first item's default label
            path = self.batch_file([{"title": "fine"}, bad])
            code, out, err = self.run_cli(["batch", path], [])
            self.assertEqual(code, 2, (bad, err))
            self.assertEqual(json.loads(err)["error"], "validation")
            self.assertEqual(self.opener.requests, [])

    def test_batch_rejects_duplicate_labels(self):
        path = self.batch_file([{"label": "x", "title": "a"}, {"label": "x", "title": "b"}])
        code, _, err = self.run_cli(["batch", path], [])
        self.assertEqual(code, 2)
        self.assertIn("duplicate label", json.loads(err)["message"])
        self.assertEqual(self.opener.requests, [])

    def test_batch_limits_and_bad_files(self):
        code, _, err = self.run_cli(["batch", self.batch_file([{"title": "x"}] * 21)], [])
        self.assertEqual(code, 2)
        self.assertIn("at most 20", json.loads(err)["message"])
        for content in ("[]", "{}", "not json"):
            code, _, err = self.run_cli(["batch", "-"], [], stdin=content)
            self.assertEqual(code, 2, content)
        code, _, err = self.run_cli(["batch", os.path.join(self.tmp.name, "missing.json")], [])
        self.assertEqual(code, 2)

    def test_batch_from_stdin(self):
        code, out, err = self.run_cli(["batch", "-"], [feed_of("2401.00001")],
                                      stdin=json.dumps([{"all": "protein folding"}]))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["queries"][0]["query"], "all:protein AND all:folding")

    def test_batch_throttled_midway_reports_completed_queries(self):
        path = self.batch_file([{"label": "a", "title": "x"}, {"label": "b", "title": "y"}])
        code, out, err = self.run_cli(["batch", path], [feed_of("2401.00001"), FakeResponse(406, b"", {})])
        self.assertEqual(code, 4)
        self.assertEqual(out, "")
        payload = json.loads(err)
        self.assertEqual(payload["error"], "throttled")
        self.assertEqual([q["label"] for q in payload["completed"]], ["a"])
        self.assertEqual(payload["completed"][0]["ids"], ["2401.00001"])
        self.assertEqual(payload["papers"]["2401.00001"]["found_by"], ["a"])
        self.assertIn("title", payload["papers"]["2401.00001"])

    def test_batch_compact(self):
        path = self.batch_file([{"label": "a", "title": "x", "max": 1}, {"label": "b", "title": "y"}])
        code, out, err = self.run_cli(["batch", path, "--format", "compact"],
                                      [feed_of("2401.00001", total=40), feed_of("2401.00001", "2401.00002")])
        self.assertEqual(code, 0, err)
        lines = out.splitlines()
        self.assertEqual(lines[0], "## a  [ti:x]  total=40")
        self.assertEqual(lines[1], "* 2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001")
        self.assertIn("  2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001", lines)
        self.assertIn("* 2401.00002 | 2024-01-01 | cs.LG | Paper 2401.00002", lines)
        self.assertEqual(lines[-1], "# 2 unique papers from 2 queries (* = first seen)")

    def test_search_compact_and_any_category(self):
        code, out, err = self.run_cli(
            ["search", "--abstract", "homogenization", "--any-category", "cs.HC", "--any-category", "cs.CL",
             "--format", "compact", "--max", "1"],
            [feed_of("2401.00001", total=7)],
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines(), [
            "## [abs:homogenization AND (cat:cs.HC OR cat:cs.CL)]  total=7  shown=1",
            "2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001",
        ])

    def test_fetch_compact_lists_not_found(self):
        code, out, err = self.run_cli(["fetch", "2401.00001", "2401.00002", "--format", "compact"],
                                      [feed_of("2401.00001")])
        self.assertEqual(code, 0, err)
        self.assertEqual(out.splitlines(), ["2401.00001 | 2024-01-01 | cs.LG | Paper 2401.00001",
                                            "# not found: 2401.00002"])

    def test_abstract_chars(self):
        long = "word " * 100
        step = lambda: [xml_response(make_feed([make_entry("2401.00001", summary=long)]))]
        code, out, _ = self.run_cli(["search", "--title", "x", "--abstract-chars", "0"], step())
        paper = json.loads(out)["papers"][0]
        self.assertNotIn("summary", paper)
        self.assertEqual(paper["authors"], [{"name": "Someone"}])
        code, out, _ = self.run_cli(["fetch", "2401.00001", "--abstract-chars", "200"], step())
        summary = json.loads(out)["papers"]["2401.00001"]["summary"]
        self.assertLessEqual(len(summary), 201)
        self.assertTrue(summary.endswith("…"))
        code, _, err = self.run_cli(["search", "--title", "x", "--abstract-chars", "-1"], [])
        self.assertEqual(code, 2)

    def test_default_json_keeps_abstract_and_drops_empty_affiliations(self):
        code, out, _ = self.run_cli(["search", "--title", "x"], [feed_of("2401.00001")])
        paper = json.loads(out)["papers"][0]
        self.assertEqual(paper["summary"], "An abstract.")
        self.assertEqual(paper["authors"], [{"name": "Someone"}])
        code, out, _ = self.run_cli(["get", "2401.00001"], [feed_of("2401.00001")])
        self.assertEqual(json.loads(out)["authors"], [{"name": "Someone"}])


if __name__ == "__main__":
    unittest.main()
