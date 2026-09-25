#!/usr/bin/env python3
"""
EU Software / Data / Backend Engineer job hunter.

Queries EURES, Adzuna, Arbeitnow, Remotive, The Muse, Landing.jobs, optional
Jooble, an optional HN "Who is hiring" thread, and optional remote boards
(RemoteOK, Jobicy, We Work Remotely) across the EU/EEA + UK + CH, normalizes
results into a common schema, classifies the actual title into Software, Data,
or Backend, keeps Junior + Mid+ by default (with unclear/mixed review flags),
excludes internships, applies a freshness window, flags relocation/visa
sponsorship, scores each posting against the master profile (match score,
reasons, gaps), dedupes against previous runs, and writes a Markdown report
(optionally also sending it as formatted HTML to a Telegram bot/channel).
Stdlib only — no pip install required; the profile scorer is colocated.

Adzuna needs a free API key (https://developer.adzuna.com): set
ADZUNA_APP_ID / ADZUNA_APP_KEY env vars, or pass --adzuna-app-id /
--adzuna-app-key. Adzuna is skipped gracefully if no key is given.

The EURES call hits an unofficial public endpoint (see
../references/sources.md) — if it starts failing, the script logs a
warning and continues with the other sources rather than aborting.

Usage:
    python job_search.py --weekly
    python job_search.py --levels junior,mid_plus,unclear,mixed --max-age-days 14 --weekly
    python job_search.py --countries de,nl,fr,se,be,at,ie,es,gb,ch --weekly
    TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=@channel python job_search.py --weekly
    python job_search.py --help
"""

from __future__ import annotations

import argparse
import contextlib
import email.utils
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

try:
    import master_profile
except ImportError:  # pragma: no cover - module is colocated in scripts/
    master_profile = None

TIMEOUT = 20
USER_AGENT = "eu-software-data-backend-job-hunter/3.0 (personal job search tool)"
SKILL_ROOT = Path(__file__).resolve().parent.parent

HUB_COUNTRIES = {
    "de", "at", "ch", "gb", "nl", "be", "lu",  # DACH + UK + Benelux
    "se", "dk", "fi", "no", "is",              # Nordics
    "fr", "ie", "es",                          # Western Europe
}

COUNTRY_NAME_TO_CODE = {
    "austria": "at", "belgium": "be", "bulgaria": "bg", "croatia": "hr",
    "cyprus": "cy", "czechia": "cz", "czech republic": "cz", "denmark": "dk",
    "estonia": "ee", "finland": "fi", "france": "fr", "germany": "de",
    "deutschland": "de", "greece": "gr", "hungary": "hu", "ireland": "ie",
    "iceland": "is", "italy": "it", "italia": "it", "latvia": "lv",
    "liechtenstein": "li", "lithuania": "lt", "luxembourg": "lu", "malta": "mt",
    "netherlands": "nl", "nederland": "nl", "norway": "no", "poland": "pl",
    "polska": "pl", "portugal": "pt", "romania": "ro", "slovakia": "sk",
    "slovenia": "si", "spain": "es", "españa": "es", "sweden": "se",
    "sverige": "se", "switzerland": "ch", "schweiz": "ch", "suisse": "ch",
    "united kingdom": "gb", "uk": "gb", "england": "gb", "scotland": "gb",
    "wales": "gb", "great britain": "gb",
}

# EU/EEA members EURES can search (GB and CH are outside EURES' index).
EURES_COUNTRIES = {
    "at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de", "gr",
    "hu", "ie", "is", "it", "lv", "li", "lt", "lu", "mt", "nl", "no", "pl",
    "pt", "ro", "sk", "si", "es", "se",
}

# Full scope: EU/EEA + UK + Switzerland (English-friendly tech hubs).
DEFAULT_COUNTRIES = [
    "at", "be", "bg", "hr", "cy", "cz", "dk", "ee", "fi", "fr", "de", "gr",
    "hu", "ie", "is", "it", "lv", "li", "lt", "lu", "mt", "nl", "no", "pl",
    "pt", "ro", "sk", "si", "es", "se", "gb", "ch",
]

ROLE_TRACKS = {
    "data_engineer": ("data engineer",),
    "backend_engineer": ("backend engineer", "backend developer"),
    "software_engineer": ("software engineer",),
}

ROLE_TRACK_LABELS = {
    "data_engineer": "Data Engineer",
    "backend_engineer": "Backend Engineer",
    "software_engineer": "Software Engineer",
}

# Explicit title matches win before a description is considered. This prevents a
# data search from labelling a backend role as Data Engineer merely because its
# description mentions data.
ROLE_CORE_TITLE_PATTERNS = {
    "data_engineer": [
        r"\bdata[\s-]+engineer(?:ing)?\b",
        r"\bdata[\s-]+platform[\s-]+engineer\b",
        r"\bdata[\s-]+warehouse[\s-]+engineer\b",
        r"\betl[\s-]+(?:engineer|developer)\b",
        r"\bdatabase[\s-]+engineer\b",
        r"\bbig[\s-]+data[\s-]+engineer\b",
    ],
    "backend_engineer": [
        r"\bback[\s-]?end[\s-]+(?:engineer|developer)\b",
        r"\bsoftware[\s-]+engineer(?:ing)?[\s,()\-]+backend\b",
        r"\bbackend[\s-]+software[\s-]+engineer\b",
        r"\bapi[\s-]+engineer\b",
    ],
    "software_engineer": [
        r"\bsoftware[\s-]+engineer(?:ing)?\b",
        r"\bsoftware[\s-]+developer\b",
        r"\bapplication[\s-]+developer\b",
        r"\bfull[\s-]?stack[\s-]+(?:engineer|developer)\b",
    ],
}

ROLE_BORDERLINE_TITLE_PATTERNS = [
    r"\banalytics[\s-]+engineer\b",
    r"\b(?:ml|machine[\s-]+learning|mlops|ai)[\s-]+engineer\b",
    r"\bsite[\s-]+reliability[\s-]+engineer\b",
    r"\bplatform[\s-]+engineer\b",
    r"\bdata[\s-]+infrastructure[\s-]+engineer\b",
]

# These titles are not software/data/backend engineering roles even when a
# description happens to contain the words "data" or "platform".
ROLE_EXCLUDE_TITLE_RE = re.compile(
    r"\b(front[\s-]?end|ui|ux|mobile|android|ios|qa|quality[\s-]+assurance|"
    r"test(?:ing)?[\s-]+engineer|data[\s-]+analyst|business[\s-]+intelligence|"
    r"bi[\s-]+analyst|data[\s-]+scientist|product[\s-]+manager|community[\s-]+manager)\b",
    re.IGNORECASE,
)

BACKEND_CONTEXT_RE = re.compile(
    r"\b(back[\s-]?end|apis?|rest|graphql|microservices?|distributed|event[\s-]?driven|"
    r"kafka|message[\s-]+queue|postgres(?:ql)?|redis|database|java|python|golang|go\b|"
    r"node\.?js|typescript|services?)\b", re.IGNORECASE,
)
BACKEND_STRONG_RE = re.compile(
    r"\b(back[\s-]?end|apis?|rest|graphql|microservices?|distributed|event[\s-]?driven|"
    r"kafka|message[\s-]+queue|postgres(?:ql)?|redis|database|java|python|golang|go\b|"
    r"node\.?js|typescript|services?)\b", re.IGNORECASE,
)
DATA_CONTEXT_RE = re.compile(
    r"\b(data[\s-]+engineering|data[\s-]+platform|data[\s-]+pipeline|pipeline|"
    r"etl|elt|warehouse|lakehouse|spark|databricks|delta[\s-]+lake|dbt|sql|"
    r"airflow|kafka|event[\s-]?driven|analytics[\s-]+engineering)\b", re.IGNORECASE,
)
SOFTWARE_CONTEXT_RE = re.compile(
    r"\b(software|application|service|api|backend|data|platform|infrastructure|"
    r"distributed|systems|cloud|docker|kubernetes)\b", re.IGNORECASE,
)
FRONTEND_CONTEXT_RE = re.compile(
    r"\b(front[\s-]?end|react|angular|vue|svelte|css|html|ui|ux)\b", re.IGNORECASE,
)
MOBILE_CONTEXT_RE = re.compile(
    r"\b(android|ios|mobile|iphone|ipad|kotlin|swift)\b", re.IGNORECASE,
)

