"""Pure-Python text normalisation for search.

Used by:
  - the indexer (writes ``normalized_text`` column for LIKE fallback)
  - the query path (turns user input into a normalised lookup key)

Choices:
  - Lowercase via ``casefold`` (handles ß → ss, Σ → σ).
  - Strip accents via NFKD decomposition + diacritic-class strip.
    Bengali characters survive (they're not in the diacritic class) —
    we want lookups for "প্যারাসিটামল" to match exactly.
  - Replace non-alphanumeric runs with a single space.
  - Collapse whitespace.

Tokenization:
  - For ts_query construction. Splits on whitespace, drops tokens
    shorter than 2 chars, then joins with ``& `` so the search is
    AND-by-default (matching all words).
  - The query path can opt into prefix-match by appending ':*' to
    each token — useful for typeahead.
"""

from __future__ import annotations

import re
import unicodedata


_WHITESPACE_RE = re.compile(r"\s+", re.UNICODE)


def _is_keepable(c: str) -> bool:
    """True if ``c`` is a letter, number, mark, or whitespace.

    Specifically we keep Unicode categories starting with:
      L* — Letters (any script: Latin, Bengali, Devanagari, Arabic, ...)
      N* — Numbers
      M* — Marks (combining; CRITICAL for Indic scripts where halants
           and vowel signs are separate codepoints carrying meaning)
      Z* — Separators / whitespace

    This keeps "প্যারাসিটামল" intact (the halant U+09CD is category Mn
    — would be stripped by ``[^\\w\\s]`` but \\w only covers L+N+_).
    """
    if c.isspace():
        return True
    cat = unicodedata.category(c)
    return cat and cat[0] in ("L", "N", "M", "Z")


def normalize_search_text(value: str | None) -> str:
    """Pipeline: NFKD → strip Latin diacritics → casefold → strip punct → collapse ws.

    IMPORTANT: only strips combining marks in the **Latin** combining
    diacritic block (U+0300–U+036F). Bengali halant (U+09CD), Devanagari
    halant (U+094D), Arabic shadda (U+0651), and other script-essential
    combining marks are preserved. Critical for Bangladesh — Bengali
    product names like "প্যারাসিটামল" must round-trip unchanged or the
    word breaks (without the halant the consonant cluster falls apart
    into a different, meaningless word).
    """
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    # Strip ONLY Latin diacritic combining marks (U+0300–U+036F).
    # Indic / Arabic / Hebrew etc. combining marks are semantically
    # significant and must survive.
    no_latin_diacritics = "".join(
        c for c in decomposed
        if not (0x0300 <= ord(c) <= 0x036F)
    )
    # Keep letters/numbers/marks/whitespace, replace everything else
    # (punctuation, symbols) with a single space. Standard regex
    # ``[^\\w\\s]`` doesn't work because \\w omits Unicode Mark
    # categories — that drops Indic combining vowels + halants.
    cleaned = "".join(
        c if _is_keepable(c) else " "
        for c in no_latin_diacritics.casefold()
    )
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def tokenize_query(value: str | None, *, min_token_len: int = 2) -> list[str]:
    """Split a user query into search tokens.

    Tokens shorter than ``min_token_len`` are dropped — they explode
    the index search and rarely reflect user intent ("a", "i", etc.).
    Returns an empty list for blank/None inputs.
    """
    normalized = normalize_search_text(value)
    if not normalized:
        return []
    return [t for t in normalized.split(" ") if len(t) >= min_token_len]


def to_tsquery_string(value: str | None, *, prefix: bool = False) -> str:
    """Build a Postgres tsquery expression from a user query.

    Each token becomes an AND-ed term. With ``prefix=True``, each
    token gets ':*' appended for prefix matching (useful for typeahead).

    Returns empty string if the query has no usable tokens — the
    service layer treats that as "no FTS, fall back to LIKE on
    normalized_text".
    """
    tokens = tokenize_query(value)
    if not tokens:
        return ""
    if prefix:
        return " & ".join(f"{t}:*" for t in tokens)
    return " & ".join(tokens)
