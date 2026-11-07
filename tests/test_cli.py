import io
import json
import os
import subprocess
import sys
import unittest

from arxiv_client.__main__ import main
from tests.helpers import FakeClock, FakeOpener, FakeResponse, fixture, make_client, make_entry, make_feed, xml_response

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CliTests(unittest.TestCase):
    def run_cli(self, argv, steps):
        clock = FakeClock()
        self.opener = FakeOpener(clock, steps)
        out, err = io.StringIO(), io.StringIO()
        self.user_agents = []

        def factory(ua):
            self.user_agents.append(ua)
            return make_client(clock, self.opener, user_agent=ua)

        code = main(argv, client_factory=factory, stdout=out, stderr=err)
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


if __name__ == "__main__":
    unittest.main()
