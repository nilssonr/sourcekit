import os
import socket
from unittest import mock
import unittest
import urllib.error
import urllib.request

from arxiv_client import (
    ArxivTimeoutError,
    MalformedResponseError,
    MemoryBlockStore,
    NetworkBlockedError,
    NotFoundError,
    Pacer,
    ServerError,
    ThrottledError,
    ValidationError,
)
from arxiv_client.transport import Transport, build_urllib_opener
from tests.helpers import FakeClock, FakeOpener, FakeResponse, fixture, make_entry, make_feed, xml_response

URL = "https://export.arxiv.org/api/query?search_query=all:x"
OK = make_feed([make_entry("2401.00001")])


def transport(clock, opener, **kw):
    kw.setdefault("block_store", MemoryBlockStore(wall=clock.wall))
    return Transport("test-agent/1.0", opener=opener, clock=clock.monotonic, sleep=clock.sleep, **kw)


def pacer(clock):
    return Pacer(3.0, clock.monotonic, clock.sleep)


class RequestShapeTests(unittest.TestCase):
    def test_get_with_connection_close_and_user_agent(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(OK)])
        transport(clock, opener).get(URL, pacer(clock))
        req = opener.requests[0]
        self.assertEqual(req.method, "GET")
        self.assertTrue(req.url.startswith("https://"))
        self.assertEqual(req.headers["connection"], "close")
        self.assertEqual(req.headers["user-agent"], "test-agent/1.0")
        self.assertLessEqual(req.timeout, 10.0)

    def test_http_url_refused(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [])
        with self.assertRaises(ValidationError):
            transport(clock, opener).get("http://export.arxiv.org/api/query", pacer(clock))
        self.assertEqual(opener.requests, [])

    def test_headers_looked_up_case_insensitively(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(200, OK, {"Content-Type": "application/atom+xml"})])
        resp = transport(clock, opener).get(URL, pacer(clock))
        self.assertEqual(resp.headers["content-type"], "application/atom+xml")

    def test_default_opener_honours_https_proxy_env(self):
        # build_opener() installs ProxyHandler, which reads https_proxy from the environment
        # (it only registers itself when a proxy is configured).
        with mock.patch.dict(os.environ, {"https_proxy": "http://proxy.example:3128"}):
            opener = build_urllib_opener()
        self.assertTrue(any(isinstance(h, urllib.request.ProxyHandler) for h in opener.handlers))


class ThrottleSignalTests(unittest.TestCase):
    def test_rate_exceeded_200_is_throttling_not_parsed(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(200, b"Rate exceeded.", {"content-type": "text/plain"}) for _ in range(3)])
        with self.assertRaises(ThrottledError) as cm:
            transport(clock, opener).get(URL, pacer(clock))
        self.assertEqual(cm.exception.kind, "rate-exceeded-200")
        self.assertGreaterEqual(cm.exception.retry_after, 60)

    def test_rate_exceeded_then_success(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(200, b"Rate exceeded.\n", {}), xml_response(OK)])
        resp = transport(clock, opener).get(URL, pacer(clock))
        self.assertEqual(resp.body, OK)
        self.assertEqual(clock.sleeps, [5.0])

    def test_429_and_503_retried_then_raise_within_30s(self):
        for status in (429, 503):
            clock = FakeClock()
            start = clock.now
            opener = FakeOpener(clock, [FakeResponse(status, b"Rate exceeded.", {}) for _ in range(5)])
            with self.assertRaises(ThrottledError) as cm:
                transport(clock, opener).get(URL, pacer(clock), budget=30)
            self.assertEqual(cm.exception.kind, str(status))
            self.assertEqual(len(opener.requests), 3)
            self.assertLessEqual(clock.now - start, 30.0)

    def test_retry_skipped_when_budget_cannot_cover_backoff(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(429, b"", {}) for _ in range(3)])
        with self.assertRaises(ThrottledError):
            transport(clock, opener).get(URL, pacer(clock), budget=8)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(clock.sleeps, [])

    def test_retry_after_header_used_with_60s_floor(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(429, b"", {"retry-after": "120"})])
        with self.assertRaises(ThrottledError) as cm:
            transport(clock, opener).get(URL, pacer(clock), budget=5)
        self.assertEqual(cm.exception.retry_after, 120)
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(503, b"", {"retry-after": "0"})])
        with self.assertRaises(ThrottledError) as cm:
            transport(clock, opener).get(URL, pacer(clock), budget=5)
        self.assertEqual(cm.exception.retry_after, 60)

    def test_406_not_retried_and_blocks(self):
        clock = FakeClock()
        store = MemoryBlockStore(wall=clock.wall)
        opener = FakeOpener(clock, [FakeResponse(406, b"", {}), xml_response(OK)])
        t = transport(clock, opener, block_store=store)
        with self.assertRaises(ThrottledError) as cm:
            t.get(URL, pacer(clock))
        self.assertEqual(cm.exception.kind, "406")
        self.assertGreaterEqual(cm.exception.retry_after, 600)
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(clock.sleeps, [])
        # Subsequent calls refuse without sending anything until the block expires.
        with self.assertRaises(ThrottledError):
            t.get(URL, pacer(clock))
        self.assertEqual(len(opener.requests), 1)
        clock.advance(601)
        t.get(URL, pacer(clock))
        self.assertEqual(len(opener.requests), 2)

    def test_empty_200_without_content_type_is_transient(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(200, b"", {}), xml_response(OK)])
        self.assertEqual(transport(clock, opener).get(URL, pacer(clock)).body, OK)

    def test_empty_200_repeatedly_is_server_error(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(200, b"", {}) for _ in range(3)])
        with self.assertRaises(ServerError):
            transport(clock, opener).get(URL, pacer(clock))

    def test_other_non_xml_200_is_malformed_not_throttling(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(200, b"Service temporarily unavailable", {"content-type": "text/html"})])
        with self.assertRaises(MalformedResponseError):
            transport(clock, opener).get(URL, pacer(clock))
        self.assertEqual(len(opener.requests), 1)


