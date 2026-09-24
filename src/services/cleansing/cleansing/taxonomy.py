"""Local, version-controlled action -> canonical taxonomy mapping and asset inference.

This is Gate 2 of clustering and the deterministic classifier used before any LLM call. The
mapping is intentionally conservative: an unmapped action becomes OTHER (retaining the original
lemma for review) rather than being force-fit into a type. Over-merging distinct causal events is
worse than under-merging (functional document sec 1), so two OTHER clusters never merge.

Both English and Swedish keyword forms are included because articles are normalized to the
canonical taxonomy locally, never translated by an LLM (functional document sec 5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from shared.reference import members_of, registry
from shared.schemas.messages import AssetId, ConditionCode, EventPolarity, EventType
from shared.text import sentences

from cleansing.evidence import ClassificationDecision, prepare_inputs, safety_reason
from cleansing.sections import mapped_section_types, section_event_type

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
    # Inflected attack forms. ``_keyword_present`` tolerates only ("", s, es, er, ar, et, en, ing),
    # so "attacked" never matched "attack", and "attackera" matched the bare infinitive alone — a
    # form that essentially never appears in a headline. "Saudiskt raffinaderi attackerat av
    # Huthirörelsen" (2026-08-18, aftonbladet + di) therefore classified OTHER and moved nothing.
    # All of these are listed in GENERIC_KEYWORDS alongside bare "attack", so they only fire on a
    # corroborated title and a sports "attackerade domaren" still falls through.
    "attacked": EventType.MILITARY_CONFLICT,
    "attackerat": EventType.MILITARY_CONFLICT,   # sv: past participle
    "attackerade": EventType.MILITARY_CONFLICT,  # sv: past tense
    "attackerats": EventType.MILITARY_CONFLICT,  # sv: passive perfect
    "attackerar": EventType.MILITARY_CONFLICT,   # sv: present tense
    "invadera": EventType.MILITARY_CONFLICT,  # sv
    "krig": EventType.MILITARY_CONFLICT,       # sv: also matches kriget/krigens (suffix)
    "krigsplan": EventType.MILITARY_CONFLICT,  # sv: war plan
    "iranattack": EventType.MILITARY_CONFLICT, # sv: compound "Iran attack"
    "stridighet": EventType.MILITARY_CONFLICT, # sv: conflict/fighting
    "robotattack": EventType.MILITARY_CONFLICT,  # sv: missile strike ("attack" alone is generic)
    # sv: missile strike, "anfall" root instead of "attack" — a distinct compound, not reachable by
    # suffix tolerance from bare "anfall" (line above) since the two are glued with no word boundary.
    # "USA och Iran trappar upp attackerna" (2026-09-09) classified OTHER until this was added: the
    # title's own "attackerna" is a GENERIC inflection needing corroboration that title lacked, but the
    # body's "Iran har svarat med robotanfall" names the event outright.
    "robotanfall": EventType.MILITARY_CONFLICT,
    # sv: wedding attack. Compound with "attack", unreachable by suffix tolerance from bare "attack".
    # "Sorg efter bröllopsattack" (2026-09-09, US strike on a wedding in Iran) classified OTHER without
    # this: the title carries no other market/conflict word for corroboration.
    "bröllopsattack": EventType.MILITARY_CONFLICT,
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
    # sv: definite plural "the sanctions". -erna is outside the suffix set in ``_keyword_present``
    # (same gap as "vinstutsikterna"/"bopriserna" elsewhere in this module), so it is listed explicitly.
    # "Roman Abramovitj får nej – sanktionerna kvar" (2026-09-09) classified OTHER without this.
    "sanktionerna": EventType.SANCTIONS,  # sv
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
    # sv: "the inflation expectations". Listed in the definite plural because -arna is outside the
    # suffix set, so neither "inflationsförväntning" nor "inflation" reaches the headline form.
    "inflationsförväntningarna": EventType.INFLATION_CHANGE,
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
    "ebita": EventType.CORPORATE_EARNINGS,
    "ebitda": EventType.CORPORATE_EARNINGS,
    "intäkter": EventType.CORPORATE_EARNINGS,  # sv: revenues. PLURAL only — bare "intäkt" is broader
    "kvartalet": EventType.CORPORATE_EARNINGS,  # sv: "the quarter", as in "för det andra kvartalet"
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
    "drought": EventType.NATURAL_DISASTER,
    "heatwave": EventType.NATURAL_DISASTER,
    "jordbävning": EventType.NATURAL_DISASTER,  # sv
    "översvämning": EventType.NATURAL_DISASTER,  # sv
    "skalv": EventType.NATURAL_DISASTER,  # sv: (earth)quake; Swedish wires prefer this to "jordbävning"
    # sv: PLURAL "fires" only. Bare "brand" is deliberately absent: it is the ordinary word for any
    # fire, and on 2026-08-17 it matched "en större brand i Nykvarn" — twelve burnt cars and an
    # arrest, not a natural disaster. The plural is what wildfire coverage uses ("Bränderna i
    # Europa", "bekämpar bränder i Belgien"), and it leaves the urban "storbrand" headlines alone.
    "bränder": EventType.NATURAL_DISASTER,
    "bränderna": EventType.NATURAL_DISASTER,  # sv: definite plural (-na is outside the suffix set)
    # "torka" (drought) is deliberately absent: it is also the verb "to wipe/dry", and in this corpus
    # it fired on an election-campaign article as readily as on the English heatwave coverage.
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
    # sv: past participle "acquired". "förvärv" does not suffix-match "förvärvat", so it is listed.
    "förvärvat": EventType.CORPORATE_ACQUISITION,
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
    # sv: approves (verb). "godkänd" does not suffix-match it, so it is listed separately.
    "godkänner": EventType.REGULATORY_ACTION,
    "godkännande": EventType.REGULATORY_ACTION,     # sv: approval
    "tillstånd": EventType.REGULATORY_ACTION,       # sv: licence/permit
    "böter": EventType.REGULATORY_ACTION,           # sv: fine
    # DEBT_CRISIS
    "bankruptcy": EventType.DEBT_CRISIS,
    "bankrupt": EventType.DEBT_CRISIS,
    # Bare "default" is deliberately absent. In English it is far more often a settings default than
    # a credit event ("enabled by default", "default settings"), and DEBT_CRISIS seeds a DOWN 0.55
    # edge to EVERY asset group (infra/neo4j/init/07), so one false match moves the whole registry.
    # A Twitch story headlined "...On Your Channel By Default" produced an AMZN DOWN MEDIUM
    # prediction on 2026-08-12 through exactly this path. The unambiguous forms below carry the real
    # signal.
    "sovereign default": EventType.DEBT_CRISIS,
    "debt default": EventType.DEBT_CRISIS,
    "defaults on": EventType.DEBT_CRISIS,
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
    "stämning": EventType.LEGAL_DISPUTE,            # sv: lawsuit (noun)
    "utredning": EventType.LEGAL_DISPUTE,           # sv: investigation
    "uppgörelse": EventType.LEGAL_DISPUTE,          # sv: settlement
    "bedrägeri": EventType.LEGAL_DISPUTE,           # sv: fraud
    # sv: "threatens to sue". The bare verb "stämma" is deliberately absent — it also means "to tune"
    # (a choir/instrument) and "to reconcile/check in" ("stämma av"), so it would repeat the "torka"
    # false-positive class this module already guards against. The phrase is unambiguous.
    "hotar stämma": EventType.LEGAL_DISPUTE,
    # sv: trial. "Rättegången inledd" (2026-09-09, a contested inheritance case) classified OTHER
    # without this.
    "rättegång": EventType.LEGAL_DISPUTE,
    # sv: "in court". Bare "rätten" is deliberately absent — it is also the ordinary word for "the
    # right" ("har rätten att...") and would be as generic a false-positive source as bare "rätt". The
    # phrase, in this word order, is specific to a courtroom.
    "i rätten": EventType.LEGAL_DISPUTE,
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
    # sv: tax relief/break — a distinct compound from "skattesänkning" above. "USA:s delstater bromsar
    # skattelättnad för datacenter" (2026-09-09) classified OTHER without this.
    "skattelättnad": EventType.FISCAL_POLICY,
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
    "avfyrningsramp": EventType.GEOPOLITICAL_TENSION,  # sv: launch ramp; suffix → -ramper
    # sv: drone. Also appears in bodies describing an actual strike, but a strike headline is typed
    # MILITARY_CONFLICT from the title before the body tier is ever consulted, so this does not
    # downgrade one.
    "drönare": EventType.GEOPOLITICAL_TENSION,
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
    "industrial production": EventType.ECONOMIC_DATA_RELEASE,
    "house prices": EventType.ECONOMIC_DATA_RELEASE,
    "bnp": EventType.ECONOMIC_DATA_RELEASE,         # sv: GDP
    "arbetslöshet": EventType.ECONOMIC_DATA_RELEASE,  # sv: unemployment
    "inköpschefsindex": EventType.ECONOMIC_DATA_RELEASE,  # sv: PMI
    "industriproduktion": EventType.ECONOMIC_DATA_RELEASE,  # sv: industrial production
    "detaljhandel": EventType.ECONOMIC_DATA_RELEASE,        # sv: retail (trade)
    "huspris": EventType.ECONOMIC_DATA_RELEASE,             # sv: house price; suffix → huspriser
    # Swedish definite plural (-erna) is outside the suffix set in ``_keyword_present``, so the
    # inflected form is listed explicitly — same approach as "vinstutsikterna" below.
    "bopriserna": EventType.ECONOMIC_DATA_RELEASE,  # sv: the housing prices
    # PANDEMIC_OUTBREAK
    "pandemic": EventType.PANDEMIC_OUTBREAK,
    "epidemic": EventType.PANDEMIC_OUTBREAK,
    "outbreak": EventType.PANDEMIC_OUTBREAK,
    "lockdown": EventType.PANDEMIC_OUTBREAK,
    "pandemi": EventType.PANDEMIC_OUTBREAK,         # sv
    "utbrott": EventType.PANDEMIC_OUTBREAK,         # sv: outbreak
    "nedstängning": EventType.PANDEMIC_OUTBREAK,    # sv: lockdown
    "ebolautbrott": EventType.PANDEMIC_OUTBREAK,    # sv compound; "utbrott" alone misses it
    # sv: a measles CASE. Bare "mässling" does not match the compound "mässlingfall", and the
    # compound is what the reporting uses.
    "mässlingfall": EventType.PANDEMIC_OUTBREAK,
    # ENERGY_POLICY
    "carbon tax": EventType.ENERGY_POLICY,
    "green deal": EventType.ENERGY_POLICY,
    "renewable energy": EventType.ENERGY_POLICY,
    "nuclear power": EventType.ENERGY_POLICY,
    "combustion ban": EventType.ENERGY_POLICY,
    "koldioxidskatt": EventType.ENERGY_POLICY,      # sv: carbon tax
    "kärnkraft": EventType.ENERGY_POLICY,           # sv: nuclear power
    "förnybar energi": EventType.ENERGY_POLICY,     # sv: renewable energy
    # CORPORATE_EARNINGS (sv compounds). Bare "vinst" is deliberately absent: in Swedish sports
    # reporting it means "a win" ("Djurgårdens vinst"), which is the exact false-positive class this
    # module now guards against. Only unambiguous compounds are listed.
    "vinstvarning": EventType.CORPORATE_EARNINGS,   # sv: profit warning
    "vinstkross": EventType.CORPORATE_EARNINGS,     # sv: profit crash
    "vinstkollaps": EventType.CORPORATE_EARNINGS,   # sv: profit collapse
    "vinstutsikt": EventType.CORPORATE_EARNINGS,    # sv: profit outlook
    # Swedish definite plural (-erna) is outside the generic suffix set in ``_keyword_present``, so
    # the inflected form is listed explicitly — same approach as "hormuz"/"hormuzsundet" above.
    "vinstutsikterna": EventType.CORPORATE_EARNINGS,  # sv: the profit outlook
    "rörelsevinst": EventType.CORPORATE_EARNINGS,   # sv: operating profit
    "kvartalsvinst": EventType.CORPORATE_EARNINGS,  # sv: quarterly profit
    "delårsrapport": EventType.CORPORATE_EARNINGS,  # sv: interim report
    "bokslut": EventType.CORPORATE_EARNINGS,        # sv: year-end report
    # RATE_DECISION (sv compounds)
    "räntebesked": EventType.RATE_DECISION,         # sv: rate announcement
    "styrräntan": EventType.RATE_DECISION,          # sv: the policy rate (definite form)
    # CORPORATE_ACQUISITION (sv compounds)
    "budpliktsbud": EventType.CORPORATE_ACQUISITION,  # sv: mandatory takeover bid
    # sv: major shareholder BUYS/increases holding — stake building, i.e. acquisition-shaped. The bare
    # noun "storägare" is deliberately absent (2026-09-09 audit): it named the shareholder but not the
    # direction, so "Storägare säljer i danska jätten" (a major holder SELLING) was typed
    # CORPORATE_ACQUISITION with no basis. These phrases keep the noun tied to a buying verb.
    "storägare köper": EventType.CORPORATE_ACQUISITION,
    "storägare ökar": EventType.CORPORATE_ACQUISITION,
}

# Keywords too generic to be trusted outside a headline. Each one measurably mistyped unrelated
# articles when matched against body prose: "close" alone typed 14 sports/markets clusters as
# STRAIT_CLOSURE, "gold" fired on Olympic gold medals, "penalty" on football penalties, "contract"
# on player contracts. They stay in the taxonomy because they are correct in a title ("Iran closes
# strait"), but `classify_text` only honours them there — see the tier order in that function.
GENERIC_KEYWORDS: frozenset[str] = frozenset(
    {
        "close",
        "closure",
        "block",
        "gold",
        "contract",
        "penalty",
        "strike",
        "rate",
        "rates",
        "val",
        "hack",
        "budget",
        "partnership",
        "investigation",
        "attack",
        # The inflected forms share bare "attack"'s failure mode ("Kane attacked the defence"), so
        # they are gated the same way: honoured in a corroborated title, never in body prose.
        "attacked",
        "attackerat",
        "attackerade",
        "attackerats",
        "attackerar",
        "approved",
        "cleared",
        "fired",
        "settlement",
        "listing",
        "outbreak",
        "tensions",
        "halt",
        "brist",
        "deal signed",
        "profit",
        "revenue",
        "sund",
    }
)

# Keyword -> non-financial type. These are the explicit reject buckets: an article matching here is
# recognised as irrelevant instead of falling through to OTHER (which means "valid event not yet
# represented" per REF-01 §2). Deliberately specific: broad words like "game", "season", "transfer",
# "cup" and "club" are omitted because they also occur in market news — "Goldman Sachs is paying
# $2.25 billion to get into Bitcoin income game" must not be suppressed.
NON_FINANCIAL_KEYWORDS: dict[str, EventType] = {
    # SPORT
    "nfl": EventType.SPORT,
    "nba": EventType.SPORT,
    "mlb": EventType.SPORT,
    "nhl": EventType.SPORT,
    "ncaa": EventType.SPORT,
    "nascar": EventType.SPORT,
    "wwe": EventType.SPORT,
    "ufc": EventType.SPORT,
    "golf": EventType.SPORT,
    "tennis": EventType.SPORT,
    "quarterback": EventType.SPORT,
    "touchdown": EventType.SPORT,
    "goalie": EventType.SPORT,
    # Sport names are unambiguous enough to reject on. A listed club or sportswear firm is still
    # safe: a company keyword in the headline skips this tier entirely (see ``classify_text``).
    "basketball": EventType.SPORT,
    "baseball": EventType.SPORT,
    "football": EventType.SPORT,
    "soccer": EventType.SPORT,
    "hockey": EventType.SPORT,
    "cricket": EventType.SPORT,
    "rugby": EventType.SPORT,
    # "olympic"/"medal" also pre-empt the generic "gold" keyword, which otherwise reads a gold medal
    # as a COMMODITY_PRICE_SHOCK. The non-financial tier runs before the generic tier by design.
    "olympic": EventType.SPORT,
    "medal": EventType.SPORT,
    "playoff": EventType.SPORT,
    "preseason": EventType.SPORT,
    "midfielder": EventType.SPORT,
    "home run": EventType.SPORT,
    "hall of fame": EventType.SPORT,
    "allsvenskan": EventType.SPORT,   # sv: Swedish top football league
    "matchen": EventType.SPORT,       # sv: the match
    "laget": EventType.SPORT,         # sv: the team
    "tränare": EventType.SPORT,       # sv: coach
    # ENTERTAINMENT
    "eurovision": EventType.ENTERTAINMENT,
    "bafta": EventType.ENTERTAINMENT,
    "box office": EventType.ENTERTAINMENT,
    "casting": EventType.ENTERTAINMENT,
    "skådespelare": EventType.ENTERTAINMENT,  # sv: actor
    "premiär": EventType.ENTERTAINMENT,       # sv: premiere
    # LIFESTYLE
    "horoscope": EventType.LIFESTYLE,
    "recipe": EventType.LIFESTYLE,
    "star sign": EventType.LIFESTYLE,
    "sexolog": EventType.LIFESTYLE,  # sv: sexologist (relationship/sex-advice column byline)
}

# Vocabulary that overrides a URL section's non-financial guess (CLN-71 residual, 2026-09-09 audit).
# Tier 0 trusts the publisher's filing decision, but a desk can carry more than one kind of content:
# DN's "kultur" desk mixes book/theatre reviews (correctly ENTERTAINMENT) with columnists writing
# about politics, and its "sport" desk mixes match reports with crime coverage filed there because the
# story happens to be about sports venues. A section cannot tell those apart; the headline's own
# vocabulary can. Mirrors the company-name escape hatch in ``classify_text``: any match here skips
# tier 0 (and tier 1) exactly like a registered company would, so the keyword tiers run and the
# article lands on OTHER rather than being silently accepted as the section's default guess.
#
# Deliberately narrow: each entry is drawn from an actual 2026-09-09 misclassification and is a
# specific compound or phrase, not a bare stem — bare "politik"/"politiker"/"politiskt" are absent
# because a genuine culture review can legitimately use them ("Både party och politiskt allvar när
# Gorillaz avslutar allt" is a Gorillaz festival review, correctly ENTERTAINMENT). Every term here is
# several steps more specific than that.
SECTION_OVERRIDE_CUES: frozenset[str] = frozenset(
    {
        "valrörelsen", "maktskifte", "systemskifte", "kulturpolitik", "mandatperioden",
        "högernationalismen", "nazism", "migrationsdebatt", "ministerintervjun",
        "pisaresultatet", "pisa katastrofen", "politiskt läger",
        "gängkriminella", "gängkriminalitet", "gängrelaterad",
    }
)

# Types that carry no causal edge and no asset mapping. Used to short-circuit asset resolution and
# to keep these articles out of clustering (see ``gate2_compatible``). Mirrors the
# ``GEOPOLITICAL_EVENT_TYPES`` pattern further down this module.
NON_FINANCIAL_EVENT_TYPES: frozenset[EventType] = frozenset(
    {EventType.SPORT, EventType.ENTERTAINMENT, EventType.LIFESTYLE}
)

# Neither a non-financial article nor an unmapped one may join a cluster: distinct causal events
# must not be silently merged, and irrelevant articles have no causal event at all.
NON_CLUSTERING_EVENT_TYPES: frozenset[EventType] = NON_FINANCIAL_EVENT_TYPES | {EventType.OTHER}

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


def _scan(
    text: str,
    table: dict[str, EventType],
    *,
    include: frozenset[str] | None = None,
    exclude: frozenset[str] | None = None,
) -> tuple[EventType, str | None]:
    """Scan ``text`` for the earliest keyword in ``table``, restricted by include/exclude.

    Returns the mapped event type and the matched keyword, or (OTHER, None) when nothing matches.
    Multi-word keys are checked as substrings so phrases like "interest rate" win over the bare
    token. Single-token keys use ``_keyword_present`` so Swedish definite/inflected forms
    (e.g. "kriget", "oljepriset") and English plurals match without enumerating every form.

    The haystack is punctuation-normalised before matching so symbols attached to words
    (e.g. "opec+" or "anfall:") do not defeat word-boundary detection.

    A multi-word key is anchored on its **leading** side only, and that asymmetry is deliberate
    (CLN-69). Without a leading boundary the key matched inside a longer word: ``"bud på"`` matched
    ``"återbud på"`` (*a withdrawal*), which typed an athlete pulling out of a final as
    ``CORPORATE_ACQUISITION``. Anchoring the trailing side too would fix that and break Swedish
    compounding, where the qualifier is glued to the noun: ``"em guld"`` would no longer match
    ``EM-guldet``, which normalises to ``em guldet``. Do not "tidy" this into symmetry.
    """
    haystack = f" {_normalise(text.lower())} "
    best_type = EventType.OTHER
    best_keyword: str | None = None
    best_pos = len(haystack) + 1
    best_len = 0
    for keyword, event_type in table.items():
        if include is not None and keyword not in include:
            continue
        if exclude is not None and keyword in exclude:
            continue
        if " " in keyword:
            # Multi-word phrase, anchored on its leading side so it cannot match inside a longer
            # word (see the docstring: "bud på" must not match "återbud på"). `find(" keyword")`
            # returns the index of the leading SPACE, so +1 gives the index of the word itself —
            # the same basis the single-token branch uses.
            pos = haystack.find(f" {keyword}")
            if pos != -1:
                pos += 1
                # On a tie the LONGER keyword wins: both "appoint" and "appoints new cfo" start at
                # the same word, and the more specific phrase is the better classification.
                if pos < best_pos or (pos == best_pos and len(keyword) > best_len):
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


def classify_text(title: str, body: str = "", url: str = "") -> tuple[EventType, str | None]:
    """Compatibility interface; decisions and safety metadata live in classify_decision."""
    decision = classify_decision(title, body, url)
    return decision.event_type, decision.keyword


def classify_decision(
    title: str, body: str = "", url: str = "", *, mode: str = "title_first"
) -> ClassificationDecision:
    title, body = prepare_inputs(title, body, mode)
    if not title:
        return ClassificationDecision(EventType.OTHER, None, "title", "unusable_title", "")
    reason = safety_reason(title)
    if reason:
        kind = EventType.GEOPOLITICAL_TENSION if reason == "reported_joke" else EventType.OTHER
        return ClassificationDecision(kind, None, "title", reason, title)
    # Preserve the measured body veto and fallback; stricter lexical relevance is opt-in.
    title_kind, title_keyword = _classify_tiers(title, "", url)
    kind, keyword = _classify_tiers(title, body, url)
    source = "title"
    evidence = title
    if keyword and keyword.startswith("section:"):
        source = "publisher_section"
        evidence = keyword
    elif (kind, keyword) != (title_kind, title_keyword):
        source = "body_veto" if kind in NON_FINANCIAL_EVENT_TYPES else "body"
        evidence = next(
            (
                span
                for span in sentences(body)
                if keyword and _keyword_present(_haystack(span), keyword)
            ),
            body,
        )
    return ClassificationDecision(
        kind,
        keyword,
        source,
        "unmapped" if kind == EventType.OTHER else "tiered_rule",
        evidence,
        allow_llm=kind == EventType.OTHER,
    )


def _classify_tiers(title: str, body: str = "", url: str = "") -> tuple[EventType, str | None]:
    """Deterministically classify an article, most trustworthy evidence first.

    Returns the mapped event type and the matched keyword (the "action" evidence), or (OTHER, None)
    when nothing matches.

    Evidence is tiered rather than taken purely by position, because position within a long body is
    not a measure of relevance. Tiers, first hit wins:

      0. **The publisher's section**, read from ``url`` (CLN-71). An editor's filing decision beats
         any inference from text: on the 2026-08-17 corpus the DN sport section was correct 26 times
         out of 26, while the keyword reject tier below fired twice in 251 articles. Skipped when the
         title names a registered company, exactly as tier 1 is, or matches SECTION_OVERRIDE_CUES —
         a desk can carry more than one kind of content (2026-09-09: DN's "kultur" desk also files
         political columns, its "sport" desk also files crime-at-a-venue stories), and the section
         alone cannot tell those apart. This tier can only select a non-financial type — a section
         must never manufacture a market classification — and it fails open, so a missing or unmapped
         URL classifies as if none had been supplied (CLN-72).
      1. **Non-financial keyword in the title** — sport/entertainment/lifestyle, which makes the
         reject explicit instead of letting a market keyword mistype it. Skipped when the title
         names a registered company, so "Nike lifts full-year guidance" is never suppressed by a
         sports word.
      2. **Specific keyword in the title.** The title states what the article is about, and a
         specific keyword there is the strongest signal available.
      3. **Generic keyword in the title**, but only when the title is corroborated — it names a
         company, an industry, or a ``FALLBACK_CUES`` domain cue. Words like "close" or "gold" are
         only trustworthy in a headline, and only when something else in that headline says the
         article is about a market at all.
      4. **Specific keyword in the body.** Recovers articles whose headline is vague but whose body
         names the event outright. Generic keywords are deliberately never honoured here — matching
         them against body prose is what typed sports reports as STRAIT_CLOSURE.

    The reject tier runs FIRST (it used to run second). A WWE headline containing "The **War**
    Raiders" was typed MILITARY_CONFLICT by tier 2 before "wwe" ever got a chance to reject it, and
    a wrestling match then moved gold and every weapons maker. Rejecting before classifying is the
    only order in which the reject buckets do the job they exist for.

    Tier 3's corroboration requirement is what stops "Forcing Early **Closure** Of Garden Display"
    (stolen Mozart figurines) becoming a STRAIT_CLOSURE and "lower overuse injury **rates**"
    becoming a RATE_DECISION, while keeping "Gold expected to trade around $4,500/oz" — whose
    generic "gold" is corroborated by the SAFE_HAVEN cue in the same headline.

    ``body`` and ``url`` are optional so a caller with only a headline (and the existing
    lemma-fallback path in ``extraction``) can pass one argument.
    """
    haystack = _haystack(title)
    names_company = bool(_company_matches(haystack)[0])
    section_overridden = any(_keyword_present(haystack, cue) for cue in SECTION_OVERRIDE_CUES)

    # A company-specific headline is financial news by definition; never reject it as sport. This
    # guard covers tier 0 and tier 1 alike. A headline matching SECTION_OVERRIDE_CUES gets the same
    # treatment: it is not financial news either, but it is also not what the section thinks it is.
    if not names_company and not section_overridden:
        from_section = section_event_type(url)
        if from_section is not None:
            # The keyword slot records WHY the article was rejected, so an audit export can show that
            # the section decided it rather than a keyword. Downstream treats this as opaque text.
            return from_section, f"section:{from_section.value.lower()}"

        non_financial, keyword = _scan(title, NON_FINANCIAL_KEYWORDS)
        if non_financial != EventType.OTHER:
            return non_financial, keyword

    specific_in_title, keyword = _scan(title, ACTION_TAXONOMY, exclude=GENERIC_KEYWORDS)
    if specific_in_title != EventType.OTHER:
        return _vetoed_by_body(specific_in_title, keyword, body, names_company, haystack)

    if _is_corroborated(title, haystack, names_company):
        generic_in_title, keyword = _scan(title, ACTION_TAXONOMY, include=GENERIC_KEYWORDS)
        if generic_in_title != EventType.OTHER:
            return _vetoed_by_body(generic_in_title, keyword, body, names_company, haystack)

    if body:
        specific_in_body, keyword = _scan(body, ACTION_TAXONOMY, exclude=GENERIC_KEYWORDS)
        return _vetoed_by_body(specific_in_body, keyword, body, names_company, haystack)
    return EventType.OTHER, None


def _is_corroborated(title: str, haystack: str, names_company: bool) -> bool:
    """True when the title carries market context beyond the generic keyword itself.

    Five independent signals, any one of which is enough: a registered company, an industry keyword,
    a
    money/percentage figure, an institutional market term, or a domain cue.
    """
    if names_company:
        return True
    if _industry_matches(haystack)[0]:
        return True
    if _MONEY_OR_PERCENT.search(title):
        return True
    if any(_keyword_present(haystack, term) for term in CORROBORATION_TERMS):
        return True
    return bool(cue_families_present(title))


def _vetoed_by_body(
    event_type: EventType,
    keyword: str | None,
    body: str,
    names_company: bool,
    title_haystack: str,
) -> tuple[EventType, str | None]:
    """Reject a market classification when the BODY reveals a non-financial subject.

    Some headlines are indistinguishable from market news by keyword alone: "Damian Priest &
    R-Truth,
    The War Raiders and The MFTs clash in title bout" contains "war" and nothing that says wrestling
    — only the body's "WWE Tag Team Title" does. Without this veto that match moved gold and every
    weapons maker on 2026-08-12.

    Skipped when the title names a company or an industry, which is the guard that keeps a genuine
    market story from being rejected because its body mentions a sport ("Nike lifts guidance" whose
    body discusses football boots). The residual risk is a company-less market headline whose body
    happens to name a sport; that is accepted deliberately, because 22 of the 73 articles in the
    2026-08-12 corpus were sport and none of the market ones were company-less in that way.
    """
    if event_type in NON_FINANCIAL_EVENT_TYPES or event_type is EventType.OTHER:
        return event_type, keyword
    if not body or names_company or _industry_matches(title_haystack)[0]:
        return event_type, keyword
    non_financial, reject_keyword = _scan(body, NON_FINANCIAL_KEYWORDS)
    if non_financial != EventType.OTHER:
        return non_financial, reject_keyword
    return event_type, keyword


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
    return _company_matches(_haystack(text))[0]


def _haystack(text: str) -> str:
    """Lowercase, punctuation-normalise, and space-pad text for keyword matching.

    Every caller of ``_company_matches`` / ``_industry_matches`` / ``_keyword_present`` must build
    its
    haystack here. Scope resolution previously used a bare ``f" {text.lower()} "``, which skipped
    normalisation that ``_keyword_present`` documents as a precondition: "Exxon, Inc." then failed
    to
    match the "exxon" keyword, and "Lockheed-Martin wins missile contract" fell through company
    scope
    into an industry fan-out that predicted its competitors.
    """
    return f" {_normalise(text.lower())} "


# Compiled once: every article passes through _normalise several times per classification.
_NON_WORD = re.compile(r"[^\w\s]", flags=re.UNICODE)

# A money amount or a percentage in a headline is market context in its own right, and it
# corroborates a generic keyword that would otherwise be untrustworthy: "CoreWeave reports $2.575B
# Q2 revenue" is an earnings story even though "revenue" is generic and CoreWeave is not in the
# registry. Matched against the RAW title, before normalisation strips the symbols. A bare count
# does not qualify — "Over 200 Mozart Figurines Stolen" must stay uncorroborated.
_MONEY_OR_PERCENT = re.compile(
    r"""(
        [$€£]\s?\d                      # $2.575B, € 4,500
      | \d\s?%                          # 12%
      | \b\d[\d.,]*\s?(bn|b|m|k)\b      # 104B, 2.5bn
      | \b\d[\d.,]*\s?(billion|million|trillion|miljard|miljon)
      | \b(usd|eur|sek|dkk|nok|gbp|kr|mdr)\s?\d
      | \b\d[\d.,]*\s?(usd|eur|sek|dkk|nok|gbp|kr)\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def _normalise(text: str) -> str:
    """Replace non-alphanumeric, non-space characters with spaces for word-boundary matching.

    This lets "opec+" match the "opec" keyword and "anfall:" match "anfall". Swedish letters
    (åäö) are preserved because they are part of valid keywords.
    """
    return _NON_WORD.sub(" ", text)


def _keyword_present(haystack: str, keyword: str) -> bool:
    """Keyword match with English/Swedish suffix tolerance.

    Haystack is expected to already be lowercased and punctuation-normalised (see
    ``classify_text``). Multi-word phrases match as substrings. A single token matches as a whole
    word and also with a trailing suffix so "missiles" fires "missile" and "kriget" fires "krig"
    (Swedish definite form). Suffixes: English plurals (s/es/er) and Swedish inflections (et/en/ar/ing).

    The keyword is normalised on the same terms as the haystack, so a registry keyword containing
    punctuation ("saab-b") becomes a phrase match rather than a token that could never appear in a
    normalised haystack.
    """
    keyword = _normalise(keyword)
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
         a geopolitical headline still reaches the commodities it moves. Gated: each target requires
         a cue from its own domain in ``text`` (see ``FALLBACK_CUES``), because the event type alone
         cannot evidence that the article is about that subject.

    Returns an empty ``NONE`` scope when nothing resolves; the caller decides whether that is worth
    recording. Never raises.

    A non-clusterable event type resolves to nothing at all, including when the text happens to
    mention a company or industry: a football result naming a listed club is not a market event, and
    Prediction drops asset-less events. This is what keeps the reject bucket from reaching
    Prediction via the event-type fallback below.

    ``OTHER`` is included (not just the non-financial types). An OTHER event has no causal factor in
    the graph, so it can never produce a prediction — resolving assets for it only created contexts
    that sat there collecting unrelated events and polluting provenance. It also had a sharper cost:
    a
    private ice-cream maker's press release ("GRIPPO FOODS, INC.") matched the FOOD_INGREDIENTS
    industry keyword "food" and attached AAK to an OTHER event.
    """
    if event_type in NON_CLUSTERING_EVENT_TYPES or safety_reason(text):
        return AssetScope(NewsScope.NONE, ())

    haystack = _haystack(text)

    assets, matched = _company_matches(haystack)
    if assets:
        return AssetScope(NewsScope.COMPANY, assets, matched)

    assets, matched = _industry_matches(haystack)
    if assets:
        return AssetScope(NewsScope.INDUSTRY, assets, matched)

    fallback, families = gated_assets_for_event_type(event_type, text)
    if fallback:
        # Provenance records the cue families that admitted the targets, not just the event type:
        # the event type was already known and never explained which assets were selected or why.
        return AssetScope(NewsScope.EVENT_TYPE, fallback, (event_type.value, *families))

    return AssetScope(NewsScope.NONE, ())


def gate2_compatible(left: EventType, right: EventType) -> bool:
    """Gate 2: two articles may share a cluster only if their canonical types are the same and
    clusterable. ``OTHER`` is never compatible with anything (including another OTHER), because
    unknown events must not be silently merged. The non-financial types are excluded for the
    opposite reason: they carry no causal event, so grouping them would build clusters that can
    never produce a prediction."""
    return left == right and left not in NON_CLUSTERING_EVENT_TYPES


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
    # A commodity price shock moves the producers of THAT commodity, which the cue gate selects: a
    # gold forecast evidences SAFE_HAVEN and reaches the miners, an OPEC story evidences ENERGY and
    # reaches the oil names, and neither reaches the other. Restores the one prediction class this
    # table was missing — on 2026-08-12 an LBMA gold forecast produced nothing at all, because
    # COMMODITY_PRICE_SHOCK had no fallback entry while sports reports had one via other types.
    EventType.COMMODITY_PRICE_SHOCK: ("PRECIOUS_METALS", "OIL_GAS"),
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

# Cues that the de-escalation named in the same headline is BREAKING DOWN rather than holding.
# Checked before RESOLUTION_CUES and short-circuiting to OCCURRENCE, because a bare cue match cannot
# tell the two readings apart. "Oil prices rise as US-Iran ceasefire ends" (2026-08-18) matched
# "ceasefire" and was typed RESOLUTION, which inverted COMMODITY_PRICE_SHOCK's UP edge to DOWN; the
# Scope-B price gate in ``decision._resolved_direction`` then dropped that DOWN as un-elevated,
# leaving no material edge — so a headline explicitly reporting an oil rise produced no
# prediction at all for either OIL_GAS member.
#
# Forms are enumerated rather than stemmed wherever the stem is ambiguous: substring matching on
# "ceasefire end" would also swallow "ceasefire endures", and "ceasefire break" would swallow
# "ceasefire breakthrough" — both the opposite meaning. Where the stem is unambiguous ("expire",
# "collapse", "fail") the short form is used and covers every inflection.
#
# A headline carrying both readings ("ceasefire agreed after earlier truce breakdown") resolves to
# OCCURRENCE. That is the conservative direction: OCCURRENCE keeps the factor's stored sign instead
# of asserting an inversion, matching the tie-breaking default in ``merge._event_polarity``.
RESOLUTION_NEGATION_CUES: tuple[str, ...] = (
    "ceasefire ends",
    "ceasefire ended",
    "ceasefire ending",
    "ceasefire expire",
    "ceasefire collapse",
    "ceasefire fail",
    "ceasefire breaks down",
    "ceasefire broke down",
    "ceasefire breakdown",
    "ceasefire is over",
    "ceasefire violated",
    "ceasefire violation",
    "end of the ceasefire",
    "end of ceasefire",
    "no ceasefire",
    "truce ends",
    "truce ended",
    "truce ending",
    "truce expire",
    "truce collapse",
    "truce fail",
    "truce breaks down",
    "truce broke down",
    "truce breakdown",
    "truce is over",
    "end of the truce",
    "end of truce",
    "no truce",
    "no deal",
    "no agreement",
    "vapenvilan upphör",  # sv: the ceasefire ends (substring also covers "upphörde")
    "vapenvila upphör",  # sv
    "vapenvilan löper ut",  # sv: the ceasefire expires
    "vapenvilan är över",  # sv: the ceasefire is over
    "vapenvilan bryts",  # sv: the ceasefire is broken
    "vapenvilan bröts",  # sv: the ceasefire was broken
    "vapenvilan avslutas",  # sv: the ceasefire is concluded/terminated
    "vapenvilan tar slut",  # sv: the ceasefire comes to an end
    "vapenvilan tagit slut",  # sv: the ceasefire has come to an end
    "slutet på vapenvilan",  # sv: the end of the ceasefire
    "ingen vapenvila",  # sv: no ceasefire
    "inget avtal",  # sv: no deal
    "ingen överenskommelse",  # sv: no agreement
)

# REGULATORY_ACTION covers two opposite events under one factor with one DOWN prior. These cue sets
# separate them so a favourable ruling is not predicted as a penalty. See
# ``_is_favourable_regulatory``.
REGULATORY_APPROVAL_CUES: tuple[str, ...] = (
    "approves", "approved", "approval", "cleared", "clears", "authorised", "authorized",
    "authorisation", "green light", "wins approval", "grants licence", "grants license",
    "godkänner", "godkänd", "godkännande", "beviljar",  # sv
)

REGULATORY_ENFORCEMENT_CUES: tuple[str, ...] = (
    "fined", "fine", "fines", "penalty", "penalties", "rejected", "rejection", "rejects",
    "sanctioned", "revoked", "revokes", "banned", "bans", "withdrawn", "suspended", "probe",
    "böter", "avslår", "avslag", "återkallar", "förbjuder",  # sv
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
    # Swedish parity for "refinery"/"tanker" above. Without these a Swedish-language strike on oil
    # infrastructure inferred SAFE_HAVEN_ONLY and moved only the gold proxies, because
    # MILITARY_CONFLICT -> OIL_GAS exists solely as the TRANSPORT_AFFECTED conditioned edge
    # (infra/neo4j/init/05) — the unconditional one is deleted there on purpose.
    "raffinaderi",  # sv: refinery
    "tankfartyg",   # sv: oil tanker
    "oljehamn",     # sv: oil port/terminal
)

# --- Event-type fallback cue gate ---------------------------------------------------------------
#
# The fallback in ``resolve_scope`` used to fire on event type alone, so ANY article that classified
# as a macro type reached the gold and oil proxies. On 2026-08-12 that put 76 of 113 predictions on
# three assets and let stolen Mozart figurines ("Forcing Early Closure Of Garden Display" ->
# STRAIT_CLOSURE) move oil. The event type says what KIND of event it is; it cannot say the article
# is about that subject at all. So each fallback target now requires a cue from its own domain, in
# the TITLE. This mirrors at the asset-selection layer what ADR-006's conditioned edges do at the
# edge layer: a conflict with no transport cue is a safe-haven bid for gold, not an oil shock. Cues
# are domain nouns, deliberately NOT the taxonomy keywords that selected the event type — that would
# be circular and would re-admit exactly the false positives this gate exists to stop. "rates" types
# an article as RATE_DECISION; only "interest rate"/"central bank" evidences one, which is why
# "lower overuse injury rates for new parents" no longer moves gold.
FALLBACK_CUES: dict[str, tuple[str, ...]] = {
    # Gates the precious-metals proxies: safe-haven, monetary, and trade-shock demand for gold.
    "SAFE_HAVEN": (
        "war", "conflict", "attack", "invasion", "missile", "sanction", "sanctions", "embargo",
        "inflation", "cpi", "deflation", "interest rate", "rate cut", "rate hike", "central bank",
        "federal reserve", "ecb", "recession", "gold", "bullion", "safe haven", "tariff",
        "trade war",
        "krig", "anfall", "invasion", "sanktion", "sanktioner", "ränta", "styrränta",  # sv
        "riksbanken", "centralbank", "lågkonjunktur", "guld", "handelskrig", "tull",   # sv
    ),
    # Gates the oil and gas proxies. Bare "oil" is absent on purpose: with suffix tolerance it also
    # fires on "Oilers", and the phrase forms below carry the same signal without the collision.
    "ENERGY": (
        "oil price", "oil supply", "oil export", "crude", "opec", "refinery", "pipeline", "tanker",
        "strait", "hormuz", "barrel", "petroleum", "shale", "lng", "fuel", "energy crisis",
        "olja", "oljepris", "raffinaderi", "hormuzsundet", "bränsle", "energikris",  # sv
    ),
    # Gates the weapons industry: an order-book story needs a defence subject, not just any conflict
    # word buried in a sports report.
    "DEFENCE": (
        "war", "conflict", "attack", "military", "missile", "defence", "defense", "troops",
        "invasion", "rearmament", "weapon", "nato", "arms deal", "air defence",
        "krig", "militär", "försvar", "vapen", "upprustning", "luftvärn",  # sv
    ),
}

# Institutional and market-context terms that corroborate a generic keyword WITHOUT selecting any
# asset. Kept separate from FALLBACK_CUES because the two answer different questions: a cue family
# says "this article is about gold / oil / defence, so those assets may be selected", while a
# corroboration term only says "this is market news, so a generic keyword in the headline can be
# trusted". "UK chancellor delivers annual budget" is the case that needs this: "budget" is generic,
# no company or industry is named, no money figure appears, and no cue family applies — yet it is
# plainly fiscal news.
CORROBORATION_TERMS: tuple[str, ...] = (
    "chancellor", "finance minister", "treasury", "ministry of finance", "central bank",
    "federal reserve", "ecb", "imf", "world bank", "budget deficit", "deficit", "parliament",
    "congress", "regulator", "stock market", "shares", "shareholder", "investor", "bond market",
    "finansminister", "riksbanken", "regeringen", "statsbudget", "börsen", "aktie",  # sv
)

# Cues that must also match INSIDE a compound, not only as a whole word. Swedish compounds glue the
# noun to its qualifier — "luftkriget" (the air war) contains "krig" with no word boundary, and
# ``_keyword_present``'s suffix tolerance cannot see it. Restricted to stems long and specific
# enough that a substring match is safe; a short cue like "tull" stays word-bounded.
COMPOUND_CUES: frozenset[str] = frozenset(
    {
        "krig", "guld", "inflation", "ränta", "försvar", "vapen", "luftvärn", "militär",
        "handelskrig", "sanktion", "olja", "oljepris",
    }
)

# Which cue family each fallback target requires. Every target in EVENT_TYPE_ASSETS and
# EVENT_TYPE_GROUPS must appear here; ``_validate_fallback_targets`` enforces that at import so a
# new fallback entry cannot silently bypass the gate.
FALLBACK_TARGET_FAMILY: dict[str, str] = {
    "NEM_NYSE": "SAFE_HAVEN",
    "PRECIOUS_METALS": "SAFE_HAVEN",
    "XOM_NYSE": "ENERGY",
    "OIL_GAS": "ENERGY",
    "WEAPON_INDUSTRY": "DEFENCE",
}

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


def classify_polarity(text: str, event_type: EventType | None = None) -> EventPolarity:
    """Return RESOLUTION when a de-escalation/negation cue is present, else OCCURRENCE.

    ``event_type`` enables factor-specific inversion. It is optional so a caller with only text (and
    the existing tests) can omit it; omitting it keeps the original global-cue behaviour.
    """
    haystack = f" {_normalise(text.lower())} "
    if event_type is not None and _is_favourable_regulatory(haystack, event_type):
        return EventPolarity.RESOLUTION
    # Runs before the cue loop: a de-escalation that is ending is not a resolution, and several
    # RESOLUTION_CUES ("ceasefire", "truce", "deal", "agreement") name the thing rather than the
    # outcome, so they match either way. See RESOLUTION_NEGATION_CUES.
    if any(_cue_present(haystack, cue) for cue in RESOLUTION_NEGATION_CUES):
        return EventPolarity.OCCURRENCE
    if event_type is not None:
        return _event_relative_polarity(text, event_type)
    for cue in RESOLUTION_CUES:
        if _cue_present(haystack, cue):
            return EventPolarity.RESOLUTION
    return EventPolarity.OCCURRENCE


def _event_relative_polarity(text: str, kind: EventType) -> EventPolarity:
    lower = _normalise(text.casefold())
    if safety_reason(text) or re.search(r"\b(?:expected|might|may|väntas|befaras)\b", lower):
        return EventPolarity.OCCURRENCE
    if kind in {EventType.MILITARY_CONFLICT, EventType.GEOPOLITICAL_TENSION} and re.search(
        r"\b(?:ceasefire|truce|de escalation|vapenvila|eldupphör|fredsavtal)\b", lower
    ):
        return EventPolarity.RESOLUTION
    if kind == EventType.STRAIT_CLOSURE and re.search(r"\b(?:reopen\w*|återöppna\w*)\b", lower):
        return EventPolarity.RESOLUTION
    if kind == EventType.SANCTIONS and re.search(
        r"\b(?:lifts?|lifted|häver|hävda)\b.{0,30}\b(?:sanction\w*|sanktion\w*)\b", lower
    ):
        return EventPolarity.RESOLUTION
    cancellation = r"(?:calls? off|called off|cancel\w*|avert\w*|avbryter|ställer in|blåser av)"
    modifiers = r"(?:\s+(?:the|a|an|planned|proposed|military|iran|new|den|det|planerade)){0,3}"
    for keyword, event_type in ACTION_TAXONOMY.items():
        if event_type == kind and re.search(
            r"\b" + cancellation + modifiers + r"\s+" + re.escape(keyword) + r"\w*\b", lower
        ):
            return EventPolarity.RESOLUTION
    return EventPolarity.OCCURRENCE


def _is_favourable_regulatory(haystack: str, event_type: EventType) -> bool:
    """True when a REGULATORY_ACTION article describes an approval, not enforcement.

    ``REGULATORY_ACTION`` conflates two opposite events. Its keywords cover both approvals
    ("approved", "cleared", "godkänd") and enforcement ("fined", "penalty", "böter"), and the seed
    gives the factor a single DOWN prior because enforcement is the more common case
    (infra/neo4j/init/07). So "FDA approves AstraZeneca's new drug" predicted AZN_STO DOWN 0.35,
    the same call as a fine — systematically wrong on drug approvals, the most reliably positive
    regulatory news there is.

    Rather than sign the factor one way and be wrong half the time, an approval is reported as
    RESOLUTION, which negates the factor's stored direction at decision time (see
    ``decision._effective_direction``) and turns the enforcement-shaped DOWN into an UP.

    Scoped to this one event type on purpose. These cues cannot be added to ``RESOLUTION_CUES``,
    which is global: "Merger approved" would then flip CORPORATE_ACQUISITION's UP prior to DOWN.

    The cleaner long-term model is to split the type into approval and enforcement factors, each
    with
    its own sign and learnable weight; that is a taxonomy version bump and a message-contract
    change,
    so it is recorded in REF-01 as the intended direction rather than done here.
    """
    if event_type is not EventType.REGULATORY_ACTION:
        return False
    if any(_cue_present(haystack, cue) for cue in REGULATORY_ENFORCEMENT_CUES):
        # Both readings present ("approval withdrawn after fine"). Enforcement wins: the
        # conservative choice keeps the factor's stored DOWN sign rather than asserting an upside.
        return False
    return any(_cue_present(haystack, cue) for cue in REGULATORY_APPROVAL_CUES)


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

    This is the raw table lookup and is deliberately **ungated**: it answers "what could this event
    type move?". Scope resolution uses :func:`gated_assets_for_event_type`, which additionally
    requires the article to be about that domain.
    """
    found: list[AssetId] = list(EVENT_TYPE_ASSETS.get(event_type, ()))
    for group_id in EVENT_TYPE_GROUPS.get(event_type, ()):
        for member in members_of(group_id):
            asset = AssetId(member)
            if asset not in found:
                found.append(asset)
    return tuple(found)


