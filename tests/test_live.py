"""Opt-in live smoke test. Skipped unless ARXIV_LIVE=1.

Sends exactly 3 requests, spaced at least 3 s apart (15 s before the PDF, per robots.txt):
one search page, one announcement feed, one PDF download.
"""

import os
import tempfile
import time
import unittest

from arxiv_client import ArxivClient, Q

LIVE = os.environ.get("ARXIV_LIVE") == "1"


@unittest.skipUnless(LIVE, "set ARXIV_LIVE=1 to run live arXiv requests")
class LiveSmokeTest(unittest.TestCase):
    def test_three_requests(self):
        client = ArxivClient(os.environ.get("ARXIV_USER_AGENT") or "research-client/0.1")

        page = client.search_page(Q.title("attention is all you need"), max_results=3)
        self.assertGreaterEqual(page.total_results or 0, 1)
        self.assertTrue(any(p.id == "1706.03762" for p in page.papers), [p.id for p in page.papers])

        time.sleep(3.5)
        feed = client.feed("cs.AI")
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


if __name__ == "__main__":
    unittest.main()
