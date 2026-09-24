from __future__ import annotations

import pytest
from shared.text import assess_text, clean_text


@pytest.mark.parametrize(
    "text",
    [
        "ÅÄÖ åäö é £ € −5%",
        "العربية فارسی می\u200cروم",
        "हिन्दी क्\u200dष",
        "සිංහල தமிழ்",
        "日本語 中文 한국어",
        "Українська Ελληνικά",
        "👩\u200d💻 earnings",
        "Revenue < 5% and profit > €20",
    ],
)
def test_valid_multilingual_text_is_not_corruption(text: str) -> None:
    assert clean_text(text) == text
    assert assess_text(text).usable == text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("fo\u0308retag", "företag"),
        ("fÃ¶retag", "företag"),
        ("<p>Räntan&nbsp;höjs &#x20; 2%</p><script>bad</script>", "Räntan höjs 2%"),
        ("&lt;b&gt;Oil &amp;amp; gas&lt;/b&gt;", "Oil & gas"),
        ("\ufeffAlpha\x00Beta\t€3", "Alpha Beta €3"),
    ],
)
def test_cleanup_is_idempotent(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected
    assert clean_text(expected) == expected


def test_quality_excludes_damage_and_boilerplate_without_guessing() -> None:
    result = assess_text("Broken f�retag. Accept all cookies. Profit rose 3.5%. Profit rose 3.5%.")
    assert result.usable == "Profit rose 3.5%."
    assert result.status == "partial"
    assert set(result.reasons) == {"unrecoverable_encoding", "boilerplate", "repetition"}
    assert "�" in result.clean
    assert assess_text("Broken � title", title=True).usable == ""
    assert assess_text("<!DOCTYPE html><html>Page</html>").usable == ""
