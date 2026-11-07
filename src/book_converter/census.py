"""Dev tool: histogram of font x size x color for a page range, with sample text.

Usage: uv run census <pdf> <firstPage> <lastPage>
"""

from __future__ import annotations

import sys
from collections import defaultdict

from .core.pdf import Book


def main() -> None:
    pdf, a, b = sys.argv[1:4]
    book = Book(pdf)
    stats: dict[tuple, list] = defaultdict(lambda: [0, 0, ""])
    for s in book.all_spans(int(a), int(b)):
        if not s.text.strip():
            continue
        k = (s.font, s.size, s.color)
        st = stats[k]
        st[0] += 1
        st[1] += len(s.text)
        if len(st[2]) < 50:
            st[2] = (st[2] + " | " + s.text.strip())[:60] if st[2] else s.text.strip()[:60]
    for (font, size, color), (n, chars, sample) in sorted(stats.items(), key=lambda kv: -kv[1][1]):
        print(f"{n:5} {chars:6}  {font:28} {size:6.2f} {color}  {sample}")


if __name__ == "__main__":
    main()