SENIORITY_INTERNSHIP_TERMS = [
    r"\bintern(?:ship)?\b", r"\btrainee\b", r"\bworking[\s-]+student\b",
    r"\bapprentice(?:ship)?\b", r"\bwerkstudent\b", r"\bpraktikant(?:in)?\b",
    r"\bpraktikum\b", r"\bausbildung\b", r"\bstagiaire\b", r"\balternance\b",
    r"\bbecari[oa]\b", r"\bpr[aá]cticas?\b", r"\btirocinante\b",
    r"\bneolaureat[oi]\b", r"\bthesis\b", r"\bwerkstudenten\b",
]
SENIORITY_JUNIOR_TERMS = [
    r"\bjunior\b", r"\bjr\.?\b", r"\bentry[\s-]?level\b", r"\bgraduate\b",
    r"\bnew[\s-]+grad\b", r"\bberufseinsteiger\b", r"\bd[ée]butant\b",
    r"\b0[\s-]?(?:to|-)?\s?[12]\s?years?\b",
    r"\b(?:one|1)[\s-]+year\b", r"\bno[\s-]+experience[\s-]+required\b",
]
SENIORITY_MID_TERMS = [
    r"\bmid(?:\b|[\s-]+level\b)", r"\bintermediate\b", r"\bconfirmed\b",
    r"\b(?:2|3|4|5|6|7|8|9)\+?\s?years?\b",
    r"\bengineer\s*(ii|iii|iv)\b",
]
SENIORITY_SENIOR_TERMS = [
    r"\bsenior\b", r"\bsr\.?\b", r"\bstaff\b", r"\bprincipal\b",
]
SENIORITY_LEAD_TERMS = [
    r"\blead\b", r"\btech[\s-]+lead\b", r"\bhead[\s-]+of\b",
    r"\barchitect\b", r"\bengineering[\s-]+manager\b",
]
EXPERIENCE_JUNIOR_RE = re.compile(
    r"\b0\s*(?:[-–—]|\bto\b)\s*[12]\s*(?:years?|yrs?)\b", re.IGNORECASE
)
EXPERIENCE_MID_RE = re.compile(
    r"(?:minimum|at\s+least|requires?|experience\s*[:of]+\s*)?"
    r"\b[2-9]\+?\s*(?:years?|yrs?)\b", re.IGNORECASE
)
DESCRIPTION_SENIOR_RE = re.compile(
    r"\b(?:senior|sr\.?|staff|principal)\s+(?:software|data|backend|platform|"
    r"site|ml|ai|devops|full[\s-]?stack)\s+(?:engineer|developer|specialist|manager)\b",
    re.IGNORECASE,
)
DESCRIPTION_JUNIOR_RE = re.compile(
    r"\b(?:junior|jr\.?|entry[\s-]level|graduate)\s+(?:software|data|backend|"
    r"platform|site|ml|ai|devops|full[\s-]?stack)?\s*(?:engineer|developer)?\b",
    re.IGNORECASE,
)
DESCRIPTION_MID_RE = re.compile(
    r"\b(?:mid[\s-]level|intermediate|confirmed)\s+(?:software|data|backend|"
    r"platform|site|ml|ai|devops|full[\s-]?stack)?\s*(?:engineer|developer)?\b",
    re.IGNORECASE,
)
DESCRIPTION_LEAD_RE = re.compile(
    r"\b(?:lead|principal|head\s+of|architect|engineering\s+manager)\s+"
    r"(?:software|data|backend|platform|site|ml|ai|devops|full[\s-]?stack)?\s*"
    r"(?:engineer|developer|manager)?\b", re.IGNORECASE
)
DESCRIPTION_INTERNSHIP_RE = re.compile(
    r"\b(?:intern|internship|trainee|working\s+student|apprentice)\s+"
    r"(?:engineer|developer|role|position|opportunity)\b|"
    r"\b(?:as|apply|join)\s+(?:an?\s+)?(?:intern|trainee)\b",
    re.IGNORECASE,
)
DESCRIPTION_ENTRY_RE = re.compile(
    r"\b(?:no\s+experience\s+required|entry[\s-]level|0\s*[-–]\s*2\s+years?)\b",
    re.IGNORECASE,
)

# Default includes real junior and mid+ roles plus ambiguous postings that are
# useful for review. Internships/trainees are excluded unless explicitly added.
DEFAULT_ALLOWED_SENIORITY = frozenset({"junior", "mid_plus", "unclear", "mixed"})
SENIORITY_LABELS = {
    "junior": "Junior",
    "mid_plus": "Mid+",
    "unclear": "Unclear",
    "mixed": "Mixed level",
    "internship": "Internship / trainee",
}
DEFAULT_MAX_PER_TRACK = 40

# Adzuna country indexes that are known to exist. Unsupported indexes are
# skipped rather than producing noisy 404 warnings on every run.
ADZUNA_COUNTRIES = frozenset({
    # Verified indexes for the current Adzuna API. Other EU countries are
    # still covered by EURES/Arbeitnow/remote boards.
    "at", "be", "ch", "de", "es", "fr", "gb", "it", "nl", "pl",
})

# Signals that the employer supports moving to the role (relocation package,
# visa/work-permit sponsorship). A flag to verify — presence means "mention",
# not a guarantee.
RELOCATION_TERMS = [
    r"\brelocat\w+\b", r"\brelo\b",
    r"\bvisa\s*sponsor\w+\b", r"\bwork\s*visa\b", r"\bwork\s*permit\b",
    r"\bwork\s*authorization\b", r"\bsponsor(ship|ing|ed)?\b",
    r"\bumzug(s)?\b", r"\bvisa assistance\b",
]
RELOCATION_RE = re.compile("|".join(RELOCATION_TERMS), re.IGNORECASE)

# Remote roles that name a region far outside the EU/EEA are rejected unless
# the posting explicitly says it is open to Europe/EMEA/EU candidates.
FAR_REGION_RE = re.compile(
    r"\b(usa|united states|canada|latin america|latam|apac|asia pacific|"
    r"india|africa|australia|new zealand|mexico|brazil|south america|"
    r"philippines|indonesia|singapore|malaysia|thailand|vietnam|japan|"
    r"korea|dubai|uae)\b", re.IGNORECASE,
)
EU_HINT_RE = re.compile(
    r"\b(europe|emea|european\s+union|eu\b|remote\s*-\s*eu|switzerland|"
    r"united kingdom|uk\b|germany|france|netherlands|spain|portugal|poland|"
    r"ireland|italy|austria|belgium|sweden|denmark|finland|norway|estonia|"
    r"lithuania|czech|czechia|croatia|hungary|romania|bulgaria|greece|"
    r"luxembourg|malta|latvia|slovenia|slovakia)\b",
    re.IGNORECASE,
)
EU_REGION_RE = re.compile(
    r"\b(europe|emea|european\s+union|eu\b|eu\s+time\s*zones?|remote\s*-\s*eu)\b",
    re.IGNORECASE,
)
EU_ELIGIBILITY_RE = re.compile(
    r"(?:open|eligible|remote|across|within|candidates?)[^.\n]{0,60}"
    r"\b(europe|emea|eu|european\s+union|eu\s+time\s*zones?)\b|"
    r"\b(europe|emea|eu|european\s+union|eu\s+time\s*zones?)\b[^.\n]{0,60}"
    r"(?:open|eligible|remote|across|within|candidates?)",
    re.IGNORECASE,
)
ELIGIBLE_LOCATION_RE = re.compile(
    r"(?:open\s+to|eligible\s+(?:candidates\s+)?(?:in|from)|"
    r"remote\s+(?:from|across|within)|available\s+to)\s+"
    r"(?:candidates\s+)?(?:in\s+|from\s+|across\s+|within\s+)?"
    r"[^.;\n]{0,100}",
    re.IGNORECASE,
)
WORLDWIDE_RE = re.compile(
    r"\b(anywhere|worldwide|global|international)\b", re.IGNORECASE,
)


def _remote_eu_ok(location: str, snippet: str = "") -> bool:
    """Return whether a remote posting has an explicit EU/EEA eligibility hint.

    A location restriction wins over words in the description: a US-only role
    is not rescued by a sentence mentioning a German office. Bare Remote,
    Anywhere, Worldwide, and Global labels require an explicit eligibility
    phrase such as "open to candidates across Europe".
    """
    location = (location or "").strip()
    if FAR_REGION_RE.search(location) and not EU_REGION_RE.search(location) \
            and not (_mentioned_countries(location) & set(DEFAULT_COUNTRIES)):
        return False
    if EU_REGION_RE.search(location):
        return True
    if _mentioned_countries(location) & set(DEFAULT_COUNTRIES):
        return True
    if _remote_eligible_countries(location, snippet):
        return True
    return False


def _mentioned_countries(*values) -> set[str]:
    text = " ".join(str(value or "") for value in values).lower()
    return {
        code for name, code in COUNTRY_NAME_TO_CODE.items()
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text)
    }


def _mentioned_country(*values) -> str | None:
    codes = sorted(_mentioned_countries(*values))
    return codes[0] if codes else None


def _remote_eligible_countries(location: str, description: str) -> set[str]:
    """Extract only countries tied to an explicit remote eligibility phrase."""
    location_codes = _mentioned_countries(location)
    if location_codes:
        return location_codes
    if EU_REGION_RE.search(location):
        return set(DEFAULT_COUNTRIES)
    codes: set[str] = set()
    text = description or ""
    for match in ELIGIBLE_LOCATION_RE.finditer(text):
        window = match.group(0)
        codes.update(_mentioned_countries(window))
        if EU_ELIGIBILITY_RE.search(window):
            return set(DEFAULT_COUNTRIES)
    return codes


def _country_codes(item: dict) -> set[str]:
    explicit = (item.get("country") or "").strip().lower()
    if explicit:
        return {explicit}
    description = item.get("description") or item.get("snippet") or ""
    if item.get("remote"):
        return _remote_eligible_countries(item.get("location", ""), description)
    codes = _mentioned_countries(item.get("location"))
    if codes:
        return codes
    url = str(item.get("url") or "").lower()
    if ".co.uk" in url:
        codes.add("gb")
    for code in ("at", "be", "ch", "de", "es", "fr", "it", "nl", "pl", "pt"):
        if f".{code}/" in url or f"/{code}/" in url:
            codes.add(code)
    if codes:
        return codes
    return _mentioned_countries(description)


