import itertools
import unittest

from arxiv_client import NotFoundError, Q, ServerError, ValidationError

from tests.helpers import FakeClock, FakeOpener, fixture, make_client, make_entry, make_feed, xml_response

BAD_ENTRY = "<entry><title>no id</title></entry>"


def ids(prefix, n, first=1):
    return ["%s.%05d" % (prefix, i) for i in range(first, first + n)]


class SearchPageTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.opener = FakeOpener(self.clock)
        self.client = make_client(self.clock, self.opener)

    def test_search_parameters_are_sent_as_arxiv_query_fields(self):
        self.opener.add(xml_response(make_feed([])))
        self.client.search_page(Q.title("x"), start=5, max_results=20, sort_by="submittedDate", sort_order="ascending")
        p = self.opener.requests[0].params
        self.assertEqual(p["search_query"], ["ti:x"])
        self.assertEqual(p["start"], ["5"])
        self.assertEqual(p["max_results"], ["20"])
        self.assertEqual(p["sortBy"], ["submittedDate"])
        self.assertEqual(p["sortOrder"], ["ascending"])
        self.assertTrue(self.opener.requests[0].url.startswith("https://export.arxiv.org/api/query?"))

    def test_inputs_arxiv_would_answer_with_a_500_are_refused_locally(self):
        for kwargs in (
            {"max_results": 0},
            {"max_results": 29999},
            {"max_results": 2001},
            {"start": 10000},
            {"start": -1},
            {"max_results": True},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValidationError):
                    self.client.search_page("x", **kwargs)
                self.assertEqual(self.opener.requests, [])

    def test_bad_sort_refused(self):
        with self.assertRaises(ValidationError):
            self.client.search_page("x", sort_by="lastUpdated")

    def test_ascending_order_by_relevance_is_refused_and_nothing_is_sent(self):
        # arXiv accepts it and returns the least relevant papers first.
        calls = (
            lambda: self.client.search_page("x", sort_order="ascending"),
            lambda: self.client.search("x", sort_order="ascending"),
            lambda: self.client.query_url("x", sort_by="relevance", sort_order="ascending"),
        )
        for call in calls:
            with self.assertRaisesRegex(ValidationError, "ascending order needs a date sort"):
                call()
        self.assertEqual(self.opener.requests, [])

    def test_ascending_order_by_last_update_is_sent(self):
        self.opener.add(xml_response(make_feed([])))
        self.client.search_page("x", sort_by="lastUpdatedDate", sort_order="ascending")
        self.assertEqual(self.opener.requests[0].params["sortOrder"], ["ascending"])

    def test_max_results_clamped_below_10000(self):
        self.opener.add(xml_response(make_feed([])))
        self.client.search_page("x", start=9950, max_results=200)
        self.assertEqual(self.opener.requests[0].params["max_results"], ["50"])

    def test_query_or_id_list_required(self):
        with self.assertRaises(ValidationError):
            self.client.search_page()


class SearchPagingTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.opener = FakeOpener(self.clock)
        self.client = make_client(self.clock, self.opener)

    def test_advances_by_raw_entry_count_not_parsed_count(self):
        first = [make_entry(i) for i in ids("2401", 99)] + [BAD_ENTRY]
        second = [make_entry(i) for i in ids("2401", 50, first=100)]
        self.opener.add(
            xml_response(make_feed(first, total=150)), xml_response(make_feed(second, total=150, start=100))
        )
        papers = list(self.client.search("x"))
        self.assertEqual(len(papers), 149)
        self.assertEqual(self.opener.requests[1].params["start"], ["100"])
        self.assertEqual(len(self.opener.requests), 2)

    def test_stops_at_total_results(self):
        self.opener.add(xml_response(make_feed([make_entry(i) for i in ids("2401", 30)], total=30)))
        self.assertEqual(len(list(self.client.search("x"))), 30)
        self.assertEqual(len(self.opener.requests), 1)

    def test_max_results_respected(self):
        self.opener.add(xml_response(make_feed([make_entry(i) for i in ids("2401", 25)], total=5000)))
        papers = list(self.client.search("x", max_results=25))
        self.assertEqual(len(papers), 25)
        self.assertEqual(self.opener.requests[0].params["max_results"], ["25"])

    def test_empty_page_partway_is_retried(self):
        first = [make_entry(i) for i in ids("2401", 100)]
        second = [make_entry(i) for i in ids("2401", 20, first=101)]
        self.opener.add(
            xml_response(make_feed(first, total=120)),
            xml_response(make_feed([], total=120, start=100)),
            xml_response(make_feed([], total=120, start=100)),
            xml_response(make_feed(second, total=120, start=100)),
        )
        self.assertEqual(len(list(self.client.search("x"))), 120)
        self.assertEqual(len(self.opener.requests), 4)
        self.assertEqual([r.params["start"] for r in self.opener.requests[1:]], [["100"]] * 3)

    def test_persistent_empty_page_raises_not_partial_success(self):
        self.opener.add(xml_response(make_feed([make_entry(i) for i in ids("2401", 100)], total=300)))
        for _ in range(4):
            self.opener.add(xml_response(make_feed([], total=300, start=100)))
        with self.assertRaises(ServerError):
            list(self.client.search("x"))
        self.assertEqual(len(self.opener.requests), 5)

    def test_empty_result_set(self):
        self.opener.add(xml_response(make_feed([], total=0)))
        self.assertEqual(list(self.client.search("x")), [])

    def test_truncation_flagged_at_10000(self):
        self.opener.add(xml_response(make_feed([make_entry(i) for i in ids("2401", 100)], total=25000)))
        results = self.client.search("x")
        next(iter(results))
        self.assertTrue(results.truncated)
        self.assertEqual(results.total_results, 25000)

    def test_no_truncation_when_max_results_fits(self):
        self.opener.add(xml_response(make_feed([make_entry(i) for i in ids("2401", 100)], total=25000)))
        results = self.client.search("x", max_results=100)
        list(results)
        self.assertFalse(results.truncated)

    def test_strict_raises_before_yielding(self):
        self.opener.add(xml_response(make_feed([make_entry(i) for i in ids("2401", 100)], total=25000)))
        with self.assertRaises(ValidationError):
            next(iter(self.client.search("x", strict=True)))

    def test_never_requests_start_at_or_above_10000(self):
        # Pretend every page is full: paging must stop with start + max_results <= 10000.
        def page(rec):
            start = int(rec.params["start"][0])
            n = int(rec.params["max_results"][0])
            return xml_response(
                make_feed(
                    [make_entry("2401.%05d" % (1 + (start + k) % 99999)) for k in range(n)], total=20000, start=start
                )
            )

        for _ in range(60):
            self.opener.add(page)
        client = make_client(self.clock, self.opener, page_size=200)
        results = client.search("x")
        self.assertEqual(sum(1 for _ in results), 10000)
        self.assertTrue(results.truncated)
        last = self.opener.requests[-1].params
        self.assertEqual(int(last["start"][0]) + int(last["max_results"][0]), 10000)
        self.assertEqual(len(self.opener.requests), 50)

    def test_page_size_must_be_100_to_200(self):
        with self.assertRaises(ValidationError):
            make_client(self.clock, self.opener, page_size=50)
        with self.assertRaises(ValidationError):
            self.client.search("x", page_size=500)


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.opener = FakeOpener(self.clock)
        self.client = make_client(self.clock, self.opener)

    def test_out_of_order_and_missing(self):
        self.opener.add(xml_response(make_feed([make_entry("2401.00003", 2), make_entry("2401.00001", 1)])))
        result = self.client.fetch(["2401.00001", "2401.00002", "2401.00003"])
        self.assertEqual(result.papers["2401.00001"].id, "2401.00001")
        self.assertEqual(result.papers["2401.00003"].version, 2)
        self.assertEqual(result.not_found, ["2401.00002"])
        p = self.opener.requests[0].params
        self.assertEqual(p["id_list"], ["2401.00001,2401.00002,2401.00003"])
        self.assertEqual(p["max_results"], ["3"])

    def test_invalid_ids_rejected_before_any_request(self):
        with self.assertRaises(ValidationError) as cm:
            self.client.fetch(["2401.00001", "foo", "2401.1"])
        self.assertIn("foo", str(cm.exception))
        self.assertEqual(self.opener.requests, [])

    def test_versioned_request_matches_version(self):
        self.opener.add(xml_response(make_feed([make_entry("2401.00001", 1), make_entry("2401.00001", 3)])))
        result = self.client.fetch(["2401.00001v1", "2401.00001"])
        self.assertEqual(result.papers["2401.00001v1"].version, 1)
        self.assertEqual(result.papers["2401.00001"].version, 3)

    def test_duplicates_sent_once_keys_preserved(self):
        self.opener.add(xml_response(make_feed([make_entry("2401.00001")])))
        result = self.client.fetch(["2401.00001", "arXiv:2401.00001"])
        self.assertEqual(set(result.papers), {"2401.00001", "arXiv:2401.00001"})
        self.assertEqual(self.opener.requests[0].params["id_list"], ["2401.00001"])

    def test_long_lists_are_chunked(self):
        wanted = ids("2401", 250)

        def respond(rec):
            chunk = rec.params["id_list"][0].split(",")
            self.assertEqual(rec.params["max_results"], [str(len(chunk))])
            return xml_response(make_feed([make_entry(i) for i in reversed(chunk)]))

        self.opener.add(respond, respond, respond)
        result = self.client.fetch(wanted)
        self.assertEqual(len(result.papers), 250)
        self.assertEqual(result.not_found, [])
        self.assertEqual([len(r.params["id_list"][0].split(",")) for r in self.opener.requests], [100, 100, 50])
        starts = [r.at for r in self.opener.requests]
        self.assertTrue(all(b - a >= 3.0 for a, b in itertools.pairwise(starts)))

    def test_old_style_versioned_500_falls_back_to_unversioned(self):
        self.opener.add(
            xml_response(fixture("query_error_500.xml"), status=500),
            xml_response(make_feed([make_entry("hep-th/9901001", 3, category="hep-th"), make_entry("2401.00001", 1)])),
        )
        result = self.client.fetch(["hep-th/9901001v1", "2401.00001"])
        old = result.papers["hep-th/9901001v1"]
        self.assertEqual((old.version, old.requested_version), (3, 1))
        self.assertTrue(old.version_mismatch)
        self.assertEqual(old.to_dict()["note"], "requested v1, served v3")
        self.assertIsNone(result.papers["2401.00001"].requested_version)
        self.assertEqual(self.opener.requests[1].params["id_list"], ["hep-th/9901001,2401.00001"])
        self.assertEqual(len(self.opener.requests), 2)

    def test_fetch_lists_the_url_of_every_request(self):
        self.opener.add(xml_response(make_feed([make_entry("2401.00001")])))
        result = self.client.fetch(["2401.00001", "2401.00002"])
        self.assertEqual(result.request_urls, [self.opener.requests[0].url])
        self.assertIn("id_list=2401.00001,2401.00002", result.request_urls[0])

    def test_after_the_fallback_the_failed_url_and_the_retry_are_both_listed(self):
        self.opener.add(
            xml_response(fixture("query_error_500.xml"), status=500),
            xml_response(make_feed([make_entry("hep-th/9901001", 3, category="hep-th")])),
        )
        result = self.client.fetch(["hep-th/9901001v1"])
        self.assertEqual(result.request_urls, [r.url for r in self.opener.requests])
        self.assertIn("id_list=hep-th%2F9901001v1&", result.request_urls[0])
        self.assertIn("id_list=hep-th%2F9901001&", result.request_urls[1])

    def test_fetch_to_dict_does_not_include_the_request_urls(self):
        self.opener.add(xml_response(make_feed([make_entry("2401.00001")])))
        self.assertEqual(sorted(self.client.fetch(["2401.00001"]).to_dict()), ["not_found", "papers"])

    def test_fallback_lists_version_above_latest_as_not_found(self):
        self.opener.add(
            xml_response(fixture("query_error_500.xml"), status=500),
            xml_response(
                make_feed(
                    [
                        make_entry("hep-th/9711200", 3, category="hep-th"),
                        make_entry("hep-th/9901001", 3, category="hep-th"),
                    ]
                )
            ),
        )
        result = self.client.fetch(["hep-th/9711200v1", "hep-th/9901001v99"])
        self.assertEqual(list(result.papers), ["hep-th/9711200v1"])
        self.assertEqual(result.not_found, ["hep-th/9901001v99"])

    def test_500_without_old_style_versioned_ids_is_raised(self):
        self.opener.add(xml_response(fixture("query_error_500.xml"), status=500))
        with self.assertRaises(ServerError):
            self.client.fetch(["2401.00001v1", "hep-th/9901001"])
        self.assertEqual(len(self.opener.requests), 1)

    def test_fallback_only_once(self):
        self.opener.add(
            xml_response(fixture("query_error_500.xml"), status=500),
            xml_response(fixture("query_error_500.xml"), status=500),
        )
        with self.assertRaises(ServerError):
            self.client.fetch(["hep-th/9901001v1"])
        self.assertEqual(len(self.opener.requests), 2)

    def test_an_unknown_id_is_not_found(self):
        self.opener.add(xml_response(make_feed([], total=0)))
        with self.assertRaises(NotFoundError):
            self.client.get("2401.99999")

    def test_a_known_id_returns_its_paper(self):
        self.opener.add(xml_response(make_feed([make_entry("2401.00001")])))
        self.assertEqual(self.client.get("2401.00001").id, "2401.00001")


