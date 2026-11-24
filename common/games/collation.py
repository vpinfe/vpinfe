"""Ordering and grouping titles nobody wrote in English.

Content, not translation. A table is called what it is called in every language, so
nothing here renames anything - it decides what order titles come in and which group
each one lands in, which `str.lower()` got wrong for every library that is not ASCII.

**The fold is base letters, not a locale's collation.** `Ähre` files under A and
`Österreich` under O, which is right for German, French and Spanish and wrong for
Swedish, where Ä and Ö close the alphabet. Getting that right needs per-locale tailoring
and a dependency to carry it; this is the rule that is right far more often than
codepoint order, which put every umlaut after Z.
"""

from __future__ import annotations

import unicodedata

# Letters NFKD leaves whole, because they are letters in their own right rather than a
# base with a mark on it. Mapped to the base a reader looking for them would try first,
# which is the English and German expectation - Danish and Norwegian close the alphabet
# with Æ, Ø, Å and are the case this rule is wrong for.
_WHOLE_LETTERS = str.maketrans({
    "æ": "ae", "ø": "o", "đ": "d", "ð": "d", "ł": "l", "þ": "th", "œ": "oe", "ħ": "h",
})

# Scripts with no small alphabet to bucket by. One group each, because bucketing them by
# character is what gave a library of 50 Japanese tables up to 50 letters in the picker:
# every kanji is `isalpha()` and every one of them became its own group.
_BULK = {
    "CJK": "漢",
    "HIRAGANA": "あ",
    # Both kana answer with the same group: they collate together, and a reader should
    # not have to know which of two lists to look in.
    "KATAKANA": "あ",
    "HANGUL": "가",
}

def fold(text: str) -> str:
    """Base letters, lowercased: what ordering and grouping both compare on.

    Decomposes and drops the combining marks, so `é` and `e` are the same letter and
    `ﬁ` is `fi`. `ß` folds to `ss` by casefold, and the letters that decompose to nothing
    are mapped by hand.
    """
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return bare.casefold().translate(_WHOLE_LETTERS)


def sort_key(text: str) -> tuple[str, str]:
    """What to order titles by.

    The fold decides, the original breaks the tie - without it `Ahre` and `Ähre` compare
    equal and their order is whatever the last caller happened to leave them in.
    """
    said = str(text or "")
    return (fold(said), said)


def _script(char: str) -> str:
    try:
        return unicodedata.name(char).split()[0]
    except ValueError:
        return ""


def letter_of(text: str) -> str:
    """The group a title files under. Digits and symbols share one.

    One definition for paging and filtering both, and it has to agree with `sort_key` or
    a page jump lands outside the group it named.
    """
    said = str(text or "").strip()
    if not said:
        return "#"

    first = said[0]
    folded = fold(first)[:1]
    if folded.isascii() and folded.isalpha():
        return folded.upper()

    if not first.isalpha():
        return "#"

    script = _script(first)
    if script in _BULK:
        return _BULK[script]
    # An alphabet of its own - Cyrillic, Greek, Hebrew, Arabic. Few enough letters that a
    # group each is a list somebody can read.
    return first.upper()
