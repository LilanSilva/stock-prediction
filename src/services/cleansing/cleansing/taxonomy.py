"""Local, version-controlled action -> canonical taxonomy mapping and asset inference.

This is Gate 2 of clustering and the deterministic classifier used before any LLM call. The
mapping is intentionally conservative: an unmapped action becomes OTHER (retaining the original
lemma for review) rather than being force-fit into a type. Over-merging distinct causal events is
worse than under-merging (functional document sec 1), so two OTHER clusters never merge.

Both English and Swedish keyword forms are included because articles are normalized to the
canonical taxonomy locally, never translated by an LLM (functional document sec 5).
"""

from __future__ import annotations

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
    "anfall": EventType.MILITARY_CONFLICT,  # sv
    "attackera": EventType.MILITARY_CONFLICT,  # sv
    "invadera": EventType.MILITARY_CONFLICT,  # sv
    "krig": EventType.MILITARY_CONFLICT,  # sv
    # STRAIT_CLOSURE
    "close": EventType.STRAIT_CLOSURE,
    "closure": EventType.STRAIT_CLOSURE,
    "block": EventType.STRAIT_CLOSURE,
    "blockade": EventType.STRAIT_CLOSURE,
    "strait": EventType.STRAIT_CLOSURE,
    "stänga": EventType.STRAIT_CLOSURE,  # sv
    "blockera": EventType.STRAIT_CLOSURE,  # sv
    "sund": EventType.STRAIT_CLOSURE,  # sv
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
    "resultat": EventType.CORPORATE_EARNINGS,  # sv
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
}

# Canonical asset inference keywords (lowercase). Commodities plus industry-sector bellwethers.
# Keywords are distinctive whole words/phrases to avoid false positives (e.g. "artificial
# intelligence" not the bare token "ai").
ASSET_KEYWORDS: dict[AssetId, tuple[str, ...]] = {
    AssetId.GOLD: ("gold", "bullion", "guld", "xau"),
    AssetId.BRENT_OIL: (
        "oil",
        "crude",
        "brent",
        "opec",
        "petroleum",
        "olja",
        "råolja",
    ),
    AssetId.PHARMA: ("pharmaceutical", "pharma", "medicine", "drugmaker", "insulin", "vaccine"),
    AssetId.DEFENSE_AEROSPACE: (
        "defense",
        "defence",
        "lockheed",
        "fighter jet",
        "aircraft",
        "aerospace",
        "missile",
    ),
    AssetId.AI_COMPUTE: ("artificial intelligence", "nvidia", "gpu", "machine learning"),
    AssetId.SEMICONDUCTOR: ("semiconductor", "chipmaker", "microchip", "foundry", "wafer", "cpu"),
    AssetId.SOFTWARE: ("microsoft", "windows", "operating system", "azure"),
    AssetId.ENTERPRISE_SOFTWARE: ("oracle", "erp", "enterprise software"),
    AssetId.INTERNET_SEARCH: ("google", "alphabet", "search engine", "android"),
    AssetId.CONSUMER_ELECTRONICS: ("apple", "iphone", "smartphone"),
    AssetId.BANKING: ("bank", "jpmorgan", "banking", "lender"),
    AssetId.PAYMENTS_FINANCE: ("visa", "mastercard", "payments network", "asset manager"),
    AssetId.AUTOMOTIVE: ("automaker", "carmaker", "electric vehicle", "automobile"),
    AssetId.FOOD_BEVERAGE: ("coca-cola", "beverage", "packaged food", "soft drink"),
    AssetId.REAL_ESTATE: ("real estate", "housing", "homebuilder", "property market"),
    AssetId.INDUSTRIAL: ("industrial manufacturer", "factory output", "machinery maker"),
    AssetId.APPAREL: ("apparel", "clothing", "sportswear", "footwear"),
}


def map_action(lemma: str | None) -> EventType:
    """Map a single action lemma to the canonical taxonomy, or OTHER when unmapped."""
    if not lemma:
        return EventType.OTHER
    return ACTION_TAXONOMY.get(lemma.strip().lower(), EventType.OTHER)