class RevisionScanTests(unittest.TestCase):
    def test_stops_after_n_pages_with_nothing_new(self):
        clock = FakeClock()
        opener = FakeOpener(clock)
        pages = [
            [make_entry("2401.%05d" % i, 2) for i in range(1, 101)],  # all revised
            [make_entry("2401.%05d" % i, 1) for i in range(101, 201)],  # all known
            [make_entry("2401.%05d" % i, 1) for i in range(201, 301)],  # all known
        ]
        for n, entries in enumerate(pages):
            opener.add(xml_response(make_feed(entries, total=100000, start=n * 100)))
        known = {"2401.%05d" % i: 1 for i in range(1, 301)}
        client = make_client(clock, opener)
        new = list(client.scan_revisions(Q.category("cs.LG"), known, stop_after_empty_pages=2))
        self.assertEqual(len(new), 100)
        self.assertTrue(all(p.version == 2 for p in new))
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(opener.requests[0].params["sortBy"], ["lastUpdatedDate"])
        self.assertEqual(opener.requests[0].params["sortOrder"], ["descending"])

    def test_unknown_ids_are_new(self):
        clock = FakeClock()
        opener = FakeOpener(clock, [xml_response(make_feed([make_entry("2401.00001")], total=1))])
        new = list(make_client(clock, opener).scan_revisions("x", {}))
        self.assertEqual([p.id for p in new], ["2401.00001"])


if __name__ == "__main__":
    unittest.main()
