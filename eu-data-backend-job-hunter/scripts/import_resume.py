#!/usr/bin/env python3
"""
import_resume.py — turn a JSON Resume (with our extensions) into the job
hunter's ``profile/master_profile.json``.

This makes ``resume.json`` the single seed: keep it rich, then regenerate the
master profile the skill scores and tailors against.

It understands the standard JSON Resume fields (basics, work, projects, skills,
education, languages, certificates, publications, awards) plus the custom
``jobSearch`` block this project adds for search preferences.

Usage:
    python scripts/import_resume.py
    python scripts/import_resume.py --resume /path/to/resume.json \
        --out profile/master_profile.json
    python scripts/import_resume.py --print        # dump to stdout, don't write

CLI flags override the file; a missing/invalid file leaves the existing profile
untouched (nonzero exit + message).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

DEFAULT_RESUME = os.environ.get("RESUME_JSON", "/home/swadhin/JOBSCAN/resume.json")
DEFAULT_OUT = ROOT / "profile" / "master_profile.json"

LEVEL_MAP = {"core": "core", "expert": "core", "advanced": "core",
             "working": "familiar", "familiar": "familiar", "intermediate": "familiar",
             "learning": "learning", "basic": "learning", "beginner": "learning"}


def _load(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"resume file not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"resume file is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("resume must be a JSON object")
    return data


def _location_text(location) -> str:
    if isinstance(location, str):
        return location.strip()
    if not isinstance(location, dict):
        return ""
    if location.get("address"):
        return str(location["address"]).strip()
    parts = [location.get("city"), location.get("region"), location.get("countryCode")]
    return ", ".join(str(p).strip() for p in parts if p)


def _end_date(value) -> str:
    text = str(value or "").strip()
    return "present" if text.lower() == "present" else text


def _links_from_profiles(profiles) -> dict:
    links: dict[str, str] = {}
    for profile in profiles or []:
        if not isinstance(profile, dict):
            continue
        url = str(profile.get("url") or "").strip()
        network = str(profile.get("network") or "").strip()
        if url and network:
            links[network.lower().replace(" ", "_")] = url
    return links


def _split_skills(groups) -> dict[str, list[str]]:
    tiers = {"core": [], "familiar": [], "learning": []}
    for group in groups or []:
        if isinstance(group, list):
            keywords, level = group, "core"
        elif isinstance(group, dict):
            keywords = group.get("keywords") or []
            level = LEVEL_MAP.get(str(group.get("level") or "core").lower(), "core")
            if not keywords and group.get("name"):
                keywords = [group["name"]]
        else:
            continue
        for keyword in keywords:
            skill = str(keyword).strip()
            if skill and skill not in tiers[level]:
                tiers[level].append(skill)
    return tiers


def _work_to_experience(work) -> list[dict]:
    experience = []
    for role in work or []:
        if not isinstance(role, dict):
            continue
        experience.append({
            "company": role.get("name") or role.get("company") or role.get("organization") or "",
            "title": role.get("position") or role.get("title") or "",
            "location": role.get("location") or "",
            "start": role.get("startDate") or "",
            "end": _end_date(role.get("endDate")),
            "bullets": [str(h) for h in (role.get("highlights") or role.get("bullets") or []) if h],
        })
    return experience


def _projects(projects) -> list[dict]:
    out = []
    for project in projects or []:
        if not isinstance(project, dict):
            continue
        links = project.get("links") or {}
        link = links.get("source") or links.get("demo") or links.get("docs") or project.get("url") or ""
        out.append({
            "name": project.get("name") or "",
            "description": project.get("description") or "",
            "link": link,
            "highlight": (project.get("highlights") or [""])[0] if project.get("highlights") else "",
        })
    return out


def _education(items) -> list[dict]:
    out = []
    for edu in items or []:
        if not isinstance(edu, dict):
            continue
        degree = " ".join(str(x) for x in (edu.get("studyType"), edu.get("area")) if x).strip()
        details = edu.get("summary") or ", ".join(edu.get("courses") or [])
        out.append({
            "institution": edu.get("institution") or "",
            "degree": degree,
            "start": edu.get("startDate") or "",
            "end": _end_date(edu.get("endDate")),
            "details": details,
        })
    return out


def _languages(items) -> list[dict]:
    out = []
    for lang in items or []:
        if not isinstance(lang, dict):
            continue
        level = str(lang.get("fluency") or lang.get("level") or "").strip()
        if lang.get("cefr"):
            level = f"{level} ({lang['cefr']})" if level else str(lang["cefr"])
        out.append({"language": lang.get("language") or "", "level": level})
    return out


def _certifications(items) -> list[dict]:
    out = []
    for cert in items or []:
        if not isinstance(cert, dict):
            continue
        year = str(cert.get("date") or "")[:4]
        name = cert.get("name") or ""
        if cert.get("issuer"):
            name = f"{name} — {cert['issuer']}"
        out.append({"name": name, "year": year})
    return out


def convert(resume: dict) -> dict:
    basics = resume.get("basics") or {}
    search = resume.get("jobSearch") or {}
    jobs = search.get("salary") or {}

    profile = {
        "name": basics.get("name") or "Your Name",
        "headline": basics.get("label") or "",
        "email": basics.get("email") or "",
        "phone": basics.get("phone") or "",
        "location": _location_text(basics.get("location")),
        "links": _links_from_profiles(basics.get("profiles")),

        "tracks": search.get("tracks") or [],
        "seniority_target": search.get("seniorityTarget") or search.get("seniority_target") or [],
        "years_experience": search.get("yearsExperience") or search.get("years_experience") or 0,

        "preferred_countries": search.get("preferredCountries") or [],
        "remote_preference": search.get("remotePreference") or "any",
        "willing_to_relocate": bool(search.get("willingToRelocate", True)),
        "work_authorization": search.get("workAuthorization") or [],
        "languages": _languages(resume.get("languages") or resume.get("languagesSpoken")),

        "salary": jobs if isinstance(jobs, dict) else {},

        "skills": _split_skills(resume.get("skills")),
        "summary": basics.get("summary") or "",

        "experience": _work_to_experience(resume.get("work")),
        "projects": _projects(resume.get("projects")),
        "education": _education(resume.get("education")),
        "certifications": _certifications(resume.get("certificates")) or resume.get("certifications") or [],

        "excluded_companies": search.get("excludedCompanies") or [],
        "excluded_keywords": search.get("excludedKeywords") or [],

        # Carried through for template/rendering use; the scorer ignores them.
        "publications": resume.get("publications") or [],
        "awards": resume.get("awards") or [],
        "interests": resume.get("interests") or [],

        "application": {
            "tone": (resume.get("application") or {}).get("tone", "warm-professional"),
            "sign_off": (resume.get("application") or {}).get("sign_off", "Best regards"),
            "custom_note": search.get("customNote") or search.get("custom_note") or "",
        },
    }
    if not profile["headline"]:
        tracks = profile["tracks"] or ["Engineer"]
        profile["headline"] = str(tracks[0]).replace("_", " ").title()
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--resume", default=DEFAULT_RESUME, help="Path to resume.json")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Master profile output path")
    parser.add_argument("--print", dest="to_stdout", action="store_true",
                        help="Print the profile instead of writing it")
    args = parser.parse_args()

    resume = _load(Path(args.resume))
    profile = convert(resume)

    if args.to_stdout:
        print(json.dumps(profile, indent=2, ensure_ascii=False))
        return

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(profile, indent=2, ensure_ascii=False))
    stats = profile["skills"]
    print(f"  [info] wrote {out}")
    print(f"  [info] {len(profile['experience'])} roles, {len(profile['projects'])} projects, "
          f"{len(stats['core'])} core / {len(stats['familiar'])} familiar / "
          f"{len(stats['learning'])} learning skills")


if __name__ == "__main__":
    main()
