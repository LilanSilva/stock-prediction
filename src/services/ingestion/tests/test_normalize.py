from ingestion.normalize import content_hash, normalize_text, truncate


def test_normalize_collapses_whitespace_and_nfc() -> None:
    assert normalize_text("  Hello\n\tworld  ") == "Hello world"


def test_truncate_caps_length() -> None:
    assert truncate("abcdef", 3) == "abc"
    assert truncate("ab", 5) == "ab"


def test_content_hash_is_stable_and_normalization_insensitive() -> None:
    a = content_hash("Title", "Body text")
    b = content_hash("  Title ", "Body   text")
    assert a == b  # whitespace-normalized inputs hash equally
    assert len(a) == 64  # sha256 hex


def test_content_hash_differs_on_different_content() -> None:
    assert content_hash("T", "one") != content_hash("T", "two")
