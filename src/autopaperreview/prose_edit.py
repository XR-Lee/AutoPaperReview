"""Lightweight readability check and conservative rewrite for review bodies.

The metrics follow public formulas and the small set of checks that show up
in write-good (long sentences, passive voice, weasel wording). The rewrite
only applies phrase substitutions and semicolon splits that keep citations
and quotations intact. Heavier tools are intentionally not dependencies:

- textstat implements the same Flesch formula behind a package import.
- proselint and Vale pull in large rule sets and, for Vale, an external binary.
- write-good is a Node program with the same regex ideas used here.
- An LLM rewrite can change a reviewer's meaning, so it stays behind the
  translation/model adapter instead of running on every export.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# Latin sentences end at .!? plus a space and a capital or quote, so scores
# (8.0), DOIs, and filenames stay intact. CJK sentences end at 。！？ even when
# the next sentence is not separated by a space.
_BOUNDARY = re.compile(
    r"(?:(?<=[。！？])\s*|(?<=[.!?])\s+(?=[A-Z\"“]))"
)
_WORD = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_CJK = re.compile(r"[\u3400-\u9fff]")
_CITE = re.compile(r"\[\[[^\]]+\]\(#[^)]+\)\]")
_PASSIVE = re.compile(
    r"\b(?:am|is|are|was|were|be|been|being)\s+"
    r"(?:[a-z]+ed|built|shown|made|known|seen|given|found|done|taken|written|left|lost|paid|run)\b",
    re.IGNORECASE,
)
_ACRONYM = re.compile(r"\b[A-Z]{3,}\b")
_VOWEL = re.compile(r"[aeiouy]+", re.IGNORECASE)

# Common academic words are not counted as jargon. Unexplained acronyms and
# very long technical stacks still are.
_KNOWN = {
    "abstract",
    "accuracy",
    "analysis",
    "baseline",
    "comparison",
    "contribution",
    "evaluation",
    "experiment",
    "experimental",
    "generalization",
    "hypothesis",
    "independent",
    "information",
    "limitation",
    "manuscript",
    "measurement",
    "performance",
    "reproducibility",
    "significant",
    "uncertainty",
}
_ACRONYM_OK = {"AAAI", "ACL", "CI", "DOI", "ICLR", "PDF", "SAR", "TMLR", "URL"}

_WORDY: tuple[tuple[str, str], ...] = (
    ("in spite of the fact that", "although"),
    ("due to the fact that", "because"),
    ("it is important to note that", ""),
    ("a large number of", "many"),
    ("in the event that", "if"),
    ("at this point in time", "now"),
    ("has the ability to", "can"),
    ("in order to", "to"),
    ("for the purpose of", "to"),
    ("a number of", "several"),
)

_STRUCTURE = (
    ("Summary", "摘要"),
    ("Strengths", "优点"),
    ("Weaknesses", "不足"),
    ("Questions", "问题"),
    ("Dimension Scores", "维度评分"),
)

_LONG_WORDS = 40
_LONG_CHARS = 80


def _sentences(text: str) -> list[str]:
    found = [part.strip() for part in _BOUNDARY.split(text) if part.strip()]
    return found or ([text.strip()] if text.strip() else [])


def _syllables(word: str) -> int:
    groups = _VOWEL.findall(word.lower())
    count = len(groups)
    if word.lower().endswith("e") and count > 1:
        count -= 1
    return max(count, 1)


def _is_cjk_heavy(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha() or ("\u3400" <= ch <= "\u9fff")]
    if not letters:
        return False
    cjk = sum(1 for ch in letters if "\u3400" <= ch <= "\u9fff")
    return cjk / len(letters) > 0.4


def _prose(markdown: str) -> str:
    kept: list[str] = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(">") or stripped.startswith("|"):
            continue
        if stripped.startswith("<a ") or stripped.startswith("- <a "):
            continue
        kept.append(stripped)
    return "\n".join(kept)


def _structure(markdown: str) -> dict[str, list[str]]:
    present: list[str] = []
    missing: list[str] = []
    for english, chinese in _STRUCTURE:
        if english in markdown or chinese in markdown:
            present.append(english)
        else:
            missing.append(english)
    return {"present": present, "missing": missing}


def measure_prose(markdown: str, *, language: str = "en") -> dict[str, object]:
    """Return deterministic readability metrics for one review body."""
    prose = _prose(markdown)
    sentences = _sentences(prose)
    cjk = _is_cjk_heavy(prose) or language == "zh-Hans"
    structure = _structure(markdown)
    if cjk:
        chars = len(_CJK.findall(prose))
        lengths = [len(_CJK.findall(sentence)) for sentence in sentences]
        redundant = _redundant(sentences)
        return {
            "language": language,
            "script": "cjk",
            "sentence_count": len(sentences),
            "char_count": chars,
            "mean_sentence_chars": round(chars / len(sentences), 2) if sentences else 0,
            "long_sentence_count": sum(1 for length in lengths if length > _LONG_CHARS),
            "passive_voice_count": None,
            "jargon_count": None,
            "redundant_sentence_count": redundant,
            "flesch_reading_ease": None,
            "structure_present": structure["present"],
            "structure_missing": structure["missing"],
        }
    words = _WORD.findall(prose)
    lengths = [len(_WORD.findall(sentence)) for sentence in sentences]
    syllables = sum(_syllables(word) for word in words)
    sentence_count = max(len(sentences), 1)
    word_count = len(words)
    flesch = None
    if word_count and sentences:
        flesch = round(
            206.835 - 1.015 * (word_count / sentence_count) - 84.6 * (syllables / word_count),
            2,
        )
    jargon = 0
    for word in words:
        if word.lower() in _KNOWN:
            continue
        if _syllables(word) >= 4 or len(word) >= 14:
            jargon += 1
    jargon += sum(1 for token in _ACRONYM.findall(prose) if token not in _ACRONYM_OK)
    return {
        "language": language,
        "script": "latin",
        "sentence_count": len(sentences),
        "word_count": word_count,
        "mean_sentence_words": round(word_count / sentence_count, 2) if words else 0,
        "max_sentence_words": max(lengths) if lengths else 0,
        "long_sentence_count": sum(1 for length in lengths if length > 28),
        "passive_voice_count": len(_PASSIVE.findall(prose)),
        "jargon_count": jargon,
        "redundant_sentence_count": _redundant(sentences),
        "flesch_reading_ease": flesch,
        "structure_present": structure["present"],
        "structure_missing": structure["missing"],
    }


def _redundant(sentences: Sequence[str]) -> int:
    seen: set[str] = set()
    extra = 0
    for sentence in sentences:
        key = re.sub(r"\s+", " ", sentence).strip().lower()
        if len(key) < 20:
            continue
        if key in seen:
            extra += 1
        seen.add(key)
    return extra


def _rewrite_sentence(sentence: str) -> tuple[str, list[str]]:
    if _is_cjk_heavy(sentence):
        return sentence, []
    edits: list[str] = []
    updated = sentence
    for source, target in _WORDY:
        pattern = re.compile(re.escape(source), re.IGNORECASE)

        def _replace(match: re.Match[str], replacement: str = target) -> str:
            if not replacement:
                return ""
            original = match.group(0)
            before = match.string[: match.start()].rstrip()
            starts_sentence = match.start() == 0 or before.endswith((".", "!", "?"))
            if original[:1].isupper() or starts_sentence:
                return replacement[:1].upper() + replacement[1:]
            return replacement

        rewritten, count = pattern.subn(_replace, updated)
        if count:
            updated = rewritten
            edits.append(source)
    updated = re.sub(r"\s{2,}", " ", updated).strip()
    updated = re.sub(r"\s+([,.;])", r"\1", updated)
    words = _WORD.findall(updated)
    if len(words) > _LONG_WORDS and "; " in updated:
        head, tail = updated.split("; ", 1)
        tail = tail[:1].upper() + tail[1:] if tail else tail
        if not head.endswith("."):
            head = head.rstrip() + "."
        if tail and not tail.endswith((".", "!", "?")):
            tail = tail.rstrip() + "."
        updated = f"{head} {tail}".strip()
        edits.append("long_sentence_split")
    return updated, edits


def _rewrite_paragraph(paragraph: str) -> tuple[str, list[str]]:
    if paragraph.lstrip().startswith(">") or paragraph.lstrip().startswith("#"):
        return paragraph, []
    protected: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"@@CITE{len(protected) - 1}@@"

    shielded = _CITE.sub(_hold, paragraph)
    pieces = _sentences(shielded)
    if not pieces:
        return paragraph, []
    rewritten: list[str] = []
    edits: list[str] = []
    previous = ""
    for piece in pieces:
        updated, piece_edits = _rewrite_sentence(piece)
        key = re.sub(r"\s+", " ", updated).strip().lower()
        if key and key == previous and len(key) >= 20:
            edits.append("duplicate_sentence")
            continue
        previous = key
        rewritten.append(updated)
        edits.extend(piece_edits)
    text = " ".join(rewritten)
    for index, token in enumerate(protected):
        text = text.replace(f"@@CITE{index}@@", token)
    if text.count("[[") != paragraph.count("[["):
        return paragraph, []
    return text, edits


def edit_body(markdown: str, *, language: str = "en") -> tuple[str, dict[str, object]]:
    """Rewrite review prose and report metrics before and after.

    Block quotes, headings, and `[E3, p.5](#...)` citations are preserved.
    A paragraph is left unchanged when a rewrite would drop a citation.
    """
    before = measure_prose(markdown, language=language)
    edits: list[str] = []
    blocks = re.split(r"(\n\s*\n)", markdown)
    output: list[str] = []
    for block in blocks:
        if not block.strip() or block.startswith("\n"):
            output.append(block)
            continue
        first = block.strip().splitlines()[0]
        if first.startswith("#") or first.startswith(">") or first.startswith("|") or first.startswith("- <a "):
            output.append(block)
            continue
        rewritten, block_edits = _rewrite_paragraph(block)
        edits.extend(block_edits)
        output.append(rewritten)
    edited = "".join(output)
    edited = re.sub(r"\n{3,}", "\n\n", edited)
    if not edited.endswith("\n"):
        edited += "\n"
    after = measure_prose(edited, language=language)
    return edited, {
        "language": language,
        "before": before,
        "after": after,
        "edits": sorted(set(edits)),
        "checker": "autopaperreview.prose_edit",
    }
