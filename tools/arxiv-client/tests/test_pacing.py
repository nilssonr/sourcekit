import errno
import io
import json
import multiprocessing
import os
import tempfile
import time
import unittest
from unittest import mock

from arxiv_client import ArxivTimeoutError, BlockStore, FileGate, MemoryGate, Pacer, ThrottledError
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

    def test_separate_calls_are_paced_by_the_gate(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [
            xml_response(make_feed([make_entry("2401.00001")])),
            xml_response(make_feed([make_entry("2401.00001")])),
        ])
        client = make_client(clock, opener)
        client.get("2401.00001")
        client.get("2401.00001")
        starts = [r.at for r in opener.requests]
        self.assertGreaterEqual(starts[1] - starts[0], 3.0)


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


API = {"any": 3.0}
PDF = {"any": 3.0, "pdf": 15.0}


def _gate_worker(path, interval, rounds, out_path):
    """Runs in a separate process: takes the gate ``rounds`` times and logs (start, end)."""
    gate = FileGate(path, poll=0.01)
    log = []
    for _ in range(rounds):
        with gate.hold({"any": interval}, time.monotonic() + 30):
            start = time.time()
            time.sleep(0.05)  # the "request"
            log.append((start, time.time()))
    with open(out_path, "w") as f:
        json.dump(log, f)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "sub", "request.lock")
        self.clock = FakeClock()

    def tearDown(self):
        self.tmp.cleanup()

    def gate(self, **kw):
        c = self.clock
        return FileGate(self.path, clock=c.monotonic, sleep=c.sleep, wall=c.wall, **kw)

    def test_first_request_does_not_wait(self):
        with self.gate().hold(API, self.clock.now + 30):
            pass
        self.assertEqual(self.clock.sleeps, [])

    def test_two_gates_sharing_the_file_are_spaced(self):
        # Two FileGate objects on one path stand in for two processes.
        a, b = self.gate(), self.gate()
        with a.hold(API, self.clock.now + 30):
            self.clock.advance(1.0)
        with b.hold(API, self.clock.now + 30):
            pass
        self.assertEqual(len(self.clock.sleeps), 1)
        self.assertAlmostEqual(self.clock.sleeps[0], 2.0, delta=0.01)

    def test_pdf_waits_15s_after_a_pdf_but_api_only_3s(self):
        gate = self.gate()
        with gate.hold(PDF, self.clock.now + 30):
            pass
        with gate.hold(API, self.clock.now + 30):
            pass
        self.assertAlmostEqual(self.clock.sleeps[-1], 3.0, delta=0.01)
        with gate.hold(PDF, self.clock.now + 30):
            pass
        self.assertAlmostEqual(self.clock.sleeps[-1], 12.0, delta=0.01)  # 15 s since the PDF start

    def test_lock_is_free_while_a_pdf_waits_for_its_lane(self):
        # A waits 11 s for the 15 s PDF lane; B's API request only needs the 3 s lane and
        # must get through during that wait instead of polling for the lock until it times out.
        c = self.clock
        b = self.gate()
        b_ran = []

        def sleep_and_let_b_in(seconds):
            c.sleep(seconds)
            if not b_ran:
                b_ran.append(True)
                with b.hold(API, c.now + 1.0):
                    b_ran.append(c.now)

        a = FileGate(self.path, clock=c.monotonic, sleep=sleep_and_let_b_in, wall=c.wall)
        with a.hold(PDF, c.now + 30):
            pass
        c.advance(4.0)
        with a.hold(PDF, c.now + 30):
            a_start = c.now
        self.assertEqual(len(b_ran), 2, "B never got the gate")
        self.assertLessEqual(b_ran[1], a_start)
        self.assertGreaterEqual(a_start - b_ran[1], 3.0)  # A still spaced 3 s after B

    def test_wait_past_deadline_raises_timeout(self):
        gate = self.gate()
        with gate.hold(API, self.clock.now + 30):
            pass
        with self.assertRaises(ArxivTimeoutError):
            with gate.hold(API, self.clock.now + 1.0):
                self.fail("request must not start")

    def test_busy_gate_times_out_instead_of_hanging(self):
        a, b = self.gate(), self.gate(poll=0.5)
        with a.hold(API, self.clock.now + 30):
            with self.assertRaises(ArxivTimeoutError) as cm:
                with b.hold(API, self.clock.now + 5.0):
                    self.fail("request must not start")
        self.assertIn("another arxiv_client process", str(cm.exception))
        self.assertTrue(all(s == 0.5 for s in self.clock.sleeps))
        # The lock was released: b can go once a is done.
        with b.hold(API, self.clock.now + 30):
            pass

    def test_lock_released_when_the_request_raises(self):
        a, b = self.gate(), self.gate()
        with self.assertRaises(ValueError):
            with a.hold(API, self.clock.now + 30):
                raise ValueError("request failed")
        self.clock.advance(5)
        with b.hold(API, self.clock.now + 1.0):
            pass

    def test_future_timestamp_is_clamped_to_one_interval(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"any": self.clock.wall() + 10 ** 6}, f)
        with self.gate().hold(API, self.clock.now + 30):
            pass
        self.assertLessEqual(self.clock.sleeps[0], 3.01)

    def test_corrupt_file_is_ignored(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("not json")
        with self.gate().hold(API, self.clock.now + 30):
            pass
        self.assertEqual(self.clock.sleeps, [])
        with open(self.path) as f:
            self.assertIn("any", json.load(f))

    def test_unusable_path_warns_once_and_still_spaces_this_process(self):
        blocker = os.path.join(self.tmp.name, "afile")
        with open(blocker, "w") as f:
            f.write("x")
        warn = io.StringIO()
        c = self.clock
        gate = FileGate(os.path.join(blocker, "request.lock"), clock=c.monotonic, sleep=c.sleep,
                        wall=c.wall, warn_stream=warn)
        for _ in range(3):
            with gate.hold(API, c.now + 30):
                pass
        self.assertEqual(warn.getvalue().count("could not use request gate"), 1)
        self.assertEqual(len(c.sleeps), 2)

    def test_lock_failure_warns_once_and_still_spaces_this_process(self):
        warn = io.StringIO()
        gate = self.gate(warn_stream=warn)
        c = self.clock
        with mock.patch("arxiv_client.pacing._fcntl.flock", side_effect=OSError(errno.ENOLCK, "No locks available")):
            for _ in range(3):
                with gate.hold(API, c.now + 30):
                    pass
        self.assertEqual(warn.getvalue().count("could not use request gate"), 1)
        self.assertEqual(len(c.sleeps), 2)

    def test_memory_gate_spaces_without_touching_disk(self):
        c = self.clock
        gate = MemoryGate(clock=c.monotonic, sleep=c.sleep, wall=c.wall)
        for _ in range(2):
            with gate.hold(API, c.now + 30):
                pass
        self.assertEqual(len(c.sleeps), 1)

    def test_real_processes_never_overlap_and_are_spaced(self):
        interval, rounds = 0.3, 3
        ctx = multiprocessing.get_context("spawn")
        outs = [os.path.join(self.tmp.name, "out%d.json" % i) for i in range(2)]
        procs = [ctx.Process(target=_gate_worker, args=(self.path, interval, rounds, o)) for o in outs]
        for p in procs:
            p.start()
        for p in procs:
            p.join(30)
            self.assertEqual(p.exitcode, 0)
        spans = []
        for o in outs:
            with open(o) as f:
                spans += json.load(f)
        spans.sort()
        self.assertEqual(len(spans), 2 * rounds)
        for (s1, e1), (s2, _) in zip(spans, spans[1:]):
            self.assertGreaterEqual(s2, e1, "requests overlapped")
            self.assertGreaterEqual(s2 - s1, interval - 0.005)


if __name__ == "__main__":
    unittest.main()