def classify_text(text: str) -> tuple[EventType, str | None]:
    """Deterministically classify free text by scanning for the earliest taxonomy keyword.

    Returns the mapped event type and the matched keyword (the "action" evidence). Multi-word keys
    are checked so phrases like "interest rate" win over the bare token. Returns (OTHER, None) when
    nothing matches.
    """
    haystack = f" {text.lower()} "
    best_type = EventType.OTHER
    best_keyword: str | None = None
    best_pos = len(haystack) + 1
    for keyword, event_type in ACTION_TAXONOMY.items():
        # Whole-word match for single tokens; substring match for multi-word phrases.
        needle = keyword if " " in keyword else f" {keyword} "
        pos = haystack.find(needle)
        if pos != -1 and pos < best_pos:
            best_pos = pos
            best_type = event_type
            best_keyword = keyword
    return best_type, best_keyword


def infer_assets(text: str) -> tuple[AssetId, ...]:
    """Return the canonical assets referenced by the text, in canonical enum order."""
    lowered = f" {text.lower()} "
    found: list[AssetId] = []
    for asset, keywords in ASSET_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            found.append(asset)
    return tuple(found)


def gate2_compatible(left: EventType, right: EventType) -> bool:
    """Gate 2: two articles may share a cluster only if their canonical types are the same and
    known. OTHER is never compatible with anything (including another OTHER), because unknown
    events must not be silently merged."""
    return left == right and left != EventType.OTHER


# Downstream assets implied by each canonical event type, mirroring the seeded Neo4j CAUSES edges
# (infra/neo4j/init/04+05). Used only as a fallback when title/body keywords name no asset, so a
# geopolitical headline like "USA calls off Iran attack" still resolves to its graph assets. Assets
# are listed in canonical enum order (GOLD, BRENT_OIL).
EVENT_TYPE_ASSETS: dict[EventType, tuple[AssetId, ...]] = {
    EventType.MILITARY_CONFLICT: (AssetId.GOLD, AssetId.BRENT_OIL),
    EventType.STRAIT_CLOSURE: (AssetId.GOLD, AssetId.BRENT_OIL),
    EventType.SUPPLY_DISRUPTION: (AssetId.BRENT_OIL,),
    EventType.SANCTIONS: (AssetId.GOLD, AssetId.BRENT_OIL),
    EventType.RATE_DECISION: (AssetId.GOLD, AssetId.BRENT_OIL),
    EventType.INFLATION_CHANGE: (AssetId.GOLD,),
    EventType.RECESSION_SIGNAL: (AssetId.GOLD, AssetId.BRENT_OIL),
    EventType.NATURAL_DISASTER: (AssetId.GOLD, AssetId.BRENT_OIL),
    EventType.POLITICAL_TRANSITION: (AssetId.GOLD,),
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
)

# Transport/supply cues (lowercase) that qualify a factor as physically threatening oil logistics,
# selecting the TRANSPORT_AFFECTED conditioned edge over the SAFE_HAVEN_ONLY one.
TRANSPORT_CUES: tuple[str, ...] = (
    "strait",
    "hormuz",
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
    """Whole-word match for single tokens; substring match for multi-word phrases."""
    needle = cue if " " in cue else f" {cue} "
    return needle in haystack


def classify_polarity(text: str) -> EventPolarity:
    """Return RESOLUTION when a de-escalation/negation cue is present, else OCCURRENCE."""
    haystack = f" {text.lower()} "
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
    haystack = f" {text.lower()} "
    if any(_cue_present(haystack, cue) for cue in TRANSPORT_CUES):
        return [ConditionCode.TRANSPORT_AFFECTED]
    if event_type in GEOPOLITICAL_EVENT_TYPES:
        return [ConditionCode.SAFE_HAVEN_ONLY]
    return []


def assets_for_event_type(event_type: EventType) -> tuple[AssetId, ...]:
    """Return the downstream assets a canonical event type maps to (empty when it has no edge)."""
    return EVENT_TYPE_ASSETS.get(event_type, ())
