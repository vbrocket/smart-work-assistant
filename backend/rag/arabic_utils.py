"""Arabic text normalization utilities for BM25 tokenization and general preprocessing."""

from __future__ import annotations

import re
import unicodedata
from typing import List

# Unicode ranges
_TATWEEL = "\u0640"
_ALEF_FORMS = re.compile("[\u0622\u0623\u0625]")  # Alef with madda / hamza above / hamza below
_ALEF_PLAIN = "\u0627"
_TEH_MARBUTA = "\u0629"
_HAH = "\u0647"
_DIACRITICS = re.compile("[\u064B-\u065F\u0670]")


def _normalize_presentation_forms(text: str) -> str:
    """Convert Arabic Presentation Forms (U+FB50-FDFF, U+FE70-FEFF) to
    standard Arabic letters (U+0600-U+06FF) using NFKC normalization.

    Many PDFs encode Arabic text using presentation form codepoints which
    look identical but are different Unicode values, breaking token matching.
    """
    return unicodedata.normalize("NFKC", text)


def normalize_arabic(text: str) -> str:
    """Arabic normalization for search/BM25 purposes.

    - Decompose Arabic Presentation Forms to standard Arabic
    - Remove tatweel (kashida)
    - Normalize Alef forms -> bare Alef
    - Remove diacritics (tashkeel)
    - Normalize Teh Marbuta -> Heh
    - Collapse whitespace
    """
    text = _normalize_presentation_forms(text)
    text = text.replace(_TATWEEL, "")
    text = _ALEF_FORMS.sub(_ALEF_PLAIN, text)
    text = _DIACRITICS.sub("", text)
    text = text.replace(_TEH_MARBUTA, _HAH)
    text = re.sub(r"\s+", " ", text).strip()
    return text


_AL_PREFIXES = re.compile(r"^(وبال|فبال|وكال|كال|بال|وال|فال|ولل|لل|ال)")


def _strip_article(token: str) -> str:
    """Remove Arabic definite article and its common prefix combinations."""
    stripped = _AL_PREFIXES.sub("", token)
    if len(stripped) >= 2:
        return stripped
    return token


def tokenize_arabic(text: str) -> List[str]:
    """Normalize, strip definite article patterns, then split for BM25."""
    normed = normalize_arabic(text)
    tokens = re.split(r"[^\w\u0600-\u06FF]+", normed)
    return [_strip_article(t) for t in tokens if t]