class ErrorStatusTests(unittest.TestCase):
    def test_400_is_validation_error_with_summary(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(fixture("query_error_400.xml"), status=400)])
        with self.assertRaises(ValidationError) as cm:
            transport(clock, opener).get(URL, pacer(clock))
        self.assertIn("incorrect id format for foo", str(cm.exception))
        self.assertEqual(len(opener.requests), 1)

    def test_400_sort_error(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(fixture("query_error_sort.xml"), status=400)])
        with self.assertRaises(ValidationError) as cm:
            transport(clock, opener).get(URL, pacer(clock))
        self.assertIn("sortBy must be in", str(cm.exception))

    def test_500_not_retried(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(fixture("query_error_500.xml"), status=500)])
        with self.assertRaises(ServerError) as cm:
            transport(clock, opener).get(URL, pacer(clock))
        self.assertEqual(cm.exception.status, 500)
        self.assertIn("internal error", str(cm.exception))
        self.assertEqual(len(opener.requests), 1)

    def test_502_504_not_retried(self):
        for status in (502, 504):
            clock = FakeClock()
            opener = FakeOpener(clock, [FakeResponse(status, b"Bad gateway", {})])
            with self.assertRaises(ServerError):
                transport(clock, opener).get(URL, pacer(clock))
            self.assertEqual(len(opener.requests), 1)

    def test_404(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(404, b"", {})])
        with self.assertRaises(NotFoundError):
            transport(clock, opener).get(URL, pacer(clock))

    def test_200_error_feed_is_detected(self):
        from arxiv_client.atom import parse_query_feed
        with self.assertRaises(ServerError):
            parse_query_feed(fixture("query_error_400.xml"))


class TimeoutAndNetworkTests(unittest.TestCase):
    def test_socket_timeout_not_retried(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [socket.timeout("timed out"), xml_response(OK)])
        with self.assertRaises(ArxivTimeoutError):
            transport(clock, opener).get(URL, pacer(clock))
        self.assertEqual(len(opener.requests), 1)

    def test_urlerror_wrapping_timeout(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [urllib.error.URLError(socket.timeout("timed out"))])
        with self.assertRaises(ArxivTimeoutError):
            transport(clock, opener).get(URL, pacer(clock))

    def test_slow_body_exceeds_budget(self):
        clock = FakeClock()
        # 1024-byte chunks, 4 s each: the 30 s budget runs out mid-body.
        body = OK + b" " * 20000
        opener = FakeOpener(clock, [xml_response(body, clock=clock, read_delay=4.0)])
        start = clock.now
        with self.assertRaises(ArxivTimeoutError):
            transport(clock, opener).get(URL, pacer(clock), budget=30)
        self.assertLessEqual(clock.now - start, 34.0)  # at most one read overshoots

    def test_timeout_during_read(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(200, OK, {"content-type": "application/atom+xml"}, read_error=socket.timeout("x"))])
        with self.assertRaises(ArxivTimeoutError):
            transport(clock, opener).get(URL, pacer(clock))

    def test_per_request_timeout_shrinks_with_budget(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(OK)])
        transport(clock, opener).get(URL, pacer(clock), budget=4)
        self.assertLessEqual(opener.requests[0].timeout, 4.0)

    def test_dns_failure_refused_and_proxy_tunnel_are_network_blocked(self):
        for exc in (
            urllib.error.URLError(socket.gaierror(8, "nodename nor servname provided")),
            urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
            urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden")),
        ):
            clock = FakeClock()
            opener = FakeOpener(clock, [exc])
            with self.assertRaises(NetworkBlockedError) as cm:
                transport(clock, opener).get(URL, pacer(clock))
            self.assertEqual(cm.exception.host, "export.arxiv.org")
            self.assertIn("allow this domain", str(cm.exception))

    def test_connection_reset_is_server_error(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [urllib.error.URLError(ConnectionResetError(54, "reset"))])
        with self.assertRaises(ServerError):
            transport(clock, opener).get(URL, pacer(clock))

    def test_http_403_is_server_error_mentioning_proxy(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [FakeResponse(403, b"Forbidden", {})])
        with self.assertRaises(ServerError) as cm:
            transport(clock, opener).get(URL, pacer(clock))
        self.assertIn("proxy", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
