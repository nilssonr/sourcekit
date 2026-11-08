import os
import tempfile
import unittest

from arxiv_client import ArxivTimeoutError, MalformedResponseError, NotFoundError, ThrottledError, ValidationError
from tests.helpers import FakeClock, FakeOpener, FakeResponse, make_client

PDF = b"%PDF-1.5\n" + b"x" * 5000 + b"\n%%EOF"


def pdf_response(body=PDF, filename="2301.00001v1.pdf", **kw):
    headers = {"content-type": "application/pdf"}
    if filename:
        headers["content-disposition"] = 'inline; filename="%s"' % filename
    return FakeResponse(200, body, headers, **kw)


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.clock = FakeClock()
        self.opener = FakeOpener(self.clock)
        self.client = make_client(self.clock, self.opener)

    def tearDown(self):
        self.tmp.cleanup()

    def files(self):
        return sorted(os.listdir(self.dir))

    def test_versioned_download(self):
        self.opener.add(pdf_response())
        r = self.client.download_pdf("2301.00001v1", self.dir)
        self.assertEqual(r.path, os.path.join(self.dir, "2301.00001v1.pdf"))
        self.assertEqual((r.id, r.version, r.bytes, r.skipped), ("2301.00001", 1, len(PDF), False))
        with open(r.path, "rb") as f:
            self.assertEqual(f.read(), PDF)
        self.assertEqual(self.files(), ["2301.00001v1.pdf"])
        req = self.opener.requests[0]
        self.assertEqual(req.url, "https://arxiv.org/pdf/2301.00001v1")
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.headers["connection"], "close")

    def test_unversioned_takes_version_from_content_disposition(self):
        self.opener.add(pdf_response(filename="2301.00001v3.pdf"))
        r = self.client.download_pdf("2301.00001", self.dir)
        self.assertEqual(r.version, 3)
        self.assertEqual(self.files(), ["2301.00001v3.pdf"])

    def test_old_style_filename(self):
        self.opener.add(pdf_response(filename="hep-th_9901001v1.pdf"))
        r = self.client.download_pdf("hep-th/9901001v1", self.dir)
        self.assertEqual(os.path.basename(r.path), "hep-th_9901001v1.pdf")

    def test_existing_file_skipped_without_request(self):
        with open(os.path.join(self.dir, "2301.00001v1.pdf"), "wb") as f:
            f.write(PDF)
        r = self.client.download_pdf("2301.00001v1", self.dir)
        self.assertTrue(r.skipped)
        self.assertEqual(self.opener.requests, [])

    def test_overwrite(self):
        with open(os.path.join(self.dir, "2301.00001v1.pdf"), "wb") as f:
            f.write(b"old")
        self.opener.add(pdf_response())
        r = self.client.download_pdf("2301.00001v1", self.dir, overwrite=True)
        self.assertEqual(r.bytes, len(PDF))

    def test_html_instead_of_pdf_leaves_no_file(self):
        self.opener.add(FakeResponse(200, b"<html>captcha</html>", {"content-type": "text/html"}))
        with self.assertRaises(MalformedResponseError):
            self.client.download_pdf("2301.00001v1", self.dir)
        self.assertEqual(self.files(), [])

    def test_pdf_content_type_without_magic_leaves_no_file(self):
        self.opener.add(pdf_response(body=b"<html>not a pdf</html>"))
        with self.assertRaises(MalformedResponseError):
            self.client.download_pdf("2301.00001v1", self.dir)
        self.assertEqual(self.files(), [])

    def test_rate_exceeded_is_throttling(self):
        for _ in range(3):
            self.opener.add(FakeResponse(200, b"Rate exceeded.", {"content-type": "text/plain"}))
        with self.assertRaises(ThrottledError):
            self.client.download_pdf("2301.00001v1", self.dir)
        self.assertEqual(self.files(), [])

    def test_404_is_not_found(self):
        self.opener.add(FakeResponse(404, b"", {}))
        with self.assertRaises(NotFoundError):
            self.client.download_pdf("2301.99999v1", self.dir)

    def test_budget_exhausted_mid_download_removes_partial_file(self):
        big = b"%PDF-1.5\n" + b"x" * 100000
        self.opener.add(pdf_response(body=big, clock=self.clock, read_delay=2.0))
        start = self.clock.now
        with self.assertRaises(ArxivTimeoutError):
            self.client.download_pdf("2301.00001v1", self.dir)
        self.assertEqual(self.files(), [])
        self.assertLessEqual(self.clock.now - start, 32.5)  # 30 s budget, at most one read overshoots

    def test_size_cap(self):
        self.opener.add(pdf_response())
        with self.assertRaises(ValidationError):
            self.client.download_pdf("2301.00001v1", self.dir, max_bytes=1000)
        self.assertEqual(self.files(), [])

    def test_missing_dest_dir(self):
        with self.assertRaises(ValidationError):
            self.client.download_pdf("2301.00001v1", os.path.join(self.dir, "nope"))

    def test_download_pdfs_spaced_15s_apart(self):
        for i in range(3):
            self.opener.add(pdf_response(filename="2301.0000%dv1.pdf" % (i + 1)))
        results = self.client.download_pdfs(["2301.00001v1", "2301.00002v1", "2301.00003v1"], self.dir)
        self.assertEqual(len(results), 3)
        starts = [r.at for r in self.opener.requests]
        self.assertTrue(all(b - a >= 15.0 for a, b in zip(starts, starts[1:])), starts)

    def test_download_pdfs_capped_at_10(self):
        with self.assertRaises(ValidationError):
            self.client.download_pdfs(["2301.%05dv1" % i for i in range(1, 12)], self.dir)
        self.assertEqual(self.opener.requests, [])

    def test_download_pdfs_failure_reports_completed(self):
        self.opener.add(pdf_response(), FakeResponse(404, b"", {}))
        with self.assertRaises(NotFoundError) as cm:
            self.client.download_pdfs(["2301.00001v1", "2301.00002v1"], self.dir)
        self.assertEqual([r.id for r in cm.exception.completed], ["2301.00001"])


if __name__ == "__main__":
    unittest.main()
