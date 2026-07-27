from __future__ import annotations

from cleansing.dedup import hamming_distance, is_near_duplicate, simhash


def test_simhash_is_deterministic() -> None:
    text = "Central bank raises interest rates by 25 basis points"
    assert simhash(text) == simhash(text)


def test_empty_text_hashes_to_zero() -> None:
    assert simhash("") == 0
    assert simhash("   ") == 0


def test_near_identical_wire_copies_are_close() -> None:
    original = "OPEC agrees to cut oil output by two million barrels per day starting November"
    # A syndicated copy with a trivial edit (outlet tag) should be within a few bits.
    syndicated = "OPEC agrees to cut oil output by two million barrels per day starting November."
    distance = hamming_distance(simhash(original), simhash(syndicated))
    assert distance <= 3
    assert is_near_duplicate(simhash(original), simhash(syndicated), max_distance=3)


def test_distinct_articles_are_far() -> None:
    a = "Gold prices surge as investors seek safe haven amid market turmoil"
    b = "Central bank holds interest rates steady citing inflation concerns"
    assert hamming_distance(simhash(a), simhash(b)) > 3


def test_hamming_distance_symmetry() -> None:
    a = simhash("earthquake strikes coastal region")
    b = simhash("flood devastates farmland")
    assert hamming_distance(a, b) == hamming_distance(b, a)
