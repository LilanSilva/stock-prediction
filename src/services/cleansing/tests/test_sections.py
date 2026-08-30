"""Publisher-section parsing (CLN-71, CLN-72).

The fail-open cases are requirements, not implementation detail: a malformed URL must classify exactly
as if none were supplied, so the tests below would fail if someone added a ``raise``.
"""

from __future__ import annotations

import pytest
from shared.schemas.messages import EventType

from cleansing.sections import mapped_section_types, section_event_type


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        # Universal section names, across publishers and languages.
        ("https://www.dn.se/sport/aik-starkast-i-derbyt", EventType.SPORT),
        ("https://www.theguardian.com/sport/2026/aug/17/root-fleming", EventType.SPORT),
        ("https://www.newsday.com/sports/high-school/aggies-hornets", EventType.SPORT),
        ("https://www.theguardian.com/film/2026/aug/17/skintown-review", EventType.ENTERTAINMENT),
        ("https://www.radiotimes.com/tv/soap/mollie-gallagher-exit", EventType.ENTERTAINMENT),
        # Publisher-specific: "kultur" is DN's own name for its culture desk.
        ("https://www.dn.se/kultur/lorde-tar-ett-bloss-med-fansen", EventType.ENTERTAINMENT),
    ],
)
def test_mapped_sections(url: str, expected: EventType) -> None:
    assert section_event_type(url) is expected


@pytest.mark.parametrize(
    "url",
    [
        # General news and comment — no non-financial implication.
        "https://www.dn.se/sverige/stor-brist-pa-meteorologer",
        "https://www.dn.se/varlden/vita-huset-trump-skamtade-om-hormuzsundet",
        "https://www.dn.se/ekonomi/nagot-om-borsen",
        # Deliberately excluded: mixes consumer features with industry news.
        "https://www.dn.se/motor/eldrivna-lastbilar-lonsamma",
        # Deliberately excluded: reader opinion on any subject.
        "https://www.dn.se/insandare/facktoppar-bor-avga-efter-forlorad-kamp-mot-tesla",
        # A financial wire; its top-level paths are formats, not subjects.
        "https://www.di.se/live/sagax-miljardshoppar-i-europa",
        "https://www.di.se/analys/nya-toppduon-vander-kriskedjan",
        "https://www.di.se/ditv/borsmorgon/live-borsmorgon-17-augusti-2026",
        # No section exposed at all.
        "https://www.svd.se/a/K8aRbe/trump-hotar-att-bomba-oman",
        "https://www.aftonbladet.se/nyheter/a/6qVM2r/dystert-ebolarekord",
        "https://tv.aftonbladet.se/video/403898/kompanichefen",
        # Host alone, no path.
        "https://www.dn.se",
        "https://www.dn.se/",
    ],
)
def test_unmapped_urls_yield_no_signal(url: str) -> None:
    assert section_event_type(url) is None


@pytest.mark.parametrize(
    "url",
    [None, "", "   ", "not-a-url", "://", "http://", "/sport/relative-path", "https://"],
)
def test_fails_open_on_unusable_input(url: str | None) -> None:
    """CLN-72: a URL problem must never raise and must never invent a classification."""
    assert section_event_type(url) is None


def test_section_matching_is_case_insensitive_and_ignores_query_and_fragment() -> None:
    assert section_event_type("https://WWW.DN.SE/Sport/nagot") is EventType.SPORT
    assert section_event_type("https://www.dn.se/sport/nagot?utm=x#top") is EventType.SPORT
    assert section_event_type("https://www.dn.se:443/kultur/nagot") is EventType.ENTERTAINMENT


def test_only_non_financial_types_can_be_returned() -> None:
    """CLN-71: a section may reject an article but must never create a market classification.

    Without this, adding a plausible-looking entry such as ("www.di.se", "bors"): RATE_DECISION would
    let a URL path manufacture a prediction ahead of every evidence tier.
    """
    assert mapped_section_types() <= {
        EventType.SPORT,
        EventType.ENTERTAINMENT,
        EventType.LIFESTYLE,
    }


def test_a_relative_path_is_not_read_as_a_section() -> None:
    # "/sport/x" has no host. Treating its first segment as a section would let a stored relative URL
    # reject an article on the strength of a path the publisher never served.
    assert section_event_type("/sport/x") is None
