import unittest

from arxiv_client import ValidationError, parse_id
from arxiv_client.ids import is_valid_id, split_entry_id, split_oai_id


class ParseIdTests(unittest.TestCase):
    def test_new_style(self):
        a = parse_id("2301.00001")
        self.assertEqual((a.base, a.version, a.old_style), ("2301.00001", None, False))
        a = parse_id("1706.03762v7")
        self.assertEqual((a.base, a.version), ("1706.03762", 7))
        self.assertEqual(str(a), "1706.03762v7")

    def test_arxiv_prefix_is_dropped(self):
        self.assertEqual(str(parse_id("arXiv:2301.00001v2")), "2301.00001v2")

    def test_four_digit_numbers_until_1412(self):
        self.assertEqual(parse_id("0704.0001").base, "0704.0001")
        self.assertEqual(parse_id("1412.9999").base, "1412.9999")
        for bad in ("0704.00001", "1412.00001"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    parse_id(bad)

    def test_five_digit_numbers_from_1501(self):
        self.assertEqual(parse_id("1501.00001").base, "1501.00001")
        with self.assertRaises(ValidationError):
            parse_id("1501.0001")

    def test_new_style_starts_at_0704(self):
        with self.assertRaises(ValidationError):
            parse_id("0703.0001")

    def test_zero_sequence_number_rejected(self):
        for bad in ("2301.00000", "hep-th/9901000"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    parse_id(bad)

    def test_old_style(self):
        a = parse_id("hep-th/9901001v1")
        self.assertEqual((a.base, a.version, a.old_style), ("hep-th/9901001", 1, True))
        self.assertEqual(parse_id("cond-mat/0207270").base, "cond-mat/0207270")

    def test_old_style_subject_class_is_dropped(self):
        a = parse_id("math.CA/0611800v2")
        self.assertEqual(str(a), "math/0611800v2")

    def test_old_style_range_9107_to_0703(self):
        self.assertTrue(is_valid_id("hep-th/9108001"))
        self.assertTrue(is_valid_id("astro-ph/0703001"))
        for bad in ("hep-th/9106001", "astro-ph/0704001"):
            with self.subTest(bad=bad):
                self.assertFalse(is_valid_id(bad))

    def test_malformed_ids_are_invalid(self):
        for bad in ("foo", "", "2301.1", "2313.00001", "HEP-TH/9901001", "2301.00001v0", "1706.03762 v1", None):
            with self.subTest(bad=bad):
                self.assertFalse(is_valid_id(bad))


class SplitTests(unittest.TestCase):
    def test_entry_id(self):
        self.assertEqual(split_entry_id("http://arxiv.org/abs/2301.00001v2"), ("2301.00001", 2))
        self.assertEqual(split_entry_id("http://arxiv.org/abs/hep-th/9901001v3"), ("hep-th/9901001", 3))
        self.assertEqual(split_entry_id("http://arxiv.org/abs/chao-dyn/9301001v1"), ("chao-dyn/9301001", 1))

    def test_oai_id(self):
        self.assertEqual(split_oai_id("oai:arXiv.org:2611.01234v1"), ("2611.01234", 1))
        self.assertEqual(split_oai_id("oai:arXiv.org:hep-th/9901001v4"), ("hep-th/9901001", 4))
        with self.assertRaises(ValueError):
            split_oai_id("https://arxiv.org/abs/2611.01234")


if __name__ == "__main__":
    unittest.main()