def filter_scope(items: list[dict], countries: list[str]) -> list[dict]:
    """Apply the requested country scope to every source, including shared feeds."""
    target = {country.lower() for country in countries}
    full_scope = target.issuperset(set(DEFAULT_COUNTRIES))
    kept = []
    for item in items:
        location = item.get("location", "")
        description = item.get("description") or item.get("snippet") or ""
        if FAR_REGION_RE.search(location) \
                and not EU_REGION_RE.search(location) \
                and not (_mentioned_countries(location) & set(DEFAULT_COUNTRIES)):
            continue
        codes = _country_codes(item)
        if codes:
            if not (codes & target):
                continue
            # Preserve all eligible countries for multi-country/Europe-wide
            # roles instead of assigning an arbitrary first country.
            item["eligible_countries"] = ",".join(sorted(codes))
            item["country"] = next(iter(codes)) if len(codes) == 1 else ""
            kept.append(item)
            continue
        if item.get("remote") and _remote_eu_ok(location, description):
            if full_scope:
                item["eligible_countries"] = ",".join(sorted(DEFAULT_COUNTRIES))
                kept.append(item)
            continue
        # A non-remote item with no country or explicit EU hint is too
        # ambiguous for a targeted digest.
        if full_scope and EU_HINT_RE.search(f"{location} {description}"):
            kept.append(item)
    return kept


def _compile(patterns):
    return [re.compile(p, re.IGNORECASE) for p in patterns]


ROLE_CORE_TITLE_RE = {
    track: _compile(patterns) for track, patterns in ROLE_CORE_TITLE_PATTERNS.items()
}
ROLE_BORDERLINE_TITLE_RE = _compile(ROLE_BORDERLINE_TITLE_PATTERNS)
SEN_INTERNSHIP_RE = _compile(SENIORITY_INTERNSHIP_TERMS)
SEN_JUNIOR_RE = _compile(SENIORITY_JUNIOR_TERMS)
SEN_MID_RE = _compile(SENIORITY_MID_TERMS)
SEN_SENIOR_RE = _compile(SENIORITY_SENIOR_TERMS)
SEN_LEAD_RE = _compile(SENIORITY_LEAD_TERMS)


def classify_track(title: str, snippet: str = "") -> tuple[str, str] | None:
    """Classify a posting as (track, fit) using the title before the snippet.

    Explicit Data/Backend titles take precedence over a data search query.
    Generic Software Engineer titles are accepted, but frontend-only and
    non-engineering titles are rejected. Ambiguous platform/analytics/SRE roles
    are accepted only when their description shows engineering work.
    """
    title = re.sub(r"\s+", " ", (title or "").strip())
    snippet = re.sub(r"\s+", " ", (snippet or "").strip())
    if not title or ROLE_EXCLUDE_TITLE_RE.search(title):
        return None

    # Data and Backend titles are explicit. Software titles need context so a
    # generic Software Engineer role is not treated as core before inspection.
    for track in ("data_engineer", "backend_engineer"):
        if any(pattern.search(title) for pattern in ROLE_CORE_TITLE_RE[track]):
            return track, "core"

    software_title = any(pattern.search(title) for pattern in ROLE_CORE_TITLE_RE["software_engineer"])
    if software_title:
        if (FRONTEND_CONTEXT_RE.search(snippet) or MOBILE_CONTEXT_RE.search(snippet)) and not (
            BACKEND_STRONG_RE.search(snippet) or DATA_CONTEXT_RE.search(snippet)
        ):
            return None
        if BACKEND_STRONG_RE.search(snippet) or DATA_CONTEXT_RE.search(snippet):
            return "software_engineer", "core"
        return "software_engineer", "borderline"

    if any(pattern.search(title) for pattern in ROLE_BORDERLINE_TITLE_RE):
        if DATA_CONTEXT_RE.search(snippet):
            return "data_engineer", "borderline"
        if BACKEND_STRONG_RE.search(snippet):
            return "backend_engineer", "borderline"
        if re.search(r"\b(?:site\s+reliability|platform)\s+engineer\b", title, re.IGNORECASE):
            return None
        if SOFTWARE_CONTEXT_RE.search(snippet):
            return "software_engineer", "borderline"
    return None


def classify_role(title: str, snippet: str = "") -> str | None:
    """Backward-compatible role-fit wrapper around :func:`classify_track`."""
    classification = classify_track(title, snippet)
    return classification[1] if classification else None


def classify_seniority(title: str, snippet: str = "") -> str:
    """Return junior, mid_plus, internship, mixed, or unclear.

    Title signals are authoritative. Description signals are accepted only when
    they look like role-level or experience requirements, not incidental words
    such as "senior leadership team".
    """
    title_text = re.sub(r"\s+", " ", (title or "").strip())
    description = re.sub(r"\s+", " ", (snippet or "").strip())
    description_head = description[:700]

    if any(pattern.search(title_text) for pattern in SEN_INTERNSHIP_RE):
        return "internship"
    title_junior = any(pattern.search(title_text) for pattern in SEN_JUNIOR_RE)
    title_mid = any(pattern.search(title_text) for pattern in SEN_MID_RE)
    title_senior = any(pattern.search(title_text) for pattern in SEN_SENIOR_RE)
    title_lead = any(pattern.search(title_text) for pattern in SEN_LEAD_RE)
    title_has_level = title_junior or title_mid or title_senior or title_lead

    if not title_has_level and DESCRIPTION_INTERNSHIP_RE.search(description_head):
        return "internship"

    experience_junior = bool(
        EXPERIENCE_JUNIOR_RE.search(title_text)
        or EXPERIENCE_JUNIOR_RE.search(description_head)
    )
    experience_mid = bool(
        EXPERIENCE_MID_RE.search(title_text)
        or EXPERIENCE_MID_RE.search(description_head)
    )
    if experience_junior:
        title_junior = True
        title_mid = False
        experience_mid = False

    description_junior = (
        DESCRIPTION_JUNIOR_RE.search(description_head)
        or DESCRIPTION_ENTRY_RE.search(description_head)
    )
    description_mid = DESCRIPTION_MID_RE.search(description_head)
    description_senior = DESCRIPTION_SENIOR_RE.search(description_head)
    description_lead = DESCRIPTION_LEAD_RE.search(description_head)

    junior = title_junior or bool(description_junior)
    mid = title_mid or experience_mid or bool(description_mid)
    senior = title_senior or bool(description_senior)
    lead = title_lead or bool(description_lead)
    if junior and (mid or senior or lead):
        return "mixed"
    if junior:
        return "junior"
    if mid or senior or lead:
        return "mid_plus"
    return "unclear"


def http_get_json(url: str, headers: dict | None = None, strict: bool = False):
    """GET JSON with one short retry for transient upstream failures."""
    for attempt in range(2):
        req = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, **(headers or {})}
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in {429, 500, 502, 503, 504} and attempt == 0:
                time.sleep(0.35)
                continue
            if strict:
                raise RuntimeError(f"GET failed: HTTP {e.code}") from e
            print(f"  [warn] GET {url[:80]}... failed: {e}", file=sys.stderr)
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if strict:
                raise RuntimeError(f"GET failed: {e}") from e
            print(f"  [warn] GET {url[:80]}... failed: {e}", file=sys.stderr)
            return None
    if strict:
        raise RuntimeError("GET failed after retry")
    return None


def http_get_text(url: str, headers: dict | None = None, strict: bool = False) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        if strict:
            raise RuntimeError(f"GET text failed: {e}") from e
        print(f"  [warn] GET {url[:80]}... failed: {e}", file=sys.stderr)
        return None


def http_post_json(url: str, body: dict, headers: dict | None = None, strict: bool = False):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        if strict:
            raise RuntimeError(f"POST failed: {e}") from e
        print(f"  [warn] POST {url[:80]}... failed: {e}", file=sys.stderr)
        return None


