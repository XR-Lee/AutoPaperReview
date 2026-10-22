"""Lightweight readability check and conservative rewrite for review bodies.

The metrics follow public formulas and the small set of checks that show up
in write-good (long sentences, passive voice, weasel wording). The rewrite
only applies phrase substitutions and semicolon splits that keep citations
and quotations intact. Heavier tools are intentionally not dependencies:

- textstat implements the same Flesch formula behind a package import.
- proselint and Vale pull in large rule sets and, for Vale, an external binary.
- write-good is a Node program with the same regex ideas used here.
- An LLM rewrite can change a reviewer's meaning, so it stays behind the
  translation/model adapter instead of running on every export. The export
  itself caps sentences at 35 words and turns semicolon chains into bullets.
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
    ("Score justification", "分数说明"),
)

_MAX_SENTENCE_WORDS = 35
_LONG_CHARS = 80


def _sentences(text: str) -> list[str]:
    found = [part.strip() for part in _BOUNDARY.split(text) if part.strip()]
    return found or ([text.strip()] if text.strip() else [])


def _measure_sentences(prose: str) -> list[str]:
    """One sentence per bullet, with citation markup left out of the count."""
    found: list[str] = []
    for line in prose.splitlines():
        line = _CITE.sub(" ", line)
        line = re.sub(r"^-\s+", "", line.strip())
        line = " ".join(line.split())
        if not line:
            continue
        found.extend(part.strip() for part in _sentences(line) if part.strip())
    return found


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
    sentences = _measure_sentences(prose)
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
        "long_sentence_count": sum(1 for length in lengths if length > _MAX_SENTENCE_WORDS),
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


_DANGLING = re.compile(r"(?:,?\s+)(?:and|but|so|which|or)$", re.IGNORECASE)
_PLACEHOLDER = "\ue000{index}\ue001"


def _shield_cites(text: str) -> tuple[str, list[str]]:
    protected: list[str] = []

    def _hold(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return _PLACEHOLDER.format(index=len(protected) - 1)

    return _CITE.sub(_hold, text), protected


def _restore_cites(text: str, protected: Sequence[str]) -> str:
    for index, token in enumerate(protected):
        text = text.replace(_PLACEHOLDER.format(index=index), token)
    return text


def _finish_sentence(text: str) -> str:
    cleaned = " ".join(text.split()).strip().rstrip(",")
    cleaned = _DANGLING.sub("", cleaned).strip().rstrip(",")
    if not cleaned:
        return ""
    if cleaned[0].islower():
        cleaned = cleaned[0].upper() + cleaned[1:]
    if not cleaned.endswith((".", "!", "?", "。", "！", "？", "]")):
        cleaned += "."
    return cleaned


def _split_long_sentence(sentence: str) -> list[str]:
    """Break one sentence so each piece stays within the word cap."""
    spans = list(_WORD.finditer(sentence))
    if len(spans) <= _MAX_SENTENCE_WORDS:
        finished = _finish_sentence(sentence)
        return [finished] if finished else []
    if "; " in sentence:
        pieces: list[str] = []
        for part in sentence.split("; "):
            pieces.extend(_split_long_sentence(part))
        return pieces
    best: tuple[int, int] | None = None
    for match in re.finditer(r", (?:and |but |so |which |or )?", sentence):
        left = len(_WORD.findall(sentence[: match.start()]))
        right = len(_WORD.findall(sentence[match.end() :]))
        if left < 8 or right < 8:
            continue
        balance = abs(left - right)
        if best is None or balance < best[0]:
            best = (balance, match.end())
    cut = best[1] if best is not None else spans[_MAX_SENTENCE_WORDS].start()
    head = sentence[:cut].strip()
    tail = sentence[cut:].strip()
    dangling = _DANGLING.search(head)
    if dangling and dangling.start() > 0:
        moved = head[dangling.start() :].lstrip(" ,")
        head = head[: dangling.start()].strip().rstrip(",")
        tail = f"{moved} {tail}".strip()
    head = head.strip().rstrip(",")
    if not head or not tail:
        cut = spans[_MAX_SENTENCE_WORDS].start()
        head = sentence[:cut].strip().rstrip(",")
        tail = sentence[cut:].strip()
    return [*_split_long_sentence(head), *_split_long_sentence(tail)]


def _rewrite_sentence(sentence: str) -> tuple[str, list[str]]:
    if _is_cjk_heavy(sentence):
        chars = _CJK.findall(sentence)
        if len(chars) <= _LONG_CHARS or "，" not in sentence:
            return sentence, []
        pieces = [part.strip() for part in sentence.split("，") if part.strip()]
        if len(pieces) < 2:
            return sentence, []
        finished = []
        for part in pieces:
            if not part.endswith(("。", "！", "？")):
                part += "。"
            finished.append(part)
        return "".join(finished), ["cjk_sentence_split"]
    shielded, protected = _shield_cites(sentence)
    edits: list[str] = []
    updated = shielded
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
    pieces = _split_long_sentence(updated)
    if len(pieces) > 1 or (pieces and pieces[0] != updated.strip()):
        if len(_WORD.findall(updated)) > _MAX_SENTENCE_WORDS or "; " in updated:
            edits.append("long_sentence_split")
    text = _restore_cites(" ".join(pieces), protected)
    if text.count("[[") != sentence.count("[[") or "\ue000" in text:
        return sentence, []
    return text, edits


def _rewrite_paragraph(paragraph: str) -> tuple[str, list[str]]:
    if paragraph.lstrip().startswith(">") or paragraph.lstrip().startswith("#"):
        return paragraph, []
    shielded, protected = _shield_cites(paragraph)
    if "; " in shielded and not paragraph.lstrip().startswith("-"):
        chunks = [part.strip() for part in shielded.split("; ") if part.strip()]
        if len(chunks) >= 2:
            bullets: list[str] = []
            edits: list[str] = []
            for chunk in chunks:
                updated, piece_edits = _rewrite_sentence(chunk)
                bullets.append(f"- {updated}")
                edits.extend(piece_edits)
            edits.append("semicolon_bullets")
            text = _restore_cites("\n".join(bullets), protected)
            if text.count("[[") != paragraph.count("[[") or "\ue000" in text:
                return paragraph, []
            return text, edits
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
    text = _restore_cites(" ".join(rewritten), protected)
    if text.count("[[") != paragraph.count("[[") or "\ue000" in text:
        return paragraph, []
    return text, edits


def _rewrite_bullet_block(block: str) -> tuple[str, list[str]]:
    edits: list[str] = []
    lines: list[str] = []
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- ") and not stripped.startswith("- <a ") and "id=\"marker-" not in stripped:
            indent = line[: len(line) - len(stripped)]
            updated, piece_edits = _rewrite_sentence(stripped[2:])
            lines.append(f"{indent}- {updated}")
            edits.extend(piece_edits)
        else:
            lines.append(line)
    return "\n".join(lines), edits


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
        if any(line.lstrip().startswith("- ") for line in block.splitlines()):
            rewritten, block_edits = _rewrite_bullet_block(block)
            edits.extend(block_edits)
            output.append(rewritten)
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
