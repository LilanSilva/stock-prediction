"""Language-preserving news cleanup. Corrupt text is excluded, never guessed from missing bytes."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser

from ftfy import fix_encoding
from ftfy.badness import is_bad

NORMALIZER_VERSION = "news-text-v1"
_PAGE = re.compile(r"<!doctype\s+html|<html(?:\s|>)", re.I)
_TAG = re.compile(r"</?[a-zA-Z][\w:-]*(?:\s[^<>]*|\s*/?)>")
_BOILERPLATE = re.compile(
    r"accept all cookies|cookie (?:policy|settings|consent)|enable javascript|"
    r"subscribe to (?:read|continue)|all rights reserved|godkänn alla kakor|"
    r"prenumerera för att läsa",
    re.I,
)


class _FragmentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe", "head", "noscript"}:
            self.hidden.append(tag)
        if tag in {"p", "div", "br", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden and tag == self.hidden[-1]:
            self.hidden.pop()
        if tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def clean_text(value: str) -> str:
    """Repair known mojibake/HTML fragments, preserving valid scripts, joiners and symbols.

    Full pages are not article text. Repeated decoding is bounded and idempotent; replacement
    characters remain visible so the quality gate can reject the damaged sentence.
    """
    text = value
    for _ in range(8):
        previous = text
        text = fix_encoding(text)
        if _PAGE.search(text[:2048]):
            return ""
        if _TAG.search(text):
            parser = _FragmentParser()
            parser.feed(text)
            parser.close()
            text = "".join(parser.parts)
        text = html.unescape(text)
        if text == previous:
            break
    else:
        return ""
    text = unicodedata.normalize("NFC", text).lstrip("\ufeff")
    text = "".join(
        " " if unicodedata.category(c) == "Cc" and c not in "\n\r\t" else c for c in text
    )
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[^\S\n]+", " ", text)
    return re.sub(r" *\n+ *", "\n", text).strip()


def sentences(text: str) -> list[str]:
    """Keep decimal values intact and recognise CJK punctuation and paragraph boundaries."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|(?<=[。！？])\s*|\n+", text) if s.strip()]


@dataclass(frozen=True)
class TextQuality:
    clean: str
    usable: str
    status: str
    reasons: tuple[str, ...]


def assess_text(value: str, *, title: bool = False) -> TextQuality:
    clean = clean_text(value)
    if not clean:
        return TextQuality(clean, "", "rejected", ("empty_or_html_page",))
    accepted: list[str] = []
    reasons: set[str] = set()
    seen: set[str] = set()
    for span in [clean] if title else sentences(clean):
        if "\ufffd" in span or any(unicodedata.category(c) == "Cs" for c in span):
            reasons.add("unrecoverable_encoding")
        elif is_bad(span):
            reasons.add("suspected_mojibake")
        elif re.search(r"<[/!]?[a-zA-Z][^>]*>|function\s*\(|var\s+\w+\s*=", span):
            reasons.add("markup_or_script_residue")
        elif _BOILERPLATE.search(span):
            reasons.add("boilerplate")
        elif not any(c.isalpha() for c in span):
            reasons.add("no_linguistic_content")
        elif span.casefold() in seen:
            reasons.add("repetition")
        else:
            accepted.append(span)
            seen.add(span.casefold())
    usable = "\n".join(accepted)
    return TextQuality(
        clean,
        usable,
        "rejected" if not usable else "partial" if reasons else "usable",
        tuple(sorted(reasons)),
    )