def make_id(company: str, title: str, location: str, url: str | None = None) -> str:
    # Canonical source URLs survive title/location rewrites across runs.
    raw = _canonical_url(url) if url else (
        f"{company.strip().lower()}|{title.strip().lower()}|{location.strip().lower()}"
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def normalize(*, title, company, location, country, remote, url, source,
              posted, salary, snippet, company_url=None):
    title = re.sub(r"\s+", " ", (title or "").strip())
    snippet_text = re.sub(r"\s+", " ", (snippet or "").strip())
    classification = classify_track(title, snippet_text)
    if classification is None or not title or not url:
        return None
    track, role = classification
    location_text = re.sub(r"\s+", " ", (location or "").strip())
    company_text = re.sub(r"\s+", " ", (company or "").strip()) or "Unknown"
    return {
        "id": make_id(company_text, title, location_text, url),
        "title": title,
        "company": company_text,
        "location": location_text,
        "country": (country or "").strip().lower(),
        "remote": bool(remote),
        "url": url.strip(),
        "canonical_url": _canonical_url(url.strip()),
        "source": source,
        "posted": posted,  # ISO date string or None
        "salary": salary,
        "snippet": snippet_text[:300],
        "description": snippet_text[:5000],
        "company_url": company_url or None,
        "role_fit": role,
        "track": track,
        "_track": track,
        "seniority": classify_seniority(title, snippet_text),
        "relocation": bool(RELOCATION_RE.search(f"{title} {snippet_text}")),
    }


# ---------------------------------------------------------------------------
# Source fetchers — each returns a list of normalized dicts (possibly empty).
# Every fetcher must fail soft: catch its own errors and return [] on trouble.
# ---------------------------------------------------------------------------

def fetch_eures(keyword: str, country_codes: list[str], max_pages: int = 3) -> list[dict]:
    out = []
    for page in range(1, max_pages + 1):
        body = {
            "resultsPerPage": 50,
            "page": page,
            "sortSearch": "MOST_RECENT",
            "keywords": [{"keyword": keyword, "specificSearchCode": "EVERYWHERE"}],
            "locationCodes": country_codes,
            "requiredExperienceCodes": [],
            "publicationPeriod": None,
            "requestLanguage": "en",
        }
        data = http_post_json(
            "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search", body,
            strict=True,
        )
        if data is None:
            raise RuntimeError("EURES request failed")
        # NOTE: this is an unofficial/reverse-engineered endpoint — the exact
        # key holding the results array isn't guaranteed. Try a few common
        # shapes; if none match, dump the top-level keys so a human can fix
        # this quickly rather than silently returning nothing forever.
        records = None
        for key in ("content", "jvs", "results", "jobs", "items"):
            if isinstance(data, dict) and isinstance(data.get(key), list):
                records = data[key]
                break
        if records is None:
            raise RuntimeError(
                "EURES response shape unrecognized: "
                f"{list(data.keys()) if isinstance(data, dict) else type(data)}"
            )
        if not records:
            break
        for r in records:
            item = normalize(
                title=r.get("title") or r.get("positionTitle") or "",
                company=(r.get("employer") or {}).get("name", "") if isinstance(r.get("employer"), dict) else r.get("employerName", ""),
                location=r.get("location", "") or r.get("locationLabel", ""),
                country=r.get("countryCode", ""),
                remote="remote" in json.dumps(r).lower(),
                url=r.get("url") or r.get("applyUrl") or "",
                source="EURES",
                posted=r.get("publicationDate") or r.get("creationDate"),
                salary=None,
                snippet=r.get("description", "") or r.get("shortDescription", ""),
            )
            if item:
                out.append(item)
        if len(records) < 50:
            break
    return out


CURRENCY_SYMBOLS = {
    "EUR": "€", "GBP": "£", "USD": "$", "CHF": "CHF ", "PLN": "zł",
    "SEK": "kr ", "DKK": "kr ", "NOK": "kr ", "CZK": "Kč", "HUF": "Ft",
    "RON": "lei", "BGN": "лв", "HRK": "kn",
}


def _currency_prefix(code: str) -> str:
    if not code:
        return ""
    return CURRENCY_SYMBOLS.get(code.upper(), f"{code} ")


def _adzuna_salary(record: dict) -> str | None:
    minimum = record.get("salary_min")
    maximum = record.get("salary_max")
    if minimum is None and maximum is None:
        return None
    try:
        low = f"{float(minimum):,.0f}" if minimum is not None else "?"
        high = f"{float(maximum):,.0f}" if maximum is not None else "?"
    except (TypeError, ValueError):
        return None
    period = (
        record.get("salary_min_time_period")
        or record.get("salary_max_time_period")
        or record.get("salary_time_period")
        or record.get("salary_period")
    )
    period_label = {
        "HOUR": "/hour", "DAY": "/day", "WEEK": "/week",
        "MONTH": "/month", "YEAR": "/year", "ANNUAL": "/year",
    }.get(str(period).upper(), "")
    return f"{_currency_prefix(record.get('salary_currency'))}{low}-{high}{period_label}"


def fetch_adzuna(keyword: str, country: str, app_id: str, app_key: str,
                  max_days_old: int = 14, max_pages: int = 2) -> list[dict]:
    out = []
    for page in range(1, max_pages + 1):
        params = urllib.parse.urlencode({
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": 50,
            "what": keyword,
            "max_days_old": max_days_old,
            "sort_by": "date",
            "content-type": "application/json",
        })
        url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}?{params}"
        data = http_get_json(url, strict=True)
        if data is None:
            raise RuntimeError("Adzuna request failed")
        if not data.get("results"):
            break
        for r in data["results"]:
            item = normalize(
                title=r.get("title", ""),
                company=(r.get("company") or {}).get("display_name", "Unknown"),
                location=(r.get("location") or {}).get("display_name", ""),
                country=country,
                remote="remote" in (r.get("title", "") + r.get("description", "")).lower(),
                url=r.get("redirect_url", ""),
                source="Adzuna",
                posted=r.get("created"),
                salary=_adzuna_salary(r),
                snippet=r.get("description", ""),
            )
            if item:
                out.append(item)
        if len(data["results"]) < 50:
            break
    return out


def fetch_arbeitnow(keyword: str = "", max_pages: int = 5) -> list[dict]:
    out = []
    url = "https://www.arbeitnow.com/api/job-board-api"
    keyword_fragment = (keyword or "").split()[0].lower() if keyword else ""
    for page in range(1, max_pages + 1):
        page_url = url if page == 1 else f"{url}?page={page}"
        data = http_get_json(page_url, strict=True)
        if not isinstance(data, dict) or "data" not in data:
            raise RuntimeError("Arbeitnow returned an unexpected response")
        if not data["data"]:
            break
        for r in data["data"]:
            title = r.get("title", "")
            snippet = r.get("description", "")
            if keyword_fragment and keyword_fragment not in (title + snippet).lower():
                # Arbeitnow returns its whole board, not a per-keyword search.
                # An empty keyword means "classify every posting on the board".
                if not classify_role(title, snippet):
                    continue
            posted = r.get("created_at")
            posted_iso = None
            if isinstance(posted, (int, float)):
                posted_iso = datetime.fromtimestamp(posted, tz=timezone.utc).date().isoformat()
            item = normalize(
                title=title,
                company=r.get("company_name", "Unknown"),
                location=r.get("location", ""),
                country="",  # Arbeitnow doesn't give a clean country code
                remote=bool(r.get("remote")),
                url=r.get("url", ""),
                source="Arbeitnow",
                posted=posted_iso,
                salary=None,
                snippet=snippet,
                company_url=r.get("company_url") or r.get("company_website"),
            )
            if item:
                out.append(item)
        if not data.get("links", {}).get("next"):
            break
    return out


def fetch_remoteok(keyword: str) -> list[dict]:
    data = http_get_json("https://remoteok.com/api", strict=True)
    if not isinstance(data, list):
        raise RuntimeError("RemoteOK returned an unexpected response")
    out = []
    for r in data[1:]:  # first element is metadata, not a job
        title = r.get("position", "") or r.get("title", "")
        snippet = r.get("description", "")
        item = normalize(
            title=title,
            company=r.get("company", "Unknown"),
            location=r.get("location", "Remote"),
            country="",
            remote=True,
            url=r.get("url", "") or r.get("apply_url", ""),
            source="RemoteOK",
            posted=r.get("date"),
            salary=(f"{r['salary_min']}-{r['salary_max']}"
                    if r.get("salary_min") and r.get("salary_max") else None),
            snippet=snippet,
        )
        if item and _remote_eu_ok(item["location"], item["snippet"]):
            out.append(item)
    return out


def _jobicy_salary(record: dict) -> str | None:
    minimum = record.get("salaryMin")
    maximum = record.get("salaryMax")
    if minimum is None and maximum is None:
        annual = record.get("annualSalary")
        return str(annual) if annual else None
    try:
        low = f"{float(minimum):,.0f}" if minimum is not None else "?"
        high = f"{float(maximum):,.0f}" if maximum is not None else "?"
    except (TypeError, ValueError):
        return None
    currency = _currency_prefix(record.get("salaryCurrency") or "")
    period = str(record.get("salaryPeriod") or "").upper()
    suffix = {"HOURLY": "/hour", "YEARLY": "/year", "ANNUAL": "/year"}.get(period, "")
    return f"{currency}{low}-{high}{suffix}"


def fetch_jobicy() -> list[dict]:
    """Fetch the shared Jobicy feed once and classify tracks locally."""
    data = http_get_json("https://jobicy.com/api/v2/remote-jobs", strict=True)
    if not isinstance(data, dict) or "jobs" not in data:
        raise RuntimeError("Jobicy returned an unexpected response")
    if not data.get("jobs"):
        return []
    out = []
    for r in data["jobs"]:
        title = r.get("jobTitle", "") or r.get("title", "")
        level = r.get("jobLevel")
        if isinstance(level, list):
            level = " ".join(str(value) for value in level)
        if level and str(level).lower() not in title.lower():
            title = f"{level} {title}"
        description = " ".join(
            str(value or "") for value in (
                r.get("jobDescription"), r.get("jobExcerpt"),
            )
        ).strip()
        item = normalize(
            title=title,
            company=r.get("companyName", "Unknown"),
            location=r.get("jobGeo", "Remote"),
            country="",
            remote=True,
            url=r.get("url", "") or r.get("jobUrl", ""),
            source="Jobicy",
            posted=r.get("pubDate") or r.get("publishedDate"),
            salary=_jobicy_salary(r),
            snippet=description,
        )
        if item and _remote_eu_ok(item["location"], item["description"]):
            out.append(item)
    return out


WWR_FEEDS = (
    "remote-back-end-programming-jobs",
    "remote-full-stack-programming-jobs",
    "remote-devops-sysadmin-jobs",
)


def fetch_wwr() -> list[dict]:
    """We Work Remotely — RSS feeds (no API; the feeds are the public feed).
    Titles come back as 'Company: Job Title'; region/country are explicit."""
    out = []
    for slug in WWR_FEEDS:
        text = http_get_text(
            f"https://weworkremotely.com/categories/{slug}.rss", strict=True
        )
        if not text:
            raise RuntimeError("WWR returned an empty feed")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            raise RuntimeError(f"WWR feed {slug} unparseable: {e}") from e
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            company, sep, job_title = title.partition(": ")
            if not sep:
                company, job_title = "", title
            region = (item.findtext("region") or "").strip()
            country = (item.findtext("country") or "").strip()
            location = f"{region} {country}".strip() or "Remote"
            desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
            item_norm = normalize(
                title=job_title,
                company=company or "Unknown",
                location=location,
                country="",
                remote=True,
                url=(item.findtext("link") or "").strip(),
                source="WWR",
                posted=_rfc2822_to_iso(item.findtext("pubDate")),
                salary=None,
                snippet=desc,
            )
            if item_norm and _remote_eu_ok(item_norm["location"], item_norm["snippet"]):
                out.append(item_norm)
    return out


