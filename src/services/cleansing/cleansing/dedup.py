"""SimHash near-duplicate detection.

Wire syndication produces near-identical copies of the same article across outlets. A 64-bit SimHash
over token shingles lets us drop a copy whose Hamming distance to a recently-seen fingerprint is
within the configured threshold, before spending embedding or LLM work on it (functional document
sec 3, acceptance criterion 2).
"""

from __future__ import annotations

import hashlib
import re

_TOKEN = re.compile(r"\w+", re.UNICODE)
_HASH_BITS = 64
_BIT_MASK = (1 << _HASH_BITS) - 1


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _feature_hash(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") & _BIT_MASK


def simhash(text: str) -> int:
    """Compute a 64-bit SimHash of the text. Empty text hashes to 0."""
    tokens = _tokens(text)
    if not tokens:
        return 0
    weights = [0] * _HASH_BITS
    for token in tokens:
        h = _feature_hash(token)
        for bit in range(_HASH_BITS):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    fingerprint = 0
    for bit in range(_HASH_BITS):
        if weights[bit] > 0:
            fingerprint |= 1 << bit
    return fingerprint


def hamming_distance(left: int, right: int) -> int:
    """Number of differing bits between two 64-bit fingerprints."""
    return ((left ^ right) & _BIT_MASK).bit_count()


def is_near_duplicate(candidate: int, existing: int, max_distance: int) -> bool:
    """True when the candidate fingerprint is within `max_distance` bits of an existing one."""
    return hamming_distance(candidate, existing) <= max_distance
