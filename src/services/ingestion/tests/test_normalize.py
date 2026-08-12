from ingestion.normalize import content_hash, normalize_text, strip_html_page, truncate


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


def test_strip_html_page_returns_empty_for_doctype_page() -> None:
    html = '<!DOCTYPE html><html lang="en"><head><script>var x=1</script></head><body><p>news</p></body></html>'
    assert strip_html_page(html) == ""


def test_strip_html_page_returns_empty_for_html_tag_page() -> None:
    html = '<html lang="sv"><head><meta charset="utf-8"></head><body>text</body></html>'
    assert strip_html_page(html) == ""


def test_strip_html_page_passes_through_plain_text() -> None:
    text = "Oil prices rose after Iran announced new sanctions."
    assert strip_html_page(text) == text