def cue_families_present(text: str) -> frozenset[str]:
    """The ``FALLBACK_CUES`` families evidenced by ``text``.

    Whole-word (suffix-tolerant) matching for every cue, plus substring matching for the
    ``COMPOUND_CUES`` stems so Swedish compounds like "luftkriget" register their stem "krig".
    """
    haystack = _haystack(text)
    families: set[str] = set()
    for family, cues in FALLBACK_CUES.items():
        for cue in cues:
            if _keyword_present(haystack, cue) or (cue in COMPOUND_CUES and cue in haystack):
                families.add(family)
                break
    return frozenset(families)


def gated_assets_for_event_type(
    event_type: EventType, text: str
) -> tuple[tuple[AssetId, ...], tuple[str, ...]]:
    """Fallback assets for an event type, restricted to targets whose cue family ``text`` evidences.

    Returns the assets and the cue families that admitted them, so a prediction's provenance can say
    why the fallback fired. An event type with no evidenced family resolves to nothing, which makes
    Prediction drop the event.
    """
    if safety_reason(text):
        return (), ()
    families = cue_families_present(text)
    if not families:
        return (), ()

    found: list[AssetId] = []
    admitted: set[str] = set()

    for asset in EVENT_TYPE_ASSETS.get(event_type, ()):
        family = FALLBACK_TARGET_FAMILY.get(asset.value)
        if family in families:
            found.append(asset)
            admitted.add(family)

    for group_id in EVENT_TYPE_GROUPS.get(event_type, ()):
        family = FALLBACK_TARGET_FAMILY.get(group_id)
        if family not in families:
            continue
        admitted.add(family)
        for member in members_of(group_id):
            asset = AssetId(member)
            if asset not in found:
                found.append(asset)

    return tuple(found), tuple(sorted(admitted))