def _rfc2822_to_iso(value):
    if not value:
        return None
    try:
        return email.utils.parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _strip_html(value: str) -> str:
    """Collapse an HTML fragment (job descriptions, HN comments) to plain text."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<br\s*/?>|</p>|</li>|</div>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


_FIRST_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_BARE_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)


def _first_url(raw_html: str) -> str | None:
    match = _FIRST_HREF_RE.search(raw_html or "")
    if match:
        return html.unescape(match.group(1))
    match = _BARE_URL_RE.search(_strip_html(raw_html))
    return match.group(0) if match else None


def fetch_remotive(keyword: str, limit: int = 50) -> list[dict]:
    """Remotive — free public remote-jobs API, filterable by keyword."""
    url = (
        "https://remotive.com/api/remote-jobs?"
        f"limit={max(1, min(limit, 100))}&search={urllib.parse.quote(keyword)}"
    )
    data = http_get_json(url, strict=True)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise RuntimeError("Remotive returned an unexpected response")
    out = []
    for record in data["jobs"]:
        description = _strip_html(record.get("description", ""))
        item = normalize(
            title=record.get("title", ""),
            company=record.get("company_name", "Unknown"),
            location=record.get("candidate_required_location", "") or "Remote",
            country="",
            remote=True,
            url=record.get("url", ""),
            source="Remotive",
            posted=(record.get("publication_date") or "")[:10] or None,
            salary=(record.get("salary") or None) or None,
            snippet=description,
        )
        if item and _remote_eu_ok(item["location"], item.get("description", "")):
            out.append(item)
    return out


_MUSE_LEVEL_PREFIX = {
    "entry level": "Junior ",
    "mid level": "Mid-level ",
    "senior level": "Senior ",
    "management": "Lead ",
}


def _muse_level_prefix(levels) -> str:
    text = " ".join(str(level).lower() for level in (levels or []))
    for key, prefix in _MUSE_LEVEL_PREFIX.items():
        if key in text:
            return prefix
    return ""


def fetch_themuse(categories=("Software Engineering", "Data and Analytics"),
                  max_pages: int = 3) -> list[dict]:
    """The Muse — free public API with structured level and location data."""
    out = []
    for category in categories:
        for page in range(1, max_pages + 1):
            url = (
                "https://www.themuse.com/api/public/jobs?"
                f"page={page}&category={urllib.parse.quote(category)}"
            )
            data = http_get_json(url, strict=True)
            results = data.get("results") if isinstance(data, dict) else None
            if not results:
                break
            for record in results:
                locations = [
                    str(loc.get("name", "")).strip()
                    for loc in (record.get("locations") or [])
                    if isinstance(loc, dict) and loc.get("name")
                ]
                location = ", ".join(locations[:2])
                remote = any(
                    "remote" in loc.lower() or "flexible" in loc.lower()
                    for loc in locations
                )
                levels = [level.get("name") for level in (record.get("levels") or [])
                          if isinstance(level, dict)]
                prefix = _muse_level_prefix(levels)
                title = f"{prefix}{record.get('name', '')}".strip()
                company = (record.get("company") or {}).get("name", "Unknown")
                item = normalize(
                    title=title,
                    company=company,
                    location=location or ("Remote" if remote else ""),
                    country="",
                    remote=remote,
                    url=(record.get("refs") or {}).get("landing_page", "")
                    or record.get("short_name", ""),
                    source="The Muse",
                    posted=(record.get("publication_date") or "")[:10] or None,
                    salary=None,
                    snippet=_strip_html(record.get("contents", "")),
                )
                if item:
                    out.append(item)
            if len(results) < 20:
                break
    return out


def _landing_company(url: str) -> str:
    path = urllib.parse.urlsplit(str(url or "")).path.strip("/")
    parts = [segment for segment in path.split("/") if segment]
    if len(parts) >= 2:
        return parts[-2].replace("-", " ").strip().title()
    return "Unknown"


def _landing_salary(record: dict) -> str | None:
    low = record.get("gross_salary_low")
    high = record.get("gross_salary_high")
    if low is None and high is None:
        return None
    prefix = _currency_prefix(record.get("currency_code") or "")
    try:
        low_label = f"{float(low):,.0f}" if low is not None else "?"
        high_label = f"{float(high):,.0f}" if high is not None else "?"
    except (TypeError, ValueError):
        return None
    return f"{prefix}{low_label}-{high_label}/year"


def fetch_landing(max_pages: int = 2, page_size: int = 50) -> list[dict]:
    """Landing.jobs — EU-focused tech board with a free public JSON API."""
    out = []
    for page in range(max_pages):
        url = f"https://landing.jobs/api/v1/jobs?limit={page_size}&offset={page * page_size}"
        data = http_get_json(url, strict=True)
        if not isinstance(data, list) or not data:
            break
        for record in data:
            locations = record.get("locations") or []
            country = ""
            location_label = ""
            if locations and isinstance(locations[0], dict):
                first = locations[0]
                country = str(first.get("country_code") or "").lower()
                location_label = ", ".join(
                    str(part) for part in (first.get("city"), first.get("country_code")) if part
                )
            description = _strip_html(" ".join(str(record.get(key) or "") for key in (
                "role_description", "main_requirements", "nice_to_have",
            )))
            item = normalize(
                title=record.get("title", ""),
                company=_landing_company(record.get("url", "")),
                location=location_label or country.upper(),
                country=country,
                remote=bool(record.get("remote")),
                url=record.get("url", ""),
                source="Landing.jobs",
                posted=(record.get("published_at") or "")[:10] or None,
                salary=_landing_salary(record),
                snippet=description,
            )
            if item:
                out.append(item)
        if len(data) < page_size:
            break
    return out


def fetch_jooble(keyword: str, api_key: str, location: str = "") -> list[dict]:
    """Jooble — free keyed aggregator API (set JOOBLE_API_KEY in .env)."""
    url = f"https://jooble.org/api/{api_key}"
    data = http_post_json(
        url, {"keywords": keyword, "location": location},
        headers={"Content-Type": "application/json"}, strict=True,
    )
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise RuntimeError("Jooble returned an unexpected response")
    out = []
    for record in data["jobs"]:
        description = _strip_html(record.get("snippet", ""))
        item = normalize(
            title=record.get("title", ""),
            company=record.get("company", "Unknown") or "Unknown",
            location=record.get("location", ""),
            country="",
            remote="remote" in f"{record.get('title','')} {description}".lower(),
            url=record.get("link", ""),
            source="Jooble",
            posted=(record.get("updated") or "")[:10] or None,
            salary=(record.get("salary") or None) or None,
            snippet=description,
        )
        if item:
            out.append(item)
    return out


HN_SEARCH_URL = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=story,author_whoishiring&hitsPerPage=10"
)
HN_ITEM_URL = "https://hn.algolia.com/api/v1/items/{id}"


def _parse_hn_comment(plain: str, raw_html: str, posted: str | None) -> dict | None:
    """Best-effort parse of one 'Who is hiring?' comment.

    The format is free text, so this is deliberately conservative: a comment is
    kept only when (a) its headline classifies as one of our tracks, and
    (b) it names an in-scope country or an EU-eligible remote location.
    """
    headline = plain[:220]
    classification = classify_track(headline, plain)
    if classification is None:
        return None
    url = _first_url(raw_html)
    if not url:
        return None
    # Company is usually the first pipe/dash-separated field.
    company = re.split(r"[|\u2013\u2014]| - | \u2022 ", headline, maxsplit=1)[0]
    company = re.sub(r"\b(is hiring|hiring|YC\s+\w+)\b", "", company, flags=re.IGNORECASE)
    company = company.strip(" .,:-")[:80] or "Unknown"
    codes = _mentioned_countries(headline) | _mentioned_countries(plain[:400])
    remote = "remote" in plain[:400].lower()
    if not codes and not (remote and _remote_eu_ok(headline, plain[:400])):
        return None
    return normalize(
        title=headline[:120],
        company=company,
        location=", ".join(sorted(codes)) or "Remote",
        country="",
        remote=remote,
        url=url,
        source="HN Hiring",
        posted=posted,
        salary=None,
        snippet=plain,
    )


def fetch_hn_hiring(max_comments: int = 250) -> list[dict]:
    """Opt-in: Hacker News 'Ask HN: Who is hiring?' monthly thread (noisy)."""
    search = http_get_json(HN_SEARCH_URL, strict=True)
    hits = search.get("hits") if isinstance(search, dict) else None
    if not hits:
        raise RuntimeError("HN search returned no stories")
    story = next(
        (hit for hit in hits if "who is hiring" in str(hit.get("title", "")).lower()),
        None,
    )
    if not story:
        return []
    item = http_get_json(HN_ITEM_URL.format(id=story["objectID"]), strict=True)
    children = item.get("children", []) if isinstance(item, dict) else []
    out = []
    for child in children[:max_comments]:
        raw = child.get("text") or ""
        if not raw:
            continue
        parsed = _parse_hn_comment(
            _strip_html(raw), raw, (child.get("created_at") or "")[:10] or None
        )
        if parsed:
            out.append(parsed)
    return out


# ---------------------------------------------------------------------------
# State (dedupe across runs) + scoring + report
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def state_lock(path: Path):
    """Serialize CLI runs sharing a state file."""
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        if fcntl is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"another job search is using {path}") from exc
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def load_state(path: Path) -> dict:
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict) and isinstance(data.get("seen", {}), dict):
                data.setdefault("aliases", {})
                if not isinstance(data["aliases"], dict):
                    data["aliases"] = {}
                return data
        except json.JSONDecodeError:
            print(f"  [warn] state file {path} unreadable, starting fresh", file=sys.stderr)
    return {"seen": {}, "aliases": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    temporary.replace(path)


def _posted_date(value) -> date | None:
    if not value:
        return None
    try:
        text = str(value).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text).date()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError, OverflowError):
            return None


def filter_freshness(items: list[dict], max_age_days: int,
                     today: date | None = None) -> list[dict]:
    """Keep recent dated jobs; retain undated jobs because sources vary."""
    if max_age_days <= 0:
        return list(items)
    today = today or datetime.now(timezone.utc).date()
    kept = []
    for item in items:
        posted = _posted_date(item.get("posted"))
        if posted is None or (today - posted).days <= max_age_days:
            kept.append(item)
    return kept


def score(item: dict) -> int:
    s = 0
    country = (item.get("country") or "").lower()
    if country in HUB_COUNTRIES or (item.get("remote") and not country):
        s += 2 if country in HUB_COUNTRIES else 1
    if item.get("remote"):
        s += 1
    posted = _posted_date(item.get("posted"))
    if posted:
        age_days = (datetime.now(timezone.utc).date() - posted).days
        if age_days <= 3:
            s += 2
        elif age_days <= 7:
            s += 1
    if item.get("role_fit") == "core":
        s += 1
    if item.get("seniority") == "mid_plus":
        s += 1
    elif item.get("seniority") == "unclear":
        s -= 1
    if item.get("salary"):
        s += 1
    if item.get("relocation"):
        s += 1
    return s


def rank_key(item: dict):
    """Sort key: an explicit profile match score leads, then the hub/recency score.

    When no profile is configured every item's match_score is absent, so this
    degrades exactly to the pre-profile behavior.
    """
    return (int(item.get("match_score") or 0), score(item))


def _normal_key(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _location_key(value: str) -> str:
    """Normalize city labels enough to merge duplicate board records."""
    text = (value or "").strip()
    if not text:
        return ""
    first = re.split(r"[,/]", text, maxsplit=1)[0].strip()
    key = _normal_key(first)
    aliases = {
        "koln": "cologne", "cologne": "cologne",
        "munchen": "munich", "munich": "munich",
        "wien": "vienna", "vienna": "vienna",
        "zurich": "zurich", "zürich": "zurich",
        "praha": "prague", "prague": "prague",
        "lisboa": "lisbon", "lisbon": "lisbon",
        "roma": "rome", "rome": "rome",
    }
    return aliases.get(key, key)


def _canonical_url(url: str) -> str:
    """Remove tracking parameters so the same job dedupes across sources."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlsplit(url.strip())
        query = [
            (key, value) for key, value in urllib.parse.parse_qsl(
                parsed.query, keep_blank_values=True
            )
            if not key.lower().startswith("utm_")
            and key.lower() not in {"source", "ref", "campaign", "se", "v"}
        ]
        return urllib.parse.urlunsplit((
            parsed.scheme.lower(), parsed.netloc.lower(), parsed.path or "/",
            urllib.parse.urlencode(sorted(query)), "",
        ))
    except ValueError:
        return url.strip().lower()


