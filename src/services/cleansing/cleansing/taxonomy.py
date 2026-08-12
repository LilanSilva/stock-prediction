"""Local, version-controlled action -> canonical taxonomy mapping and asset inference.

This is Gate 2 of clustering and the deterministic classifier used before any LLM call. The
mapping is intentionally conservative: an unmapped action becomes OTHER (retaining the original
lemma for review) rather than being force-fit into a type. Over-merging distinct causal events is
worse than under-merging (functional document sec 1), so two OTHER clusters never merge.

Both English and Swedish keyword forms are included because articles are normalized to the
canonical taxonomy locally, never translated by an LLM (functional document sec 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from shared.reference import members_of, registry
from shared.schemas.messages import AssetId, ConditionCode, EventPolarity, EventType

# Keyword/lemma (lowercase) -> canonical event type. Swedish and English forms map to the same type.
ACTION_TAXONOMY: dict[str, EventType] = {
    # MILITARY_CONFLICT
    "attack": EventType.MILITARY_CONFLICT,
    "invade": EventType.MILITARY_CONFLICT,
    "invasion": EventType.MILITARY_CONFLICT,
    "strike": EventType.MILITARY_CONFLICT,
    "bombard": EventType.MILITARY_CONFLICT,
    "airstrike": EventType.MILITARY_CONFLICT,
    "war": EventType.MILITARY_CONFLICT,
    "conflict": EventType.MILITARY_CONFLICT,
    "anfall": EventType.MILITARY_CONFLICT,  # sv
    "attackera": EventType.MILITARY_CONFLICT,  # sv
    "invadera": EventType.MILITARY_CONFLICT,  # sv
    "krig": EventType.MILITARY_CONFLICT,       # sv: also matches kriget/krigens (suffix)
    "krigsplan": EventType.MILITARY_CONFLICT,  # sv: war plan
    "iranattack": EventType.MILITARY_CONFLICT, # sv: compound "Iran attack"
    "stridighet": EventType.MILITARY_CONFLICT, # sv: conflict/fighting
    # STRAIT_CLOSURE
    "close": EventType.STRAIT_CLOSURE,
    "closure": EventType.STRAIT_CLOSURE,
    "block": EventType.STRAIT_CLOSURE,
    "blockade": EventType.STRAIT_CLOSURE,
    "strait": EventType.STRAIT_CLOSURE,
    "hormuz": EventType.STRAIT_CLOSURE,      # en+sv: standalone "Hormuz"
    "hormuzsundet": EventType.STRAIT_CLOSURE,  # sv: compound "Hormuzsundet" (the Strait of Hormuz)
    "stänga": EventType.STRAIT_CLOSURE,      # sv
    "blockera": EventType.STRAIT_CLOSURE,    # sv
    "sund": EventType.STRAIT_CLOSURE,        # sv: strait/sound; suffix match catches "sundet"
    "öppna hormuz": EventType.STRAIT_CLOSURE,   # sv: open Hormuz (multi-word, avoids generic "öppna")
    "återöppna": EventType.STRAIT_CLOSURE,   # sv: reopen
    "rutt i hormuz": EventType.STRAIT_CLOSURE,  # sv: route through Hormuz
    # SUPPLY_DISRUPTION
    "halt": EventType.SUPPLY_DISRUPTION,
    "disrupt": EventType.SUPPLY_DISRUPTION,
    "disruption": EventType.SUPPLY_DISRUPTION,
    "pipeline": EventType.SUPPLY_DISRUPTION,
    "outage": EventType.SUPPLY_DISRUPTION,
    "shortage": EventType.SUPPLY_DISRUPTION,
    "cut output": EventType.SUPPLY_DISRUPTION,
    "avbrott": EventType.SUPPLY_DISRUPTION,  # sv
    "störning": EventType.SUPPLY_DISRUPTION,  # sv
    "brist": EventType.SUPPLY_DISRUPTION,  # sv
    # SANCTIONS
    "sanction": EventType.SANCTIONS,
    "sanctions": EventType.SANCTIONS,
    "embargo": EventType.SANCTIONS,
    "sanktion": EventType.SANCTIONS,  # sv
    "sanktioner": EventType.SANCTIONS,  # sv
    # RATE_DECISION
    "rate": EventType.RATE_DECISION,
    "rates": EventType.RATE_DECISION,
    "interest rate": EventType.RATE_DECISION,
    "hike": EventType.RATE_DECISION,
    "ränta": EventType.RATE_DECISION,  # sv
    "styrränta": EventType.RATE_DECISION,  # sv
    # INFLATION_CHANGE
    "inflation": EventType.INFLATION_CHANGE,
    "cpi": EventType.INFLATION_CHANGE,
    "deflation": EventType.INFLATION_CHANGE,
    # RECESSION_SIGNAL
    "recession": EventType.RECESSION_SIGNAL,
    "contraction": EventType.RECESSION_SIGNAL,
    "downturn": EventType.RECESSION_SIGNAL,
    "lågkonjunktur": EventType.RECESSION_SIGNAL,  # sv
    # CORPORATE_EARNINGS
    "earnings": EventType.CORPORATE_EARNINGS,
    "guidance": EventType.CORPORATE_EARNINGS,
    "profit": EventType.CORPORATE_EARNINGS,
    "revenue": EventType.CORPORATE_EARNINGS,
    "kvartalsresultat": EventType.CORPORATE_EARNINGS,  # sv: quarterly result (not generic "resultat")
    "rörelseresultat": EventType.CORPORATE_EARNINGS,   # sv: operating result
    # POLITICAL_TRANSITION
    "election": EventType.POLITICAL_TRANSITION,
    "resign": EventType.POLITICAL_TRANSITION,
    "resignation": EventType.POLITICAL_TRANSITION,
    "appoint": EventType.POLITICAL_TRANSITION,
    "val": EventType.POLITICAL_TRANSITION,  # sv
    "avgå": EventType.POLITICAL_TRANSITION,  # sv
    # NATURAL_DISASTER
    "earthquake": EventType.NATURAL_DISASTER,
    "flood": EventType.NATURAL_DISASTER,
    "wildfire": EventType.NATURAL_DISASTER,
    "hurricane": EventType.NATURAL_DISASTER,
    "jordbävning": EventType.NATURAL_DISASTER,  # sv
    "översvämning": EventType.NATURAL_DISASTER,  # sv
    # CORPORATE_ACQUISITION
    "merger": EventType.CORPORATE_ACQUISITION,
    "mergers": EventType.CORPORATE_ACQUISITION,
    "acquisition": EventType.CORPORATE_ACQUISITION,
    "acquire": EventType.CORPORATE_ACQUISITION,
    "takeover": EventType.CORPORATE_ACQUISITION,
    "buyout": EventType.CORPORATE_ACQUISITION,
    "bid for": EventType.CORPORATE_ACQUISITION,
    "fusion": EventType.CORPORATE_ACQUISITION,         # sv
    "jättefusion": EventType.CORPORATE_ACQUISITION, # sv: mega-merger compound
    "förvärv": EventType.CORPORATE_ACQUISITION,     # sv
    "uppköp": EventType.CORPORATE_ACQUISITION,      # sv
    "samgående": EventType.CORPORATE_ACQUISITION,   # sv
    "bud på": EventType.CORPORATE_ACQUISITION,      # sv
    # EXECUTIVE_CHANGE
    "ceo": EventType.EXECUTIVE_CHANGE,
    "cfo": EventType.EXECUTIVE_CHANGE,
    "coo": EventType.EXECUTIVE_CHANGE,
    "chief executive": EventType.EXECUTIVE_CHANGE,
    "steps down": EventType.EXECUTIVE_CHANGE,
    "step down": EventType.EXECUTIVE_CHANGE,
    "fired": EventType.EXECUTIVE_CHANGE,
    # An officer appointment is an EXECUTIVE_CHANGE, not a POLITICAL_TRANSITION. These phrases are
    # needed because classify_text takes the EARLIEST match: in "Board appoints new CFO" the generic
    # "appoint" (pos 6) would otherwise beat the specific "cfo" (pos 17).
    "appoints new ceo": EventType.EXECUTIVE_CHANGE,
    "appoints new cfo": EventType.EXECUTIVE_CHANGE,
    "appoints new coo": EventType.EXECUTIVE_CHANGE,
    "appoints new chief": EventType.EXECUTIVE_CHANGE,
    "vd avgår": EventType.EXECUTIVE_CHANGE,         # sv: CEO resigns
    "ny vd": EventType.EXECUTIVE_CHANGE,            # sv: new CEO
    "vd utsedd": EventType.EXECUTIVE_CHANGE,        # sv: CEO appointed
    "styrelseordförande": EventType.EXECUTIVE_CHANGE,  # sv: chairman
    # REGULATORY_ACTION
    "approved": EventType.REGULATORY_ACTION,
    "approval": EventType.REGULATORY_ACTION,
    "rejected": EventType.REGULATORY_ACTION,
    "rejection": EventType.REGULATORY_ACTION,
    "fda": EventType.REGULATORY_ACTION,
    "regulator": EventType.REGULATORY_ACTION,
    "licence": EventType.REGULATORY_ACTION,
    "license": EventType.REGULATORY_ACTION,
    "cleared": EventType.REGULATORY_ACTION,
    "fined": EventType.REGULATORY_ACTION,
    "penalty": EventType.REGULATORY_ACTION,
    "godkänd": EventType.REGULATORY_ACTION,         # sv: approved
    "godkännande": EventType.REGULATORY_ACTION,     # sv: approval
    "tillstånd": EventType.REGULATORY_ACTION,       # sv: licence/permit
    "böter": EventType.REGULATORY_ACTION,           # sv: fine
    # DEBT_CRISIS
    "bankruptcy": EventType.DEBT_CRISIS,
    "bankrupt": EventType.DEBT_CRISIS,
    "default": EventType.DEBT_CRISIS,
    "insolvent": EventType.DEBT_CRISIS,
    "insolvency": EventType.DEBT_CRISIS,
    "credit downgrade": EventType.DEBT_CRISIS,
    "konkurs": EventType.DEBT_CRISIS,               # sv
    "betalningsinställelse": EventType.DEBT_CRISIS, # sv: suspension of payments
    "kreditbetyg": EventType.DEBT_CRISIS,           # sv: credit rating
    # RESTRUCTURING
    "layoffs": EventType.RESTRUCTURING,
    "layoff": EventType.RESTRUCTURING,
    "redundancies": EventType.RESTRUCTURING,
    "job cuts": EventType.RESTRUCTURING,
    "cost cutting": EventType.RESTRUCTURING,
    "restructure": EventType.RESTRUCTURING,
    "restructuring": EventType.RESTRUCTURING,
    # "spin off" (not "spin-off"): _normalise turns punctuation into spaces, so a hyphenated key
    # could never match. Both "spin-off" and "spin off" normalise to this form.
    "spin off": EventType.RESTRUCTURING,
    "divestiture": EventType.RESTRUCTURING,
    "divest": EventType.RESTRUCTURING,
    "varsel": EventType.RESTRUCTURING,              # sv: redundancy notice
    "nedskärningar": EventType.RESTRUCTURING,       # sv: cutbacks
    "omstrukturering": EventType.RESTRUCTURING,     # sv
    "avknoppning": EventType.RESTRUCTURING,         # sv: spin-off
    # LEGAL_DISPUTE
    "lawsuit": EventType.LEGAL_DISPUTE,
    "sued": EventType.LEGAL_DISPUTE,
    "litigation": EventType.LEGAL_DISPUTE,
    "class action": EventType.LEGAL_DISPUTE,
    "fraud": EventType.LEGAL_DISPUTE,
    "investigation": EventType.LEGAL_DISPUTE,
    "settlement": EventType.LEGAL_DISPUTE,
    "stämning": EventType.LEGAL_DISPUTE,            # sv: lawsuit
    "utredning": EventType.LEGAL_DISPUTE,           # sv: investigation
    "uppgörelse": EventType.LEGAL_DISPUTE,          # sv: settlement
    "bedrägeri": EventType.LEGAL_DISPUTE,           # sv: fraud
    # PRODUCT_RECALL
    "recall": EventType.PRODUCT_RECALL,
    "recalls": EventType.PRODUCT_RECALL,
    "recalled": EventType.PRODUCT_RECALL,
    "withdrawn": EventType.PRODUCT_RECALL,
    "safety warning": EventType.PRODUCT_RECALL,
    "market ban": EventType.PRODUCT_RECALL,
    "återkallelse": EventType.PRODUCT_RECALL,       # sv
    "återkallar": EventType.PRODUCT_RECALL,         # sv
    "säkerhetsvarning": EventType.PRODUCT_RECALL,   # sv
    # DIVIDEND_CHANGE
    "dividend": EventType.DIVIDEND_CHANGE,
    "dividends": EventType.DIVIDEND_CHANGE,
    "payout": EventType.DIVIDEND_CHANGE,
    "utdelning": EventType.DIVIDEND_CHANGE,         # sv
    # CONTRACT_WIN
    "contract": EventType.CONTRACT_WIN,
    "deal signed": EventType.CONTRACT_WIN,
    "agreement signed": EventType.CONTRACT_WIN,
    "partnership": EventType.CONTRACT_WIN,
    "supply agreement": EventType.CONTRACT_WIN,
    "kontrakt": EventType.CONTRACT_WIN,             # sv
    "avtal": EventType.CONTRACT_WIN,                # sv: agreement/deal
    "partnerskap": EventType.CONTRACT_WIN,          # sv
    # SHARE_BUYBACK
    "buyback": EventType.SHARE_BUYBACK,
    "buy back": EventType.SHARE_BUYBACK,
    "share repurchase": EventType.SHARE_BUYBACK,
    "återköp": EventType.SHARE_BUYBACK,             # sv
    # IPO_LISTING
    "ipo": EventType.IPO_LISTING,
    "listing": EventType.IPO_LISTING,
    "stock market debut": EventType.IPO_LISTING,
    "börsnot": EventType.IPO_LISTING,               # sv: stock listing
    "börsnotering": EventType.IPO_LISTING,          # sv
    # CYBERSECURITY_INCIDENT
    "data breach": EventType.CYBERSECURITY_INCIDENT,
    "cyberattack": EventType.CYBERSECURITY_INCIDENT,
    "ransomware": EventType.CYBERSECURITY_INCIDENT,
    "hacked": EventType.CYBERSECURITY_INCIDENT,
    "hack": EventType.CYBERSECURITY_INCIDENT,
    "dataintrång": EventType.CYBERSECURITY_INCIDENT,  # sv
    "cyberangrepp": EventType.CYBERSECURITY_INCIDENT, # sv
    # TRADE_POLICY
    "tariff": EventType.TRADE_POLICY,
    "tariffs": EventType.TRADE_POLICY,
    "trade war": EventType.TRADE_POLICY,
    "trade deal": EventType.TRADE_POLICY,
    "import ban": EventType.TRADE_POLICY,
    "export ban": EventType.TRADE_POLICY,
    "tull": EventType.TRADE_POLICY,                 # sv: tariff/customs
    "handelskrig": EventType.TRADE_POLICY,          # sv: trade war
    "handelsavtal": EventType.TRADE_POLICY,         # sv: trade deal
    # FISCAL_POLICY
    "stimulus": EventType.FISCAL_POLICY,
    "budget": EventType.FISCAL_POLICY,
    "tax cut": EventType.FISCAL_POLICY,
    "tax hike": EventType.FISCAL_POLICY,
    "infrastructure spending": EventType.FISCAL_POLICY,
    "statsbudget": EventType.FISCAL_POLICY,         # sv: state budget
    "skattesänkning": EventType.FISCAL_POLICY,      # sv: tax cut
    "stimulans": EventType.FISCAL_POLICY,           # sv
    # CURRENCY_CRISIS
    "devaluation": EventType.CURRENCY_CRISIS,
    "devalued": EventType.CURRENCY_CRISIS,
    "currency collapse": EventType.CURRENCY_CRISIS,
    "exchange rate": EventType.CURRENCY_CRISIS,
    "valutakris": EventType.CURRENCY_CRISIS,        # sv: currency crisis
    "devalvering": EventType.CURRENCY_CRISIS,       # sv
    "växelkurs": EventType.CURRENCY_CRISIS,         # sv: exchange rate
    # SOVEREIGN_DEBT
    "sovereign debt": EventType.SOVEREIGN_DEBT,
    "imf bailout": EventType.SOVEREIGN_DEBT,
    "country default": EventType.SOVEREIGN_DEBT,
    "statsskuld": EventType.SOVEREIGN_DEBT,         # sv: national debt
    "statsobligation": EventType.SOVEREIGN_DEBT,    # sv: government bond
    # GEOPOLITICAL_TENSION
    "military exercises": EventType.GEOPOLITICAL_TENSION,
    "missile test": EventType.GEOPOLITICAL_TENSION,
    "nuclear threat": EventType.GEOPOLITICAL_TENSION,
    "tensions": EventType.GEOPOLITICAL_TENSION,
    "militärövning": EventType.GEOPOLITICAL_TENSION,  # sv
    "kärnvapenhot": EventType.GEOPOLITICAL_TENSION,   # sv: nuclear threat
    "spänningar": EventType.GEOPOLITICAL_TENSION,     # sv: tensions
    # COMMODITY_PRICE_SHOCK
    "opec": EventType.COMMODITY_PRICE_SHOCK,
    "oil production cut": EventType.COMMODITY_PRICE_SHOCK,
    "commodity price": EventType.COMMODITY_PRICE_SHOCK,
    "grain price": EventType.COMMODITY_PRICE_SHOCK,
    "gold price": EventType.COMMODITY_PRICE_SHOCK,
    "oil price": EventType.COMMODITY_PRICE_SHOCK,
    "gold": EventType.COMMODITY_PRICE_SHOCK,        # en: standalone "gold" (buy gold, gold rises)
    "råvarupris": EventType.COMMODITY_PRICE_SHOCK,  # sv: commodity price
    "oljepris": EventType.COMMODITY_PRICE_SHOCK,    # sv: oil price; suffix match → oljepriset
    "guldpris": EventType.COMMODITY_PRICE_SHOCK,    # sv: gold price; suffix match → guldpriset
    # ECONOMIC_DATA_RELEASE
    "gdp": EventType.ECONOMIC_DATA_RELEASE,
    "jobs report": EventType.ECONOMIC_DATA_RELEASE,
    "unemployment": EventType.ECONOMIC_DATA_RELEASE,
    "pmi": EventType.ECONOMIC_DATA_RELEASE,
    "retail sales": EventType.ECONOMIC_DATA_RELEASE,
    "bnp": EventType.ECONOMIC_DATA_RELEASE,         # sv: GDP
    "arbetslöshet": EventType.ECONOMIC_DATA_RELEASE,  # sv: unemployment
    "inköpschefsindex": EventType.ECONOMIC_DATA_RELEASE,  # sv: PMI
    # PANDEMIC_OUTBREAK
    "pandemic": EventType.PANDEMIC_OUTBREAK,
    "epidemic": EventType.PANDEMIC_OUTBREAK,
    "outbreak": EventType.PANDEMIC_OUTBREAK,
    "lockdown": EventType.PANDEMIC_OUTBREAK,
    "pandemi": EventType.PANDEMIC_OUTBREAK,         # sv
    "utbrott": EventType.PANDEMIC_OUTBREAK,         # sv: outbreak
    "nedstängning": EventType.PANDEMIC_OUTBREAK,    # sv: lockdown
    # ENERGY_POLICY
    "carbon tax": EventType.ENERGY_POLICY,
    "green deal": EventType.ENERGY_POLICY,
    "renewable energy": EventType.ENERGY_POLICY,
    "nuclear power": EventType.ENERGY_POLICY,
    "combustion ban": EventType.ENERGY_POLICY,
    "koldioxidskatt": EventType.ENERGY_POLICY,      # sv: carbon tax
    "kärnkraft": EventType.ENERGY_POLICY,           # sv: nuclear power
    "förnybar energi": EventType.ENERGY_POLICY,     # sv: renewable energy
}

def _asset_keywords() -> dict[AssetId, tuple[str, ...]]:
    """Per-asset inference keywords, read from the JSON registry rather than hardcoded here.

    Keeping these in the registry beside each asset's provider mapping is what lets a company be
    added by editing ``assets.json`` alone. The registry rejects a keyword claimed by two assets, so
    a match is never ambiguous.
    """
    return {
        AssetId(entry.asset_id): entry.keywords
        for entry in registry().assets.values()
        if entry.keywords
    }


def _industry_keywords() -> dict[str, tuple[str, ...]]:
    """Per-group keywords: an industry-scope match fans out to every member of the group."""
    return {
        group.group_id: group.industry_keywords
        for group in registry().groups.values()
        if group.industry_keywords
    }


# Resolved once at import, mirroring the registry's own load-once semantics.
ASSET_KEYWORDS: dict[AssetId, tuple[str, ...]] = _asset_keywords()
INDUSTRY_KEYWORDS: dict[str, tuple[str, ...]] = _industry_keywords()


def map_action(lemma: str | None) -> EventType:
    """Map a single action lemma to the canonical taxonomy, or OTHER when unmapped."""
    if not lemma:
        return EventType.OTHER
    return ACTION_TAXONOMY.get(lemma.strip().lower(), EventType.OTHER)


def classify_text(text: str) -> tuple[EventType, str | None]:
    """Deterministically classify free text by scanning for the earliest taxonomy keyword.

    Returns the mapped event type and the matched keyword (the "action" evidence). Multi-word keys
    are checked so phrases like "interest rate" win over the bare token. Single-token keys use
    ``_keyword_present`` so Swedish definite/inflected forms (e.g. "kriget", "oljepriset") and
    English plurals are matched without enumerating every form. Returns (OTHER, None) when nothing
    matches.

    The haystack is punctuation-normalised before matching so symbols attached to words
    (e.g. "opec+" or "anfall:") do not defeat word-boundary detection.
    """
    haystack = f" {_normalise(text.lower())} "
    best_type = EventType.OTHER
    best_keyword: str | None = None
    best_pos = len(haystack) + 1
    best_len = 0
    for keyword, event_type in ACTION_TAXONOMY.items():
        if " " in keyword:
            # Multi-word phrase: find position for earliest-match tie-breaking.
            pos = haystack.find(keyword)
            # On a tie the LONGER keyword wins: both "appoint" and "appoints new cfo" start at the
            # same word, and the more specific phrase is the better classification.
            if pos != -1 and (pos < best_pos or (pos == best_pos and len(keyword) > best_len)):
                best_pos = pos
                best_len = len(keyword)
                best_type = event_type
                best_keyword = keyword
        else:
            # Single token: use suffix-tolerant match; position is the bare-token index.
            # `find(" keyword")` returns the index of the leading SPACE, so +1 gives the index of
            # the word itself — the same basis the phrase branch uses. Without this a single token
            # always appeared one character earlier than a phrase starting at the same word, so a
            # generic token beat a more specific phrase ("appoint" over "appoints new cfo").
            if _keyword_present(haystack, keyword):
                pos = haystack.find(f" {keyword}") + 1
                if pos < best_pos or (pos == best_pos and len(keyword) > best_len):
                    best_pos = pos
                    best_len = len(keyword)
                    best_type = event_type
                    best_keyword = keyword
    return best_type, best_keyword


class NewsScope(StrEnum):
    """How broadly a headline applies, which decides how many assets it moves."""

    COMPANY = "COMPANY"
    INDUSTRY = "INDUSTRY"
    EVENT_TYPE = "EVENT_TYPE"
    NONE = "NONE"


@dataclass(frozen=True)
class AssetScope:
    """The resolved assets for one article plus the scope that produced them.

    ``matched`` records the keyword(s) that fired, so a prediction's provenance can explain why an
    asset was selected.
    """

    scope: NewsScope
    assets: tuple[AssetId, ...]
    matched: tuple[str, ...] = ()


def infer_assets(text: str) -> tuple[AssetId, ...]:
    """Return the assets named *specifically* by the text (company scope only).

    Kept as the narrow, company-level lookup; use :func:`resolve_scope` for the full precedence
    including industry fan-out.
    """
    return _company_matches(f" {text.lower()} ")[0]


def _normalise(text: str) -> str:
    """Replace non-alphanumeric, non-space characters with spaces for word-boundary matching.

    This lets "opec+" match the "opec" keyword and "anfall:" match "anfall". Swedish letters
    (åäö) are preserved because they are part of valid keywords.
    """
    import re
    return re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)


def _keyword_present(haystack: str, keyword: str) -> bool:
    """Keyword match with English/Swedish suffix tolerance.

    Haystack is expected to already be lowercased and punctuation-normalised (see
    ``classify_text``). Multi-word phrases match as substrings. A single token matches as a whole
    word and also with a trailing suffix so "missiles" fires "missile" and "kriget" fires "krig"
    (Swedish definite form). Suffixes: English plurals (s/es/er) and Swedish inflections (et/en/ar/ing).
    """
    if " " in keyword:
        return keyword in haystack
    return any(
        f" {keyword}{suffix} " in haystack
        for suffix in ("", "s", "es", "er", "ar", "et", "en", "ing")
    )


def _company_matches(haystack: str) -> tuple[tuple[AssetId, ...], tuple[str, ...]]:
    """Assets whose own keywords appear in ``haystack`` (pre-lowercased and space-padded)."""
    found: list[AssetId] = []
    matched: list[str] = []
    for asset, keywords in ASSET_KEYWORDS.items():
        hits = [kw for kw in keywords if _keyword_present(haystack, kw)]
        if hits:
            found.append(asset)
            matched.extend(hits)
    return tuple(found), tuple(matched)


def _industry_matches(haystack: str) -> tuple[tuple[AssetId, ...], tuple[str, ...]]:
    """Every member of each group whose industry keywords appear in ``haystack``.

    This is the fan-out: an industry-wide event moves every listing in that industry, across
    markets.
    """
    found: list[AssetId] = []
    matched: list[str] = []
    for group_id, keywords in INDUSTRY_KEYWORDS.items():
        hits = [kw for kw in keywords if _keyword_present(haystack, kw)]
        if not hits:
            continue
        matched.extend(hits)
        for member in members_of(group_id):
            asset = AssetId(member)
            if asset not in found:
                found.append(asset)
    return tuple(found), tuple(matched)


def resolve_scope(text: str, event_type: EventType) -> AssetScope:
    """Resolve which assets an article affects, most specific match first.

    Precedence, first match wins:

      1. **Company** -- a company's own keyword appears ("Tesla acquired" moves Tesla alone). The
         most specific signal available, so it is never widened to the whole industry.
      2. **Industry** -- a group keyword appears ("war begins" moves every weapons maker in every
         market). Fans out to all members of the group.
      3. **Event type** -- neither named an asset, so fall back to the event type's graph assets, so
         a geopolitical headline still reaches the commodities it moves.

    Returns an empty ``NONE`` scope when nothing resolves; the caller decides whether that is worth
    recording. Never raises.
    """
    haystack = f" {text.lower()} "

    assets, matched = _company_matches(haystack)
    if assets:
        return AssetScope(NewsScope.COMPANY, assets, matched)

    assets, matched = _industry_matches(haystack)
    if assets:
        return AssetScope(NewsScope.INDUSTRY, assets, matched)

    fallback = assets_for_event_type(event_type)
    if fallback:
        return AssetScope(NewsScope.EVENT_TYPE, fallback, (event_type.value,))

    return AssetScope(NewsScope.NONE, ())


def gate2_compatible(left: EventType, right: EventType) -> bool:
    """Gate 2: two articles may share a cluster only if their canonical types are the same and
    known. OTHER is never compatible with anything (including another OTHER), because unknown
    events must not be silently merged."""
    return left == right and left != EventType.OTHER


# Downstream assets implied by each canonical event type, mirroring the seeded Neo4j CAUSES edges
# (infra/neo4j/init/04+05). Used only as a fallback when neither a company nor an industry keyword
# named an asset, so a geopolitical headline like "USA calls off Iran attack" still resolves.
#
# The registry no longer carries the GOLD and BRENT_OIL commodity instruments; equity proxies stand
# in for that macro exposure. NEM_NYSE (Newmont, PRECIOUS_METALS) is the gold proxy and XOM_NYSE
# (Exxon, OIL_GAS) the oil proxy, so these entries keep resolving to a real, priceable asset.
EVENT_TYPE_ASSETS: dict[EventType, tuple[AssetId, ...]] = {
    EventType.MILITARY_CONFLICT: (AssetId.NEM_NYSE, AssetId.XOM_NYSE),
    EventType.STRAIT_CLOSURE: (AssetId.NEM_NYSE, AssetId.XOM_NYSE),
    EventType.SUPPLY_DISRUPTION: (AssetId.XOM_NYSE,),
    EventType.SANCTIONS: (AssetId.NEM_NYSE, AssetId.XOM_NYSE),
    EventType.RATE_DECISION: (AssetId.NEM_NYSE, AssetId.XOM_NYSE),
    EventType.INFLATION_CHANGE: (AssetId.NEM_NYSE,),
    EventType.RECESSION_SIGNAL: (AssetId.NEM_NYSE, AssetId.XOM_NYSE),
    EventType.NATURAL_DISASTER: (AssetId.NEM_NYSE, AssetId.XOM_NYSE),
    EventType.POLITICAL_TRANSITION: (AssetId.NEM_NYSE,),
}

# Industry groups an event type moves in addition to the commodities above. This is what lets an
# industry-wide event reach the listings it affects even when the headline names no company and no
# industry keyword: a war moves weapons makers everywhere, not just gold and oil. Members are
# expanded from the registry, so a new market listing is picked up without editing this table.
EVENT_TYPE_GROUPS: dict[EventType, tuple[str, ...]] = {
    EventType.MILITARY_CONFLICT: ("WEAPON_INDUSTRY",),
    EventType.SANCTIONS: ("WEAPON_INDUSTRY",),
    EventType.STRAIT_CLOSURE: ("OIL_GAS",),
    EventType.SUPPLY_DISRUPTION: ("OIL_GAS",),
}

# Resolution/negation cues (lowercase). Presence of any flips event polarity to RESOLUTION, which
# inverts the causal sign at decision time (e.g. a planned strike called off pushes oil DOWN).
# English and Swedish forms are listed because articles are never translated by an LLM.
RESOLUTION_CUES: tuple[str, ...] = (
    "calls off",
    "call off",
    "called off",
    "calling off",
    "cancel",
    "cancels",
    "cancelled",
    "canceled",
    "avert",
    "averts",
    "averted",
    "ceasefire",
    "truce",
    "de-escalate",
    "de-escalation",
    "agreement",
    "deal",
    "resolved",
    "avbryter",  # sv: calls off / cancels
    "ställer in",  # sv: calls off
    "blåser av",  # sv: calls off
    "drar tillbaka",  # sv: withdraws
    "vapenvila",  # sv: ceasefire
    "eldupphör",  # sv: ceasefire (lit. fire stop)
    "eld upphör",  # sv: ceasefire variant
    "fredsavtal",  # sv: peace agreement (not bare "fred" — too generic: "fred" means peace but appears in names)
    "pausa anfall",  # sv: pause attack / stand down
    "ger andrum",  # sv: gives respite / stands down
)

# Transport/supply cues (lowercase) that qualify a factor as physically threatening oil logistics,
# selecting the TRANSPORT_AFFECTED conditioned edge over the SAFE_HAVEN_ONLY one.
TRANSPORT_CUES: tuple[str, ...] = (
    "strait",
    "hormuz",   # also catches "hormuzsundet" via punctuation normalisation splitting compound
    "shipping",
    "tanker",
    "pipeline",
    "refinery",
    "blockade",
    "port",
    "export terminal",
    "oil route",
    "sjöfart",  # sv: shipping
)

# Event types whose geopolitical nature warrants a SAFE_HAVEN_ONLY tag when no transport cue is
# present (a distant conflict/sanction/transition is a safe-haven bid for gold, not an oil shock).
GEOPOLITICAL_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.MILITARY_CONFLICT,
        EventType.SANCTIONS,
        EventType.POLITICAL_TRANSITION,
    }
)


def _cue_present(haystack: str, cue: str) -> bool:
    """Whole-word match for single tokens; substring match for multi-word phrases.

    Expects a punctuation-normalised, space-padded, lowercased haystack.
    """
    needle = cue if " " in cue else f" {cue} "
    return needle in haystack


def classify_polarity(text: str) -> EventPolarity:
    """Return RESOLUTION when a de-escalation/negation cue is present, else OCCURRENCE."""
    haystack = f" {_normalise(text.lower())} "
    for cue in RESOLUTION_CUES:
        if _cue_present(haystack, cue):
            return EventPolarity.RESOLUTION
    return EventPolarity.OCCURRENCE


def infer_conditions(text: str, event_type: EventType) -> list[ConditionCode]:
    """Deterministically infer context tags gating which conditioned causal edge fires.

    A transport/supply cue yields TRANSPORT_AFFECTED. Otherwise a geopolitical event type yields
    SAFE_HAVEN_ONLY. RISK_PREMIUM_ELEVATED is never inferred here; it is price-derived and added
    later by Prediction.
    """
    haystack = f" {_normalise(text.lower())} "
    if any(_cue_present(haystack, cue) for cue in TRANSPORT_CUES):
        return [ConditionCode.TRANSPORT_AFFECTED]
    if event_type in GEOPOLITICAL_EVENT_TYPES:
        return [ConditionCode.SAFE_HAVEN_ONLY]
    return []


def assets_for_event_type(event_type: EventType) -> tuple[AssetId, ...]:
    """Return the downstream assets a canonical event type maps to (empty when it has no edge).

    Combines the commodity mapping with the members of any industry group the event type moves, so a
    war headline naming no company still reaches weapons makers in every market.
    """
    found: list[AssetId] = list(EVENT_TYPE_ASSETS.get(event_type, ()))
    for group_id in EVENT_TYPE_GROUPS.get(event_type, ()):
        for member in members_of(group_id):
            asset = AssetId(member)
            if asset not in found:
                found.append(asset)
    return tuple(found)
