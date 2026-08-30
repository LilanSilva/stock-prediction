"""Publisher-section signal derived from an article's canonical URL.

Most news sites file an article under a section, and that section appears in the URL path. It is an
editor's filing decision rather than an inference from text, which makes it the most reliable
non-financial signal available: on the 2026-08-17 corpus the DN sport section was correct 26 times out
of 26, and its culture section contained no market event in 28 articles, while the keyword reject tier
fired twice in 251 articles.

The signal is consulted ahead of every keyword tier (CLN-71) and can only ever select a non-financial
type — it must never create a market classification. It is skipped when the headline names a registered
company, and it fails open when a URL carries no usable section (CLN-72).

Two layers, because the sources differ in kind:

  * ``_UNIVERSAL_SECTIONS`` — path segments that mean the same thing on essentially any news site.
    Needed because one source is an aggregator: its articles carry the URLs of a dozen or more
    publishers (Newsday, The Guardian, The Hollywood Reporter, ABS-CBN …), so a host-keyed map could
    never be complete.
  * ``_PUBLISHER_SECTIONS`` — keyed on ``(host, section)`` for names that are specific to one
    publisher's language or taxonomy.

This module deliberately knows nothing about article text. Keeping URL parsing separate from keyword
matching is what lets each be tested on its own.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from shared.schemas.messages import EventType

# Section names that carry the same meaning across publishers and languages.
#
# These are safe as a reject signal because a financial publisher does not file market news under
# "sport". The residual risk is "tv", which on some sites means video content rather than television
# as a subject; the company gate in ``classify_text`` covers the case that matters, since a headline
# naming a listed company skips this signal entirely.
_UNIVERSAL_SECTIONS: dict[str, EventType] = {
    "sport": EventType.SPORT,
    "sports": EventType.SPORT,
    "cricket-betting": EventType.SPORT,
    "film": EventType.ENTERTAINMENT,
    "tv": EventType.ENTERTAINMENT,
}

# Publisher-specific section names. Kept narrow on purpose: only sections measured against a labelled
# corpus belong here.
_PUBLISHER_SECTIONS: dict[tuple[str, str], EventType] = {
    # 28 of 28 articles non-financial on 2026-08-17 — reviews, columns, essays, celebrity deaths.
    ("www.dn.se", "kultur"): EventType.ENTERTAINMENT,
}

# Sections deliberately NOT mapped. Recorded so a later reader can see these were decisions rather
# than oversights, and does not have to rediscover the measurement:
#
#   dn/motor      Mixes consumer car features with industry news. "Eldrivna lastbilar lönsamma — men
#                 förlustaffär i Sverige" is a Scania/Volvo story that names no registered company, so
#                 the company gate would not rescue it. This section alone would have caused a
#                 material regression.
#   dn/insandare  Reader opinion on any subject, market or not.
#   dn/podd       One article on the audit day; no evidence either way.
#   dn/sverige, dn/varlden, dn/ekonomi, dn/ledare, dn/debatt
#                 General news and comment — no non-financial implication.
#   di/*          A financial wire. Its top-level paths ("live", "analys", "nyheter") are formats, not
#                 subjects.
#   svd/a, aftonbladet/nyheter
#                 No section is exposed at all: svd uses /a/<id>/<slug> and aftonbladet files almost
#                 everything under /nyheter/.


def section_event_type(url: str | None) -> EventType | None:
    """Return the non-financial type the publisher's section implies, or ``None``.

    ``None`` means "no usable signal" and the caller must classify as if no URL had been supplied.
    This function never raises: a URL problem must not be able to fail an article (CLN-72).
    """
    if not url:
        return None
    try:
        parts = urlsplit(url)
        host = parts.netloc.lower().split(":")[0]
        segments = [segment for segment in parts.path.split("/") if segment]
    except ValueError:
        # urlsplit raises on a handful of malformed inputs (e.g. a bad IPv6 literal). Fail open.
        return None
    # A host is required. Without this check a relative path such as "/sport/x" would be read as a
    # section, letting a stored relative URL reject an article on a path no publisher ever served.
    if not host or not segments:
        return None

    section = segments[0].lower()
    publisher_match = _PUBLISHER_SECTIONS.get((host, section))
    if publisher_match is not None:
        return publisher_match
    return _UNIVERSAL_SECTIONS.get(section)


def mapped_section_types() -> frozenset[EventType]:
    """Every type this module can return. Used to assert the reject-only invariant (CLN-71)."""
    return frozenset(_UNIVERSAL_SECTIONS.values()) | frozenset(_PUBLISHER_SECTIONS.values())