def _item_completeness(item: dict) -> int:
    return sum(bool(item.get(key)) for key in (
        "title", "company", "location", "country", "posted", "salary",
        "snippet", "description", "company_url", "url",
    ))


def _merge_duplicate(existing: dict, candidate: dict) -> dict:
    """Keep the richer record while retaining every source URL alias."""
    winner = candidate if _item_completeness(candidate) > _item_completeness(existing) else existing
    aliases = set(existing.get("_aliases", []))
    aliases.update(candidate.get("_aliases", []))
    for record in (existing, candidate):
        canonical = record.get("canonical_url") or _canonical_url(record.get("url", ""))
        if canonical:
            aliases.add(canonical)
    winner["_aliases"] = sorted(alias for alias in aliases if alias)
    return winner


def dedupe_within_run(items: list[dict]) -> list[dict]:
    """Dedupe URLs and cross-board duplicates without collapsing requisitions."""
    kept: dict[str, dict] = {}
    fallback: dict[tuple[str, str, str], str] = {}
    for item in items:
        url_key = _canonical_url(item.get("url", ""))
        item.setdefault("_aliases", [url_key] if url_key else [])
        identity = (
            _normal_key(item.get("company", "")),
            _normal_key(item.get("title", "")),
            _location_key(item.get("location", "")),
        )
        existing_key = url_key if url_key and url_key in kept else None
        if existing_key is None:
            candidate_key = fallback.get(identity)
            if candidate_key and kept[candidate_key].get("source") != item.get("source"):
                existing_key = candidate_key
        if not existing_key:
            key = url_key or "|".join(identity)
            kept[key] = item
            fallback.setdefault(identity, key)
            continue
        merged = _merge_duplicate(kept[existing_key], item)
        kept[existing_key] = merged
        fallback[identity] = existing_key
    return list(kept.values())


def filter_seniority(items: list[dict], allowed_levels=None) -> list[dict]:
    """Keep the requested real levels; internships are opt-in via CLI."""
    if allowed_levels is None:
        allowed = set(DEFAULT_ALLOWED_SENIORITY)
    elif isinstance(allowed_levels, str):
        allowed = {part.strip() for part in allowed_levels.split(",") if part.strip()}
    else:
        allowed = set(allowed_levels)
    aliases = {"mid": "mid_plus", "senior": "mid_plus", "lead": "mid_plus"}
    include_internship = "internship" in allowed
    if "all" in allowed:
        allowed = set(DEFAULT_ALLOWED_SENIORITY)
        if include_internship:
            allowed.add("internship")
    else:
        allowed = {aliases.get(level, level) for level in allowed}
    if "*" in allowed:
        return list(items)
    return [item for item in items if item.get("seniority") in allowed]


def apply_state_aliases(items: list[dict], state: dict) -> list[dict]:
    """Map previously seen source URLs to their stable canonical job ID."""
    aliases = state.get("aliases", {})
    for item in items:
        canonical = item.get("canonical_url") or _canonical_url(item.get("url", ""))
        if canonical and canonical in aliases:
            item["id"] = aliases[canonical]
    return items


def record_state_aliases(items: list[dict], state: dict) -> None:
    aliases = state.setdefault("aliases", {})
    for item in items:
        canonical_id = item["id"]
        urls = set(item.get("_aliases", []))
        canonical = item.get("canonical_url") or _canonical_url(item.get("url", ""))
        if canonical:
            urls.add(canonical)
        for url in urls:
            if url:
                aliases[url] = canonical_id


def split_new_vs_seen(items: list[dict], state: dict):
    seen = state.get("seen", {})
    new_items = [it for it in items if it["id"] not in seen]
    return new_items, items  # (new, all-current)


def _md_cell(value) -> str:
    """Make arbitrary source text safe inside a Markdown table cell."""
    text = str(value if value not in (None, "") else "—")
    text = re.sub(r"\s+", " ", text).replace("\\", "\\\\")
    return text.replace("|", "\\|")


def _level_label(level: str) -> str:
    return SENIORITY_LABELS.get(level, level.replace("_", " ").title() or "Unclear")


def _match_label(item: dict) -> str:
    """Traffic-light match-score cell for reports and the Telegram digest."""
    if item.get("match_score") is None:
        return "—"
    try:
        value = int(item["match_score"])
    except (TypeError, ValueError):
        return "—"
    dot = "🟢" if value >= 75 else ("🟡" if value >= 50 else "🔴")
    return f"{dot} {value}"


def _report_breakdown(items: list[dict]) -> str:
    track_counts = {track: 0 for track in ROLE_TRACK_LABELS}
    level_counts: dict[str, int] = {}
    for item in items:
        track = item.get("_track") or item.get("track") or "other"
        if track in track_counts:
            track_counts[track] += 1
        level = item.get("seniority", "unclear")
        level_counts[level] = level_counts.get(level, 0) + 1
    tracks = " · ".join(
        f"{ROLE_TRACK_LABELS[track]} {count}" for track, count in track_counts.items()
    )
    levels = " · ".join(
        f"{_level_label(level)} {count}" for level, count in sorted(level_counts.items())
    )
    return f"Tracks: {tracks or 'none'} | Levels: {levels or 'none'}"


def _source_status_text(sources_status: dict) -> str:
    labels = []
    for name, status in sources_status.items():
        if status is True:
            label = "ok"
        elif status is False or status is None:
            label = "FAILED/SKIPPED"
        else:
            label = str(status)
        labels.append(f"{name} ({label})")
    return ", ".join(labels)


