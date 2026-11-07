"""Opt-in live smoke tests. Skipped unless ARXIV_LIVE=1.

Sends exactly 5 requests. The request gate spaces them at least 3 s apart, and the PDF
waits an extra 15 s (robots.txt): one search page, one announcement feed, one PDF
download, then a two-query batch through the CLI.
"""

import io
import json
import os
import tempfile
import time
import unittest

from arxiv_client import ArxivClient, Q
from arxiv_client.__main__ import main

LIVE = os.environ.get("ARXIV_LIVE") == "1"


@unittest.skipUnless(LIVE, "set ARXIV_LIVE=1 to run live arXiv requests")
class LiveSmokeTest(unittest.TestCase):
    def test_three_requests(self):
        client = ArxivClient(os.environ.get("ARXIV_USER_AGENT") or "research-client/0.1")

        page = client.search_page(Q.title("attention is all you need"), max_results=3)
        self.assertGreaterEqual(page.total_results or 0, 1)
        self.assertTrue(any(p.id == "1706.03762" for p in page.papers), [p.id for p in page.papers])

        feed = client.feed("cs.AI")  # no sleep: the request gate spaces this call
        # Weekends and holidays legitimately return zero items.
        for item in feed.items:
            self.assertTrue(item.id)

        time.sleep(15.5)
        with tempfile.TemporaryDirectory() as d:
            result = client.download_pdf("2301.00001", d)
            self.assertTrue(os.path.exists(result.path))
            self.assertGreater(result.bytes, 1000)
            with open(result.path, "rb") as f:
                self.assertEqual(f.read(5), b"%PDF-")

    def test_batch_of_two(self):
        queries = [
            {"label": "attention", "title": "attention is all you need", "max": 3},
            {"label": "design2code", "title": "Design2Code", "any_category": ["cs.CL", "cs.CV"], "max": 3},
        ]
        out, err = io.StringIO(), io.StringIO()
        code = main(["batch", "-"], stdout=out, stderr=err, stdin=io.StringIO(json.dumps(queries)))
        self.assertEqual(code, 0, err.getvalue())
        data = json.loads(out.getvalue())
        self.assertIn("1706.03762", data["queries"][0]["ids"])
        self.assertIn("2403.03163", data["queries"][1]["ids"])


if __name__ == "__main__":
    unittest.main()