def _validate_fallback_targets() -> None:
    """Fail at import when a fallback target has no cue family.

    An unmapped target would be silently excluded by ``gated_assets_for_event_type``, so a new entry
    in EVENT_TYPE_ASSETS or EVENT_TYPE_GROUPS would stop predicting with no error anywhere. Better
    to
    refuse to load.
    """
    targets = {asset.value for assets in EVENT_TYPE_ASSETS.values() for asset in assets}
    targets |= {group for groups in EVENT_TYPE_GROUPS.values() for group in groups}
    missing = sorted(targets - set(FALLBACK_TARGET_FAMILY))
    if missing:
        raise RuntimeError(
            "event-type fallback targets have no FALLBACK_TARGET_FAMILY entry: "
            f"{', '.join(missing)}"
        )


def _validate_section_types_are_non_financial() -> None:
    """Fail at import if a publisher section could select a market event type (CLN-71).

    Tier 0 runs ahead of every evidence tier, so an entry such as ``("www.di.se", "bors"):
    RATE_DECISION`` would let a URL path manufacture a prediction with no evidence from the article at
    all. A section may reject an article; it may never classify one. Refusing to load is better than
    discovering this from a prediction.
    """
    offenders = sorted(
        event_type.value
        for event_type in mapped_section_types()
        if event_type not in NON_FINANCIAL_EVENT_TYPES
    )
    if offenders:
        raise RuntimeError(
            "publisher sections may only map to non-financial event types; found: "
            f"{', '.join(offenders)}"
        )


_validate_fallback_targets()
_validate_section_types_are_non_financial()
