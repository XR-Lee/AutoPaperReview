"""Join pdftotext small-caps that were split after the first letter.

pdftotext often writes a heading as ``A BSTRACT`` or ``I NTRODUCTION``. The
letters are still the heading. A normal sentence such as ``A novel method``
is left unchanged, because the remainder of the word is not all capitals.
"""

from __future__ import annotations

import re

_JOIN = re.compile(r"(?<![A-Za-z])([A-Z])\s+([A-Z]{2,})")


def collapse_smallcaps(text: str) -> str:
    """Join ``A BSTRACT`` into ``ABSTRACT``. Other lines stay as they are."""
    lines: list[str] = []
    for line in text.splitlines():
        previous = None
        while previous != line:
            previous = line
            line = _JOIN.sub(r"\1\2", line)
        lines.append(line)
    return "\n".join(lines)
