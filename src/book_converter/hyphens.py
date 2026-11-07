"""Decide whether a line-end hyphen is a soft break (join) or a real hyphen (keep),
using the book's own unbroken text first and the system dictionary second."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, asdict

from .pdf import Span
from .styles import is_figure_text

WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*[A-Za-z]|[A-Za-z]")


@dataclass
class Decision:
    a: str
    b: str
    keep: bool
    why: str
    where: str


class Resolver:
    def __init__(self, spans: list[Span], dict_path: str = "/usr/share/dict/words"):
        self.corpus: Counter[str] = Counter()
        for s in spans:
            if is_figure_text(s) or s.font.startswith("Courier"):
                continue
            t = re.sub(r"[A-Za-z]+-\s*$", "", s.text)  # drop a trailing broken word
            for w in WORD.findall(t):
                self.corpus[w.lower()] += 1
        with open(dict_path, encoding="utf-8", errors="replace") as f:
            self.dict = {w.strip().lower() for w in f}
        self.log: list[Decision] = []

    def _is_word(self, w: str) -> bool:
        w = w.lower()
        stems = [w, re.sub(r"ies$", "y", w), re.sub(r"es$", "", w), re.sub(r"s$", "", w), re.sub(r"ed$", "", w),
                 re.sub(r"ed$", "e", w), re.sub(r"ing$", "", w), re.sub(r"ing$", "e", w), re.sub(r"ly$", "", w)]
        return any(s in self.dict or self.corpus[s] > 0 for s in stems)

    def resolve(self, a: str, b: str, where: str) -> bool:
        """True = keep the hyphen, False = join the halves."""
        j, h = (a + b).lower(), (a + "-" + b).lower()
        jc, hc = self.corpus[j], self.corpus[h]
        if jc and not hc:
            keep, why = False, "book-joined"
        elif hc and not jc:
            keep, why = True, "book-hyphenated"
        elif jc and hc:
            keep, why = hc >= jc, f"AMBIGUOUS book has both (joined {jc}, hyphenated {hc})"
        elif self._is_word(j):
            keep, why = False, "dictionary-joined"
        elif len(a) >= 3 and self._is_word(a) and self._is_word(b):
            keep, why = True, "compound"
        else:
            keep, why = False, "REVIEW unknown, joined by default"
        self.log.append(Decision(a, b, keep, why, where))
        return keep

    def log_dicts(self) -> list[dict]:
        return [asdict(d) for d in self.log]