def render_report(new_items: list[dict], total_current: int, sources_status: dict,
                   countries: list[str], weekly: bool,
                   max_per_track: int = DEFAULT_MAX_PER_TRACK,
                   show_match: bool = False) -> str:
    lines = []
    today = datetime.now(timezone.utc).date().isoformat()
    lines.append(f"## EU Software, Data & Backend Engineering Job Search — {today}\n")
    period = "since last run" if weekly else "found"
    lines.append(
        f"**{len(new_items)} new engineering postings {period}** "
        f"({total_current} total currently tracked; Junior + Mid+ by default)\n"
    )
    lines.append(f"_{_report_breakdown(new_items)}_\n")

    by_track = _group_by_track(new_items)
    track_titles = {
        "data_engineer": "Data Engineer",
        "backend_engineer": "Backend Engineer",
        "software_engineer": "Software Engineer",
        "other": "Other / Unclassified",
    }
    for track, label in track_titles.items():
        rows = sorted(by_track.get(track, []), key=rank_key, reverse=True)
        if not rows:
            continue
        hidden_count = max(0, len(rows) - max_per_track) if max_per_track > 0 else 0
        rows = rows[:max_per_track] if max_per_track > 0 else rows
        lines.append(f"### {label}\n")
        if show_match:
            lines.append("| Match | Role | Company | Location | Remote | Reloc | Level | Posted | Salary | Source | Link |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        else:
            lines.append("| Role | Company | Location | Remote | Reloc | Level | Posted | Salary | Source | Link |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for it in rows:
            role_label = it.get("title", "")
            if it.get("role_fit") == "borderline":
                role_label = f"~ {role_label}"
            level = _level_label(it.get("seniority", "unclear"))
            if it.get("seniority") in {"unclear", "mixed"}:
                role_label += " ⚠ verify level"
            remote_label = "Remote" if it.get("remote") else "On-site/Hybrid"
            reloc_label = "✈️" if it.get("relocation") else "—"
            url = str(it.get("url") or "").replace(" ", "%20").replace(")", "%29")
            match_label = _match_label(it) if show_match else ""
            match_cell = f"{match_label} | " if show_match else ""
            lines.append(
                f"| {match_cell}{_md_cell(role_label)} | {_md_cell(it.get('company'))} | "
                f"{_md_cell(it.get('location'))} | {remote_label} | {reloc_label} | "
                f"{level} | {_md_cell(it.get('posted'))} | {_md_cell(it.get('salary'))} | "
                f"{_md_cell(it.get('source'))} | [apply]({url}) |"
            )
        if hidden_count:
            lines.append(f"_{hidden_count} more {label} results are available in the board._")
        lines.append("")

    lines.append("---")
    lines.append(f"Sources checked: {_source_status_text(sources_status)}.")
    lines.append(f"Countries in scope: {', '.join(countries)}.")
    lines.append('"New" = not seen in a prior run; undated postings are retained for review.')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Telegram delivery (optional)
# ---------------------------------------------------------------------------

TELEGRAM_MAX_MSG = 4096

TRACK_EMOJI = {
    "data_engineer": "🛠️ Data Engineer",
    "backend_engineer": "⚙️ Backend Engineer",
    "software_engineer": "💻 Software Engineer",
    "other": "📦 Other / Unclassified",
}


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _group_by_track(items: list[dict]) -> dict[str, list[dict]]:
    by_track: dict[str, list[dict]] = {
        "data_engineer": [], "backend_engineer": [], "software_engineer": [], "other": []
    }
    for it in items:
        by_track.setdefault(it.get("_track") or it.get("track") or "other", []).append(it)
    return by_track


def _chunk_text(text: str, size: int = TELEGRAM_MAX_MSG):
    while len(text) > size:
        split_at = text.rfind("\n", 0, size)
        if split_at < 1:
            split_at = size
        yield text[:split_at]
        text = text[split_at:].lstrip("\n")
    if text:
        yield text


def resolve_chat_id(chat: str) -> str:
    """Accept @username, a t.me channel URL, or a numeric chat id."""
    chat = chat.strip()
    if chat.startswith("https://t.me/"):
        return "@" + chat.rstrip("/").rsplit("/", 1)[-1]
    return chat


def _telegram_send(url: str, chat_id: str, text: str) -> dict | None:
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("ok") is False:
                print(f"  [warn] Telegram send rejected: {payload.get('description', 'unknown error')}",
                      file=sys.stderr)
                return None
            return payload
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        print(f"  [warn] Telegram send failed: {e}", file=sys.stderr)
        return None


def _job_line_html(it: dict) -> str:
    """One clean, scannable bullet per job (Telegram doesn't render tables)."""
    title = it.get("title", "")
    if it.get("role_fit") == "borderline":
        title = "~ " + title
    level = _level_label(it.get("seniority", "unclear"))
    if it.get("seniority") in {"unclear", "mixed"}:
        title += " ⚠️"
    parts = [f"<b>{_esc(title)}</b>", f"({_esc(level)})"]
    if it.get("match_score") is not None:
        parts.insert(0, _match_label(it))
    if it.get("company"):
        parts.append(_esc(it["company"]))
    if it.get("location"):
        parts.append(_esc(it["location"]))
    parts.append("🌍 Remote" if it.get("remote") else "🏢 On-site/Hybrid")
    if it.get("relocation"):
        parts.append("✈️ Relocation")
    if it.get("posted"):
        parts.append(_esc(str(it["posted"])[:10]))
    if it.get("salary"):
        parts.append(f"💰 {_esc(it['salary'])}")
    line = " · ".join(parts)
    if it.get("url"):
        line += f' · <a href="{html.escape(it["url"], quote=True)}">Apply</a>'
    return f"• {line}"


def render_report_telegram(new_items: list[dict], total_current: int,
                           sources_status: dict, countries: list[str],
                           weekly: bool, today: str | None = None,
                           max_per_track: int = DEFAULT_MAX_PER_TRACK,
                           show_match: bool = False) -> str:
    """HTML-formatted version of the report, built for Telegram's message
    engine (bold headers, emoji section markers, clickable apply links)."""
    today = today or datetime.now().date().isoformat()
    lines = [f"<b>🔍 EU Software, Data &amp; Backend Engineering Search — {today}</b>"]
    period = "since last run" if weekly else "found"
    lines.append(f"<b>📊 {len(new_items)} new engineering postings {period}</b> "
                 f"({total_current} total tracked; Junior + Mid+ by default)")
    lines.append(f"<i>{_esc(_report_breakdown(new_items))}</i>")

    by_track = _group_by_track(new_items)
    for track, label in TRACK_EMOJI.items():
        rows = sorted(by_track.get(track, []), key=rank_key, reverse=True)
        if not rows:
            continue
        hidden_count = max(0, len(rows) - max_per_track) if max_per_track > 0 else 0
        rows = rows[:max_per_track] if max_per_track > 0 else rows
        lines.append("")
        lines.append(f"<b>{label}</b>")
        lines.extend(_job_line_html(it) for it in rows)
        if hidden_count:
            lines.append(f"<i>{hidden_count} more results are available in the board.</i>")

    lines.append("")
    lines.append("—")
    lines.append(f"<i>Sources: {_source_status_text(sources_status)}</i>")
    lines.append(f"<i>Countries in scope: {', '.join(countries)}</i>")
    lines.append('<i>"New" = not seen in a prior run.</i>')
    return "\n".join(lines)


def send_telegram(token: str, chat: str, html_report: str) -> bool:
    """Send all report chunks and return True only when every chunk succeeds."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chat_id = resolve_chat_id(chat)
    sent = 0
    failed = 0
    for chunk in _chunk_text(html_report):
        if _telegram_send(url, chat_id, chunk) is None:
            failed += 1
        else:
            sent += 1
    if failed:
        print(f"  [warn] Telegram delivery incomplete: {sent} sent, {failed} failed",
              file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def load_dotenv(path: Path) -> None:
    """Best-effort KEY=VALUE .env loader (setdefault semantics: real env wins)."""
    import os
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def infer_track(title: str, snippet: str = "") -> str | None:
    """Classify keyword-less sources using the same title-first rules."""
    classification = classify_track(title, snippet)
    return classification[0] if classification else None


def run(countries: list[str], state_path: Path, weekly: bool,
        adzuna_app_id: str | None, adzuna_app_key: str | None,
        include_remote_boards: bool = True,
        turso_url: str | None = None, turso_token: str | None = None,
        max_age_days: int = 14, allowed_levels=None,
        max_per_track: int = DEFAULT_MAX_PER_TRACK,
        profile: dict | None = None,
        profile_path: str | None = None,
        jooble_api_key: str | None = None,
        include_hn: bool = False) -> tuple[str, str]:
    state = load_state(state_path)
    if profile is None and master_profile is not None:
        try:
            profile = master_profile.load_profile(profile_path)
        except (OSError, ValueError) as exc:
            print(f"  [warn] could not load profile: {exc}", file=sys.stderr)
            profile = {}
    profile = profile or {}
    use_match = bool(master_profile and master_profile.profile_is_usable(profile))
    if use_match:
        print(f"  [info] personalizing with profile: {profile.get('_path', 'master_profile')}",
              file=sys.stderr)
    all_items: list[dict] = []
    eures_countries = [country for country in countries if country in EURES_COUNTRIES]
    adzuna_countries = [country for country in countries if country in ADZUNA_COUNTRIES]
    sources_status = {
        "EURES": True if eures_countries else "skipped",
        "Arbeitnow": True,
        "Adzuna": True if adzuna_app_id and adzuna_app_key and adzuna_countries else "skipped",
        "Remotive": True,
        "The Muse": True,
        "Landing.jobs": True,
        "Jooble": True if jooble_api_key else "skipped",
    }
    if include_remote_boards:
        sources_status.update({"RemoteOK": True, "Jobicy": True, "WWR": True})
    if include_hn:
        sources_status["HN Hiring"] = True

    def mark_source(name: str, ok: bool) -> None:
        # A source is healthy only if every attempted query succeeds.
        if name in sources_status:
            sources_status[name] = sources_status[name] and ok
        else:
            sources_status[name] = ok

    def source_is_complete(status) -> bool:
        return status is True or status == "skipped"

    def fetch_source(name, fn):
        """Run one source fetch with fail-soft handling."""
        try:
            return fn(), True
        except Exception as e:  # noqa: BLE001 - one dead source shouldn't kill the run
            print(f"  [warn] {name} fetch failed: {e}", file=sys.stderr)
            return [], False

    api_max_age = max_age_days if max_age_days > 0 else 3650

    # Search each track separately, but never let the query keyword overwrite
    # the track inferred from the actual title.
    for track, keywords in ROLE_TRACKS.items():
        for keyword in keywords:
            if eures_countries:
                items, ok = fetch_source(
                    "EURES", lambda k=keyword: fetch_eures(k, eures_countries)
                )
                mark_source("EURES", ok)
                all_items.extend(items)

            if adzuna_app_id and adzuna_app_key:
                for country in adzuna_countries:
                    items, ok = fetch_source(
                        f"Adzuna/{country}",
                        lambda c=country, k=keyword: fetch_adzuna(
                            k, c, adzuna_app_id, adzuna_app_key,
                            max_days_old=api_max_age, max_pages=1,
                        ),
                    )
                    mark_source("Adzuna", ok)
                    all_items.extend(items)

            items, ok = fetch_source(
                "Remotive", lambda k=keyword: fetch_remotive(k)
            )
            mark_source("Remotive", ok)
            all_items.extend(items)

            if jooble_api_key:
                items, ok = fetch_source(
                    "Jooble", lambda k=keyword: fetch_jooble(k, jooble_api_key)
                )
                mark_source("Jooble", ok)
                all_items.extend(items)

    # These boards return a shared feed, so fetch each once and classify locally.
    items, ok = fetch_source("Arbeitnow", lambda: fetch_arbeitnow(""))
    mark_source("Arbeitnow", ok)
    all_items.extend(items)

    items, ok = fetch_source("The Muse", fetch_themuse)
    mark_source("The Muse", ok)
    all_items.extend(items)

    items, ok = fetch_source("Landing.jobs", fetch_landing)
    mark_source("Landing.jobs", ok)
    all_items.extend(items)

    if include_remote_boards:
        items, ok = fetch_source("RemoteOK", lambda: fetch_remoteok(""))
        mark_source("RemoteOK", ok)
        all_items.extend(items)

        items, ok = fetch_source("Jobicy", fetch_jobicy)
        mark_source("Jobicy", ok)
        all_items.extend(items)

        items, ok = fetch_source("WWR", fetch_wwr)
        mark_source("WWR", ok)
        all_items.extend(items)

    if include_hn:
        items, ok = fetch_source("HN Hiring", fetch_hn_hiring)
        mark_source("HN Hiring", ok)
        all_items.extend(items)

    all_items = filter_scope(all_items, countries)
    all_items = filter_freshness(all_items, max_age_days)
    all_items = filter_seniority(all_items, allowed_levels)
    all_items = apply_state_aliases(all_items, state)
    all_items = dedupe_within_run(all_items)

    # Personalize against the master profile: score every posting, then drop
    # hard-vetoed rows (excluded company/keyword) before they reach the report
    # or the database.
    if use_match:
        master_profile.score_items(all_items, profile)
        before = len(all_items)
        all_items = [it for it in all_items if not it.get("excluded")]
        dropped = before - len(all_items)
        if dropped:
            print(f"  [info] {dropped} posting(s) excluded by profile filters",
                  file=sys.stderr)

    new_items, current_items = split_new_vs_seen(all_items, state)

    # Update state only after a complete source snapshot. If a source failed,
    # leave IDs unseen so the next run retries them instead of losing roles.
    snapshot_complete = all(source_is_complete(status) for status in sources_status.values())
    if snapshot_complete:
        record_state_aliases(current_items, state)
        today = datetime.now(timezone.utc).date().isoformat()
        for it in current_items:
            state.setdefault("seen", {}).setdefault(it["id"], today)
        save_state(state_path, state)
    else:
        print("  [warn] state not advanced because one or more sources were incomplete",
              file=sys.stderr)

    # Sync all current postings to Turso (if configured) so the site stays fresh.
    if turso_url and turso_token:
        try:
            import turso as turso_sync
            turso_sync.init_schema(turso_url, turso_token)
            n = turso_sync.upsert_jobs(turso_url, turso_token, current_items)
            if snapshot_complete:
                hidden = turso_sync.deactivate_missing_jobs(
                    turso_url, turso_token, [it["id"] for it in current_items]
                )
            else:
                hidden = 0
                print("  [info] stale-row pruning skipped because a source was unavailable",
                      file=sys.stderr)
            print(f"  [info] synced {n} job postings to Turso "
                  f"({len(current_items)} tracked; {hidden} stale rows hidden)",
                  file=sys.stderr)
        except Exception as e:  # noqa: BLE001 - DB sync shouldn't kill the run
            print(f"  [warn] Turso sync failed: {e}", file=sys.stderr)

    report_md = render_report(
        new_items, len(current_items), sources_status, countries, weekly, max_per_track,
        show_match=use_match,
    )
    report_tg = render_report_telegram(
        new_items, len(current_items), sources_status, countries, weekly,
        max_per_track=max_per_track, show_match=use_match,
    )
    return report_md, report_tg


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--countries", default=",".join(DEFAULT_COUNTRIES),
                         help="Comma-separated ISO country codes (default: EU/EEA + UK + CH)")
    parser.add_argument("--state-file", default=str(SKILL_ROOT / "state" / "seen_jobs.json"),
                         help="Path to persistent dedupe state (must persist between runs!)")
    parser.add_argument("--weekly", action="store_true", help="Frame the report as a weekly digest")
    parser.add_argument("--first-run", dest="weekly", action="store_false",
                         help="Frame the report as a full first-run listing")
    parser.add_argument("--adzuna-app-id", default=None)
    parser.add_argument("--adzuna-app-key", default=None)
    parser.add_argument("--no-remote-boards", action="store_true",
                         help="Skip remote boards: RemoteOK, Jobicy, WWR (on-site/hybrid only)")
    parser.add_argument("--jooble-key", default=None,
                        help="Jooble API key (or set JOOBLE_API_KEY); source is skipped without it")
    parser.add_argument("--include-hn", action="store_true",
                        help="Also pull the monthly HN 'Who is hiring?' thread (best-effort, noisier)")
    parser.add_argument("--max-age-days", type=int, default=14,
                         help="Keep dated postings from the last N days; 0 disables freshness filtering")
    parser.add_argument("--levels", default="junior,mid_plus,unclear,mixed",
                         help="Comma-separated levels: junior, mid_plus, unclear, mixed (aliases: mid, senior, all)")
    parser.add_argument("--include-internships", action="store_true",
                         help="Include internships/trainees/working-student roles (excluded by default)")
    parser.add_argument("--max-per-track", type=int, default=DEFAULT_MAX_PER_TRACK,
                         help="Maximum rows per track in chat/Telegram; 0 sends all")
    parser.add_argument("--out", default=None, help="Write report to this file too (in addition to stdout)")
    parser.add_argument("--profile", default=None,
                        help="Master profile to personalize scoring/tailoring "
                             "(default: profile/master_profile.json|yaml)")
    parser.add_argument("--no-profile", action="store_true",
                        help="Ignore the master profile and use the generic hub/recency ranking")
    parser.add_argument("--telegram-token", default=None,
                        help="Telegram bot token from @BotFather (or set TELEGRAM_BOT_TOKEN)")
    parser.add_argument("--telegram-chat", default=None,
                        help="Telegram chat/channel: @username, t.me URL, or numeric chat id (or set TELEGRAM_CHAT_ID)")
    parser.add_argument("--turso-url", default=None,
                        help="Turso database URL, e.g. libsql://<db>.<region>.turso.io (or set TURSO_URL)")
    parser.add_argument("--turso-token", default=None,
                        help="Turso database auth token (or set TURSO_TOKEN)")
    parser.set_defaults(weekly=True)
    args = parser.parse_args()

    import os
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    adzuna_id = args.adzuna_app_id or os.environ.get("ADZUNA_APP_ID")
    adzuna_key = args.adzuna_app_key or os.environ.get("ADZUNA_APP_KEY")
    telegram_token = args.telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    telegram_chat = args.telegram_chat or os.environ.get("TELEGRAM_CHAT_ID")
    turso_url = args.turso_url or os.environ.get("TURSO_URL")
    turso_token = args.turso_token or os.environ.get("TURSO_TOKEN")
    jooble_key = args.jooble_key or os.environ.get("JOOBLE_API_KEY")
    countries = [c.strip().lower() for c in args.countries.split(",") if c.strip()]
    if args.max_age_days < 0:
        parser.error("--max-age-days must be >= 0")
    if args.max_per_track < 0:
        parser.error("--max-per-track must be >= 0")
    unknown_countries = set(countries) - set(DEFAULT_COUNTRIES)
    if unknown_countries:
        parser.error(f"unknown --countries value(s): {', '.join(sorted(unknown_countries))}")

    allowed_levels = {part.strip() for part in args.levels.split(",") if part.strip()}
    valid_levels = {"junior", "mid_plus", "unclear", "mixed", "internship", "mid", "senior", "lead", "all"}
    unknown_levels = allowed_levels - valid_levels
    if unknown_levels:
        parser.error(f"unknown --levels value(s): {', '.join(sorted(unknown_levels))}")
    if args.include_internships:
        allowed_levels.add("internship")
    state_path = Path(args.state_file)
    try:
        with state_lock(state_path):
            report_md, report_tg = run(
                countries=countries,
                state_path=state_path,
                weekly=args.weekly,
                adzuna_app_id=adzuna_id,
                adzuna_app_key=adzuna_key,
                include_remote_boards=not args.no_remote_boards,
                turso_url=turso_url,
                turso_token=turso_token,
                max_age_days=args.max_age_days,
                allowed_levels=allowed_levels,
                max_per_track=args.max_per_track,
                profile_path=None if args.no_profile else args.profile,
                jooble_api_key=jooble_key,
                include_hn=args.include_hn,
            )
    except RuntimeError as exc:
        print(f"  [error] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(report_md)
    if args.out:
        Path(args.out).write_text(report_md)
    if telegram_token and telegram_chat:
        if send_telegram(telegram_token, telegram_chat, report_tg):
            print(f"  [info] report sent to Telegram chat {resolve_chat_id(telegram_chat)}",
                  file=sys.stderr)
        else:
            print("  [warn] report was generated but Telegram delivery failed",
                  file=sys.stderr)
    elif telegram_token:
        print("  [warn] TELEGRAM_CHAT_ID not set — skipping Telegram send", file=sys.stderr)


if __name__ == "__main__":
    main()
