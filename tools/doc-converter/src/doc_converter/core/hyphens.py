"""Decide whether a line-end hyphen is a soft break (join) or a real hyphen (keep),
using the book's own unbroken text first and the system dictionary second."""

from __future__ import annotations

import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, asdict

from .pdf import Span

WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*[A-Za-z]|[A-Za-z]")


@dataclass
class Decision:
    a: str
    b: str
    keep: bool
    why: str
    where: str


class Resolver:
    DICT_PATH = "/usr/share/dict/words"

    def __init__(self, spans: list[Span], is_code: Callable[[Span], bool], dict_path: str = DICT_PATH):
        """is_code: code text, which is kept out of the corpus of unbroken words."""
        self.corpus: Counter[str] = Counter()
        for s in spans:
            if is_code(s):
                continue
            t = re.sub(r"[A-Za-z]+-\s*$", "", s.text)  # drop a trailing broken word
            for w in WORD.findall(t):
                self.corpus[w.lower()] += 1
        try:
            with open(dict_path, encoding="utf-8", errors="replace") as f:
                self.dict = {w.strip().lower() for w in f}
        except OSError as e:
            sys.exit(f"cannot read the system dictionary {dict_path} ({e.strerror}); hyphen decisions need it. "
                     "On Debian or Ubuntu: apt-get install wamerican")
        self.log: list[Decision] = []
        self.code_hyphenated = Counter(
            w.lower() for sp in spans if is_code(sp)
            for w in re.findall(r"[A-Za-z]+-[A-Za-z]+", sp.text))

    @staticmethod
    def _stems(w: str) -> list[str]:
        w = w.lower()
        return [w, re.sub(r"ies$", "y", w), re.sub(r"es$", "", w), re.sub(r"s$", "", w), re.sub(r"ed$", "", w),
                re.sub(r"ed$", "e", w), re.sub(r"ing$", "", w), re.sub(r"ing$", "e", w), re.sub(r"ly$", "", w),
                re.sub(r"est$", "", w)]

    def _is_word(self, w: str) -> bool:
        if not hasattr(self, "_corpus_stems"):
            # stems of every unbroken word in the book, so "overcomplicating" matches "overcomplicated"
            self._corpus_stems = {st for c in self.corpus for st in self._stems(c) if len(st) >= 4}
        return any(s in self.dict or self.corpus[s] > 0 or s in self._corpus_stems for s in self._stems(w))

    def resolve(self, a: str, b: str, where: str, chain: bool = False) -> bool:
        """True = keep the hyphen, False = join the halves.

        chain: the broken word is itself the end of a hyphenated compound ("close-to-|production").
        """
        j, h = (a + b).lower(), (a + "-" + b).lower()
        jc, hc = self.corpus[j], self.corpus[h]
        if chain and not jc:
            keep, why = True, "compound-chain"
        elif jc and not hc:
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

    def resolve_code(self, a: str, b: str, where: str) -> bool:
        """Inline code broken at a line end: join unless the book's code has the hyphenated form."""
        keep = self.code_hyphenated[(a + "-" + b).lower()] > 0
        self.log.append(Decision(a, b, keep, "code-hyphenated" if keep else "code-joined", where))
        return keep

    def log_dicts(self) -> list[dict]:
        return [asdict(d) for d in self.log]
