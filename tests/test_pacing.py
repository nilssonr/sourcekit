import io
import os
import tempfile
import unittest

from arxiv_client import ArxivTimeoutError, BlockStore, Pacer, ThrottledError
from tests.helpers import FakeClock, FakeOpener, make_client, make_entry, make_feed, xml_response


class PacerTests(unittest.TestCase):
    def test_first_request_does_not_wait(self):
        clock = FakeClock()
        pacer = Pacer(3.0, clock.monotonic, clock.sleep)
        pacer.before_request(deadline=clock.now + 30)
        self.assertEqual(clock.sleeps, [])

    def test_spacing_measured_from_request_start(self):
        clock = FakeClock()
        pacer = Pacer(3.0, clock.monotonic, clock.sleep)
        pacer.before_request(clock.now + 30)
        clock.advance(1.0)  # the request itself took 1 s
        pacer.before_request(clock.now + 30)
        self.assertEqual(clock.sleeps, [2.0])

    def test_no_wait_if_interval_already_passed(self):
        clock = FakeClock()
        pacer = Pacer(3.0, clock.monotonic, clock.sleep)
        pacer.before_request(clock.now + 30)
        clock.advance(5.0)
        pacer.before_request(clock.now + 30)
        self.assertEqual(clock.sleeps, [])

    def test_wait_past_deadline_raises_timeout(self):
        clock = FakeClock()
        pacer = Pacer(3.0, clock.monotonic, clock.sleep)
        pacer.before_request(clock.now + 30)
        with self.assertRaises(ArxivTimeoutError):
            pacer.before_request(deadline=clock.now + 1.0)
        self.assertEqual(clock.sleeps, [])

    def test_requests_within_one_call_are_spaced_3s(self):
        clock = FakeClock()
        entries = [make_entry("2401.%05d" % i) for i in range(1, 251)]
        opener = FakeOpener(clock, [
            xml_response(make_feed(entries[0:100], total=250)),
            xml_response(make_feed(entries[100:200], total=250, start=100)),
            xml_response(make_feed(entries[200:250], total=250, start=200)),
        ])
        client = make_client(clock, opener)
        self.assertEqual(len(list(client.search("x"))), 250)
        starts = [r.at for r in opener.requests]
        self.assertEqual(len(starts), 3)
        for a, b in zip(starts, starts[1:]):
            self.assertGreaterEqual(b - a, 3.0)

    def test_separate_calls_are_not_paced(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [
            xml_response(make_feed([make_entry("2401.00001")])),
            xml_response(make_feed([make_entry("2401.00001")])),
        ])
        client = make_client(clock, opener)
        client.get("2401.00001")
        client.get("2401.00001")
        self.assertEqual(clock.sleeps, [])


class BlockStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "blocked_until")

    def tearDown(self):
        self.tmp.cleanup()

    def test_406_blocks_other_clients_sharing_the_marker(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(b"", status=406, ct="")])
        a = make_client(clock, opener, block_store=BlockStore(self.path, wall=clock.wall))
        with self.assertRaises(ThrottledError) as cm:
            a.get("2401.00001")
        self.assertEqual(cm.exception.kind, "406")
        self.assertGreaterEqual(cm.exception.retry_after, 600)
        self.assertTrue(os.path.exists(self.path))

        # A second client (think: another process) sees the block and sends nothing.
        opener_b = FakeOpener(clock, [])
        b = make_client(clock, opener_b, block_store=BlockStore(self.path, wall=clock.wall))
        clock.advance(100)
        with self.assertRaises(ThrottledError) as cm:
            b.get("2401.00001")
        self.assertEqual(cm.exception.kind, "406")
        self.assertAlmostEqual(cm.exception.retry_after, 500, delta=1)
        self.assertEqual(opener_b.requests, [])

    def test_block_expires_after_600s(self):
        clock = FakeClock()
        store = BlockStore(self.path, wall=clock.wall)
        store.block()
        clock.advance(599)
        self.assertGreater(store.remaining(), 0)
        clock.advance(2)
        self.assertEqual(store.remaining(), 0)

    def test_far_future_marker_is_clamped(self):
        clock = FakeClock()
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write(str(clock.wall() + 10 ** 6))
        self.assertLessEqual(BlockStore(self.path, wall=clock.wall).remaining(), 600)

    def test_garbage_marker_ignored(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("not a number")
        self.assertEqual(BlockStore(self.path).remaining(), 0)

    def test_unwritable_dir_falls_back_to_memory_with_one_warning(self):
        clock = FakeClock()
        blocker = os.path.join(self.tmp.name, "afile")
        with open(blocker, "w") as f:
            f.write("x")
        warn = io.StringIO()
        store = BlockStore(os.path.join(blocker, "blocked_until"), wall=clock.wall, warn_stream=warn)
        store.block()
        store.block()
        self.assertGreater(store.remaining(), 0)
        self.assertEqual(warn.getvalue().count("could not write"), 1)


if __name__ == "__main__":
    unittest.main()
