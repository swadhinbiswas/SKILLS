#!/usr/bin/env python3
"""
profile.py — master profile loading, job↔profile match scoring, and skill-gap
analysis.

This module is the personalization layer for the EU job hunter. It reads a
single "master profile" (JSON or YAML) describing the candidate — skills,
experience, preferences, salary, work authorization — and scores each
normalized job posting against it. It is stdlib-only except for an optional
PyYAML import (JSON always works).

Design goals:

* **Explainable.** Every score comes with human-readable reasons and gaps so
  the report and the board can show *why* a job ranked where it did.
* **Deterministic.** No randomness, no network calls. Same inputs → same score.
* **Forgiving.** A missing or partial profile degrades to the old
  recency/hub ranking instead of crashing.

Usage:
    from master_profile import load_profile, score_job
    profile = load_profile("profile/master_profile.json")
    result = score_job(job, profile)   # {"match_score", "reasons", "gaps", ...}
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_PATHS = (
    SKILL_ROOT / "profile" / "master_profile.json",
    SKILL_ROOT / "profile" / "master_profile.yaml",
    SKILL_ROOT / "profile" / "master_profile.yml",
)

# ---------------------------------------------------------------------------
# Skill normalization
# ---------------------------------------------------------------------------

# Canonical name -> list of surface forms found in job ads / resumes.
# Matching is word-boundary aware, so "go" does not match "good".
SKILL_ALIASES: dict[str, list[str]] = {
    "python": ["python"],
    "sql": ["sql"],
    "postgresql": ["postgresql", "postgres", "psql"],
    "mysql": ["mysql", "mariadb"],
    "sql server": ["sql server", "mssql", "t-sql", "tsql"],
    "sqlite": ["sqlite"],
    "oracle": ["oracle db", "oracle database", "pl/sql"],
    "mongodb": ["mongodb", "mongo"],
    "redis": ["redis"],
    "elasticsearch": ["elasticsearch", "elastic search", "opensearch"],
    "cassandra": ["cassandra"],
    "dynamodb": ["dynamodb"],
    "snowflake": ["snowflake"],
    "bigquery": ["bigquery", "big query"],
    "redshift": ["redshift"],
    "databricks": ["databricks"],
    "dbt": ["dbt", "data build tool"],
    "airflow": ["airflow", "apache airflow"],
    "dagster": ["dagster"],
    "prefect": ["prefect"],
    "spark": ["spark", "pyspark", "apache spark"],
    "flink": ["flink", "apache flink"],
    "kafka": ["kafka", "apache kafka"],
    "rabbitmq": ["rabbitmq"],
    "nats": ["nats"],
    "hadoop": ["hadoop", "hdfs", "hive"],
    "delta lake": ["delta lake", "delta format"],
    "iceberg": ["iceberg", "apache iceberg"],
    "pandas": ["pandas"],
    "numpy": ["numpy"],
    "polars": ["polars"],
    "duckdb": ["duckdb"],
    "java": ["java"],
    "kotlin": ["kotlin"],
    "scala": ["scala"],
    "go": ["golang", "go lang"],
    "rust": ["rust"],
    "c#": ["c#", "csharp", "c sharp", ".net", "dotnet", "asp.net"],
    "c++": ["c++", "cpp"],
    "javascript": ["javascript", "js", "ecmascript"],
    "typescript": ["typescript"],
    "node.js": ["node.js", "nodejs", "node js", "node"],
    "php": ["php"],
    "ruby": ["ruby", "ruby on rails", "rails"],
    "django": ["django"],
    "flask": ["flask"],
    "fastapi": ["fastapi"],
    "spring": ["spring", "spring boot"],
    "graphql": ["graphql"],
    "rest": ["rest", "restful", "rest api", "restful api"],
    "grpc": ["grpc"],
    "microservices": ["microservices", "microservice", "micro-services"],
    "distributed systems": ["distributed systems", "distributed system"],
    "event-driven": ["event-driven", "event driven", "event streaming"],
    "aws": ["aws", "amazon web services"],
    "gcp": ["gcp", "google cloud", "google cloud platform"],
    "azure": ["azure", "microsoft azure"],
    "docker": ["docker", "containerization", "containers"],
    "kubernetes": ["kubernetes", "k8s", "eks", "gke", "aks"],
    "terraform": ["terraform"],
    "pulumi": ["pulumi"],
    "ansible": ["ansible"],
    "helm": ["helm"],
    "ci/cd": ["ci/cd", "cicd", "ci cd", "continuous integration", "continuous delivery"],
    "jenkins": ["jenkins"],
    "github actions": ["github actions"],
    "gitlab ci": ["gitlab ci", "gitlab-ci"],
    "linux": ["linux", "unix"],
    "git": ["git"],
    "google cloud dataflow": ["dataflow", "data flow"],
    "prometheus": ["prometheus"],
    "grafana": ["grafana"],
    "datadog": ["datadog"],
    "observability": ["observability", "monitoring", "logging"],
    "machine learning": ["machine learning", "ml", "deep learning"],
    "mlops": ["mlops", "ml ops", "ml pipeline"],
    "pytorch": ["pytorch"],
    "tensorflow": ["tensorflow"],
    "llm": ["llm", "large language model", "llms"],
    "langchain": ["langchain"],
    "power bi": ["power bi", "powerbi"],
    "tableau": ["tableau"],
    "looker": ["looker"],
    "excel": ["excel", "spreadsheets"],
    "n8n": ["n8n"],
}

# Terms used for skill-gap analysis: jobs often require technologies the
# candidate does not list. We surface those as "gaps" so the user can decide.
GAP_VOCAB = sorted(
    {alias for forms in SKILL_ALIASES.values() for alias in forms}
    | {
        "snowflake", "databricks", "dbt", "airflow", "spark", "kafka", "flink",
        "terraform", "kubernetes", "docker", "gcp", "aws", "azure", "scala",
        "kotlin", "golang", "rust", "java", "typescript", "graphql", "grpc",
        "microservices", "snowflake", "bigquery", "redshift", "tableau",
        "looker", "power bi", "sage", "sap", "sas", "matlab", "r",
    },
)


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def normalize_skill(value: str) -> str:
    """Map a free-text skill to its canonical name when known."""
    text = _strip_accents(value or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    for canonical, forms in SKILL_ALIASES.items():
        if text == canonical or text in forms:
            return canonical
    return text


def _surface_pattern(alias: str) -> re.Pattern:
    # Escape, and require word boundaries that work for terms like "c++", "c#",
    # ".net", "ci/cd". \b is unreliable around symbols, so we use a custom
    # boundary: not preceded/followed by a word character or the same symbol.
    escaped = re.escape(alias)
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


_SURFACE_CACHE: dict[str, re.Pattern] = {}


def _alias_re(alias: str) -> re.Pattern:
    pattern = _SURFACE_CACHE.get(alias)
    if pattern is None:
        pattern = _surface_pattern(alias)
        _SURFACE_CACHE[alias] = pattern
    return pattern


def _text_of(job: dict, limit: int = 6000) -> str:
    return " ".join(str(job.get(key) or "") for key in ("title", "snippet", "description"))[:limit]


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def _load_yaml(text: str):
    try:
        import yaml  # type: ignore
    except ImportError as exc:  # pragma: no cover - PyYAML is usually present
        raise RuntimeError(
            "YAML profile given but PyYAML is not installed; use JSON instead"
        ) from exc
    return yaml.safe_load(text)


def load_profile(path: str | Path | None = None) -> dict:
    """Load and normalize the master profile. Returns {} if none exists.

    Accepts a JSON or YAML file. Missing file → empty profile (the scorer
    then returns neutral, recency-based results rather than failing).
    """
    candidates = []
    if path:
        candidates.append(Path(path))
    candidates.extend(DEFAULT_PROFILE_PATHS)
    chosen = next((p for p in candidates if p and p.exists()), None)
    if chosen is None:
        return {}
    text = chosen.read_text()
    if chosen.suffix.lower() in {".yaml", ".yml"}:
        data = _load_yaml(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = _load_yaml(text)
    if not isinstance(data, dict):
        raise ValueError(f"profile {chosen} must be a mapping/object")
    profile = _normalize_profile(data)
    profile["_path"] = str(chosen)
    return profile


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v not in (None, "")]
    return [value]


def _normalize_profile(raw: dict) -> dict:
    profile = dict(raw)
    skills = raw.get("skills") or {}
    if isinstance(skills, list):  # allow a flat list too
        skills = {"core": skills}
    profile["skills"] = {
        "core": sorted({normalize_skill(s) for s in _as_list(skills.get("core")) if s}),
        "familiar": sorted({normalize_skill(s) for s in _as_list(skills.get("familiar")) if s}),
        "learning": sorted({normalize_skill(s) for s in _as_list(skills.get("learning")) if s}),
    }
    profile["tracks"] = [t for t in _as_list(raw.get("tracks"))]
    profile["preferred_countries"] = [
        str(c).strip().lower() for c in _as_list(raw.get("preferred_countries")) if c
    ]
    profile["seniority_target"] = [str(s).strip().lower() for s in _as_list(raw.get("seniority_target"))]
    profile["excluded_companies"] = [
        str(c).strip().lower() for c in _as_list(raw.get("excluded_companies")) if c
    ]
    profile["excluded_keywords"] = [
        str(k).strip().lower() for k in _as_list(raw.get("excluded_keywords")) if k
    ]
    profile["work_authorization"] = [
        str(w).strip().lower() for w in _as_list(raw.get("work_authorization"))
    ]
    profile["languages"] = raw.get("languages") or []
    salary = raw.get("salary") or {}
    profile["salary"] = salary if isinstance(salary, dict) else {}
    return profile


def profile_is_usable(profile: dict) -> bool:
    """A profile needs at least some skills or a target track to personalize."""
    if not profile:
        return False
    skills = profile.get("skills") or {}
    return bool(skills.get("core") or skills.get("familiar") or profile.get("tracks"))


# ---------------------------------------------------------------------------
# Skill extraction
# ---------------------------------------------------------------------------

def _skill_hits(text: str, canonical_skills: list[str]) -> list[str]:
    lowered = text or ""
    hits = []
    for skill in canonical_skills:
        forms = SKILL_ALIASES.get(skill, [skill])
        if any(_alias_re(alias).search(lowered) for alias in forms):
            hits.append(skill)
    return hits


def extract_profile_skills(text: str, profile: dict) -> dict[str, list[str]]:
    """Which of the candidate's skills appear in a job's text."""
    skills = profile.get("skills") or {}

    def hit(canonical: str) -> bool:
        return any(
            _alias_re(alias).search(text or "")
            for alias in SKILL_ALIASES.get(canonical, [canonical])
        )

    return {
        "core": [s for s in skills.get("core", []) if hit(s)],
        "familiar": [s for s in skills.get("familiar", []) if hit(s)],
        "learning": [s for s in skills.get("learning", []) if hit(s)],
    }


def detect_gaps(text: str, profile: dict, limit: int = 12) -> list[str]:
    """Technologies mentioned in a posting that are absent from the profile."""
    owned = set()
    skills = profile.get("skills") or {}
    for tier in ("core", "familiar", "learning"):
        owned.update(skills.get(tier, []))
    normalized = {normalize_skill(s) for s in owned}

    found: list[str] = []
    for canonical, forms in SKILL_ALIASES.items():
        if normalize_skill(canonical) in normalized:
            continue
        if any(_alias_re(alias).search(text or "") for alias in forms):
            found.append(canonical)
    # Stable, readable order: most-mentioned first, then alphabetical.
    found.sort()
    return found[:limit]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "skills": 40,
    "track": 15,
    "seniority": 15,
    "location": 15,
    "salary": 8,
    "freshness": 5,
    "relocation": 2,
}


def _seniority_fit(job_level: str, targets: list[str]) -> tuple[int, str | None, str | None]:
    """Return (points 0-15, reason, gap)."""
    if not targets:
        return 10, None, None
    level = (job_level or "unclear").lower()
    norm = {"mid": "mid_plus", "senior": "mid_plus", "lead": "mid_plus"}.get(level, level)
    if norm in targets:
        return 15, f"seniority matches your target ({level})", None
    if norm == "unclear" or norm == "mixed":
        return 7, None, "seniority unclear — verify it fits"
    return 2, None, f"seniority ({level}) is outside your target"


def _location_fit(job: dict, profile: dict) -> tuple[int, str | None, str | None]:
    """Return (points 0-15, reason, gap)."""
    prefs = profile.get("preferred_countries") or []
    remote_pref = str(profile.get("remote_preference") or "any").lower()
    relocate_ok = bool(profile.get("willing_to_relocate"))
    country = (job.get("country") or "").lower()
    eligible = {c for c in str(job.get("eligible_countries") or "").split(",") if c}
    scoped = {country} if country else set()
    scoped |= eligible
    reasons = []
    gaps = []
    points = 0
    if job.get("remote"):
        if remote_pref in {"remote", "any", "hybrid"}:
            points += 8
            reasons.append("remote role matches your preference")
        else:
            gaps.append("remote role, but you asked for on-site/hybrid")
    if prefs and scoped:
        if scoped & set(prefs):
            points += 7
            reasons.append("in a country you prefer")
        else:
            gaps.append(
                "country not in your preferred list"
                + (f" ({', '.join(sorted(scoped))})" if scoped else "")
            )
    elif not prefs:
        points += 7  # no stated preference → neutral, not penalized
    if job.get("relocation"):
        if relocate_ok:
            points += 2
            reasons.append("offers relocation/visa support")
        else:
            gaps.append("requires relocation but you marked yourself not willing to relocate")
    # Work authorization is a hard gate when the job demands it and we can't.
    auth = set(profile.get("work_authorization") or [])
    needs_sponsorship = "requires_sponsorship" in auth
    if needs_sponsorship and scoped and not (scoped & {"eu", "eea"}):
        gaps.append("may need work authorization you don't have")
    return min(points, 15), ("; ".join(reasons) or None), ("; ".join(gaps) or None)


def _salary_fit(job: dict, profile: dict) -> tuple[int, str | None, str | None]:
    """Return (points 0-8, reason, gap) using a conservative numeric parse."""
    target = (profile.get("salary") or {}).get("min") or (profile.get("salary") or {}).get("target")
    if not job.get("salary"):
        return 0, None, None
    if not target:
        return 4, "salary disclosed", None
    number = _parse_salary(job.get("salary"))
    if number is None:
        return 4, "salary disclosed", None
    try:
        target = float(target)
    except (TypeError, ValueError):
        return 4, "salary disclosed", None
    if number >= target:
        return 8, f"salary meets your target (≥{target:,.0f})", None
    if number >= target * 0.85:
        return 4, "salary close to your target", None
    return 0, None, f"salary below your target ({number:,.0f} < {target:,.0f})"


def _parse_salary(value) -> float | None:
    text = str(value or "")
    match = re.search(r"([\d][\d.,\s]*)\s*(k|m)?", text, re.IGNORECASE)
    if not match:
        return None
    raw = match.group(1).replace(" ", "")
    # 70,000 / 70.000 / 70 000 → 70000 ; 70k → 70000
    if "," in raw and "." in raw:
        raw = raw.replace(",", "")
    elif raw.count(",") == 1 and len(raw.split(",")[-1]) == 3:
        raw = raw.replace(",", "")
    elif raw.count(".") == 1 and len(raw.split(".")[-1]) == 3:
        raw = raw.replace(".", "")
    else:
        raw = raw.replace(",", ".").replace(".", "")
    try:
        number = float(raw)
    except ValueError:
        return None
    suffix = (match.group(2) or "").lower()
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return number


def _freshness_points(job: dict, today=None) -> int:
    from datetime import date, datetime, timezone
    posted_raw = job.get("posted")
    if not posted_raw:
        return 1
    try:
        posted = datetime.fromisoformat(str(posted_raw).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return 1
    today = today or datetime.now(timezone.utc).date()
    age = (today - posted).days
    if age <= 3:
        return 5
    if age <= 7:
        return 3
    if age <= 14:
        return 1
    return 0


def score_job(job: dict, profile: dict, weights: dict | None = None) -> dict:
    """Score a normalized job against the profile.

    Returns a dict with:
      * ``match_score``  — 0–100
      * ``reasons``      — why it fits (list[str])
      * ``gaps``         — things to watch (list[str])
      * ``matched_skills`` / ``missing_skills`` — skill breakdown
      * ``excluded``     — True when a profile rule vetoes the job
    """
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    text = _text_of(job)
    reasons: list[str] = []
    gaps: list[str] = []

    # -- hard exclusions -------------------------------------------------
    haystack = _strip_accents(f"{job.get('title','')} {job.get('company','')}").lower()
    for company in profile.get("excluded_companies", []):
        if company and company in _strip_accents(job.get("company", "")).lower():
            return _result(0, [], [f"company on your excluded list ({company})"],
                           [], [], excluded=True)
    for keyword in profile.get("excluded_keywords", []):
        if keyword and keyword in haystack:
            return _result(0, [], [f"excluded keyword present ({keyword})"],
                           [], [], excluded=True)

    # -- skills ----------------------------------------------------------
    hits = extract_profile_skills(text, profile)
    core_hits = hits["core"]
    fam_hits = hits["familiar"]
    learn_hits = hits["learning"]
    owned_total = max(1, len(profile.get("skills", {}).get("core", [])))
    weighted = min(1.0, (2 * len(core_hits) + len(fam_hits)) / (2 * owned_total))
    skill_points = round(weights["skills"] * weighted)
    if core_hits:
        reasons.append("matches core skills: " + ", ".join(core_hits[:6]))
    if fam_hits and not core_hits:
        reasons.append("matches familiar skills: " + ", ".join(fam_hits[:5]))
    missing = detect_gaps(text, profile)
    if missing:
        gaps.append("skills to address: " + ", ".join(missing[:6]))

    # -- track -----------------------------------------------------------
    track = job.get("track") or job.get("_track") or ""
    tracks = profile.get("tracks") or []
    track_points = 0
    if not tracks:
        track_points = round(weights["track"] * 0.6)
    elif track in tracks:
        track_points = weights["track"]
        reasons.append(f"target role track ({track.replace('_', ' ')})")
    elif (job.get("role_fit") == "borderline"):
        track_points = round(weights["track"] * 0.3)

    # -- seniority / location / salary / freshness / relocation ----------
    sen_points, sen_reason, sen_gap = _seniority_fit(job.get("seniority"), profile.get("seniority_target", []))
    if sen_reason:
        reasons.append(sen_reason)
    if sen_gap:
        gaps.append(sen_gap)

    loc_points, loc_reason, loc_gap = _location_fit(job, profile)
    if loc_reason:
        reasons.append(loc_reason)
    if loc_gap:
        gaps.append(loc_gap)

    sal_points, sal_reason, sal_gap = _salary_fit(job, profile)
    if sal_reason:
        reasons.append(sal_reason)
    if sal_gap:
        gaps.append(sal_gap)

    fresh_points = _freshness_points(job)
    if fresh_points >= 3:
        reasons.append("recently posted")
    reloc_points = weights["relocation"] if job.get("relocation") else 0

    total = (skill_points + track_points + sen_points + loc_points
             + sal_points + fresh_points + reloc_points)
    total = max(0, min(100, total))
    return _result(total, reasons, gaps, core_hits + fam_hits + learn_hits, missing)


def _result(score, reasons, gaps, matched, missing, excluded=False) -> dict:
    return {
        "match_score": int(score),
        "reasons": list(dict.fromkeys(reasons)),
        "gaps": list(dict.fromkeys(gaps)),
        "matched_skills": matched,
        "missing_skills": missing,
        "excluded": bool(excluded),
    }


def score_items(items: list[dict], profile: dict) -> list[dict]:
    """Annotate every item in place with match data and return the list."""
    if not profile_is_usable(profile):
        return items
    for item in items:
        result = score_job(item, profile)
        item["match_score"] = result["match_score"]
        item["match_reasons"] = "; ".join(result["reasons"])
        item["match_gaps"] = "; ".join(result["gaps"])
        item["matched_skills"] = ",".join(result["matched_skills"])
        item["missing_skills"] = ",".join(result["missing_skills"])
        if result["excluded"]:
            item["excluded"] = True
    return items


__all__ = [
    "load_profile", "profile_is_usable", "normalize_skill", "extract_profile_skills",
    "detect_gaps", "score_job", "score_items", "DEFAULT_WEIGHTS",
]
