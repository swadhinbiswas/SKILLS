#!/usr/bin/env python3
"""
apply.py — application drafting for the EU job hunter.

Given a normalized job posting and the master profile, this produces a
**tailored, ready-to-send application pack** in ``applications/<job_id>/``:

* ``resume.html`` / ``resume.pdf`` — the CV, with skills and bullets reordered
  so the ones the posting asks for come first
* ``resume.md``               — plain-text/Markdown CV for ATS text boxes
* ``cover_letter.md`` / ``cover_letter.txt``
* ``email.txt``               — recipient, subject and body for a recruiter email
* ``job.json`` / ``meta.json`` — the snapshot and what was generated

It is **drafts only**: nothing is ever sent. You review and hit send. This is
deliberate — automated mass applications breach most job boards' terms and
damage your reputation with employers.

Stdlib + PyYAML + jinja2 + markdown; PDF uses the system ``chromium`` (or
``pandoc``) if present, otherwise the HTML/Markdown is still written.

Usage:
    python scripts/apply.py tailor --job-id <id>          # pull from Turso
    python scripts/apply.py tailor --job-json job.json    # or a local snapshot
    python scripts/apply.py list                          # recent drafts/applications
    python scripts/apply.py status --job-id <id> --status applied
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import master_profile  # noqa: E402
import turso  # noqa: E402

TEMPLATES_DIR = ROOT / "templates"
APPLICATIONS_DIR = ROOT / "applications"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ROLE_WORDS = {
    "engineer", "engineering", "developer", "data", "backend", "back-end",
    "software", "platform", "pipeline", "api", "cloud", "architect",
}


# ---------------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------------

def _sentence_list(items) -> str:
    items = [str(item) for item in (items or []) if item]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + ", and " + items[-1]


def _pretty_skill(skill: str) -> str:
    map_ = {
        "sql": "SQL", "aws": "AWS", "gcp": "GCP", "ci/cd": "CI/CD",
        "rest": "REST", "node.js": "Node.js", "c#": "C#", "c++": "C++",
        "postgresql": "PostgreSQL", "mysql": "MySQL", "mongodb": "MongoDB",
        "graphql": "GraphQL", "grpc": "gRPC", "dbt": "dbt", "etl": "ETL",
        "llm": "LLM", "mlops": "MLOps", "n8n": "n8n", "php": "PHP",
        "html": "HTML", "css": "CSS", "api": "API",
    }
    if skill in map_:
        return map_[skill]
    return " ".join(word.upper() if word in {"sql", "aws", "gcp", "etl", "api"}
                    else word.capitalize() for word in skill.split())


def _job_text(job: dict) -> str:
    return " ".join(str(job.get(key) or "") for key in ("title", "snippet", "description"))


def _job_keywords(job: dict, profile: dict) -> list[str]:
    """Keywords used to rank bullets/skills for this specific posting."""
    text = _job_text(job).lower()
    keywords: list[str] = []
    for tier in ("core", "familiar", "learning"):
        for skill in (profile.get("skills") or {}).get(tier, []):
            forms = master_profile.SKILL_ALIASES.get(skill, [skill])
            if any(form in text for form in forms):
                keywords.append(skill)
                keywords.extend(forms)
    for word in re.findall(r"[a-zA-Z][a-zA-Z+#.]{2,}", text):
        low = word.lower()
        if low in _ROLE_WORDS:
            keywords.append(low)
    seen, ordered = set(), []
    for keyword in keywords:
        if keyword not in seen:
            seen.add(keyword)
            ordered.append(keyword)
    return ordered


def _relevance(text: str, keywords: list[str]) -> int:
    low = (text or "").lower()
    return sum(low.count(keyword) for keyword in keywords)


def _ordered_bullets(bullets: list[str], keywords: list[str]) -> list[str]:
    indexed = list(enumerate(bullets or []))
    indexed.sort(key=lambda pair: (-_relevance(pair[1], keywords), pair[0]))
    return [bullet for _, bullet in indexed]


def _ordered_skills(profile: dict, job: dict) -> list[str]:
    text = _job_text(job).lower()

    def on_job(skill: str) -> bool:
        return any(form in text for form in master_profile.SKILL_ALIASES.get(skill, [skill]))

    skills = profile.get("skills") or {}
    ordered: list[str] = []
    for tier in ("core", "familiar", "learning"):
        for skill in skills.get(tier, []):
            ordered.append(skill)
    # Relevant first, preserving tier order within each group.
    relevant = [skill for skill in ordered if on_job(skill)]
    rest = [skill for skill in ordered if skill not in relevant]
    return [_pretty_skill(skill) for skill in relevant + rest]


def _extract_email(text: str) -> str | None:
    match = _EMAIL_RE.search(text or "")
    if not match:
        return None
    email = match.group(0).rstrip(".,;:)")
    # Ignore obvious non-contact addresses.
    if any(bad in email.lower() for bad in ("noreply", "no-reply", "example.com")):
        return None
    return email


def _dates(start, end) -> str:
    start = str(start or "").strip()
    end = str(end or "").strip() or "present"
    if not start:
        return end
    return f"{start} – {end}"


def _format_salary(job: dict) -> str:
    return str(job.get("salary") or "").strip()


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------

def build_context(job: dict, profile: dict) -> dict:
    keywords = _job_keywords(job, profile)
    matched = [s for s in (job.get("matched_skills") or "").split(",") if s]
    if not matched:
        hits = master_profile.extract_profile_skills(_job_text(job), profile)
        matched = hits["core"] + hits["familiar"] + hits["learning"]
    missing = [s for s in (job.get("missing_skills") or "").split(",") if s][:6]

    experience = []
    for role in profile.get("experience") or []:
        if not isinstance(role, dict):
            continue
        bullets = _ordered_bullets([str(b) for b in (role.get("bullets") or [])], keywords)
        experience.append({
            "title": role.get("title", ""),
            "company": role.get("company", ""),
            "location": role.get("location", ""),
            "dates": _dates(role.get("start"), role.get("end")),
            "bullets": bullets,
        })

    links = []
    for label, url in (profile.get("links") or {}).items():
        if url:
            links.append({"label": label.replace("_", " ").title(), "url": url})

    name = profile.get("name") or "Your Name"
    headline = profile.get("headline") or (profile.get("tracks") or ["Engineer"])[0].replace("_", " ").title()
    title = job.get("title") or "the role"
    company = job.get("company") or ""
    company_or_team = company or "your"

    top_skills = [_pretty_skill(s) for s in (matched or keywords)[:6] if not s.startswith("~")]
    # Most recent role supplies the concrete experience sentence.
    recent_sentence = ""
    if experience:
        recent = experience[0]
        first_bullet = (recent.get("bullets") or [""])[0]
        lead = first_bullet.rstrip(".").strip()
        if lead:
            lead = lead[0].lower() + lead[1:]
            recent_sentence = (
                f"Most recently at {recent['company'] or 'my current company'} "
                f"as {recent['title']}, I {lead}."
            )
    matched_sentence = _sentence_list(top_skills)

    application = profile.get("application") or {}
    opening = (
        f"I'm a {headline} based in {profile.get('location', 'the EU')}, and this "
        f"role lines up closely with the work I do and want to keep doing."
        if profile.get("location")
        else f"I'm a {headline}, and this role lines up closely with the work I do."
    )
    motivation = (
        f"{company or 'Your team'} stands out for the problems this role owns, and "
        f"I'd bring hands-on experience with {matched_sentence or 'the core stack'} from day one."
    )
    experience_sentence = recent_sentence or (
        f"I've delivered production work with {matched_sentence}." if matched_sentence
        else "I've delivered production engineering work end to end."
    )

    recipient = _extract_email(_job_text(job)) or ""
    subject = f"Application: {title}" + (f" — {name}" if name else "")
    email_body = (
        f"{experience_sentence} "
        + (f"Relevant skills for this role: {matched_sentence}. " if matched_sentence else "")
        + f"I'm excited by the chance to bring that to {company or 'your team'}."
    )

    return {
        "job": job,
        "name": name,
        "headline": headline,
        "email": profile.get("email", ""),
        "phone": profile.get("phone", ""),
        "location": profile.get("location", ""),
        "links": links,
        "summary": profile.get("summary", ""),
        "skills": _ordered_skills(profile, job),
        "experience": experience,
        "projects": profile.get("projects") or [],
        "publications": [
            {
                "name": pub.get("name", ""),
                "publisher": pub.get("publisher", ""),
                "date": pub.get("releaseDate", ""),
                "url": pub.get("url", ""),
                "summary": pub.get("summary", ""),
            }
            for pub in (profile.get("publications") or []) if isinstance(pub, dict)
        ],
        "awards": [
            {
                "title": award.get("title", ""),
                "date": award.get("date", ""),
                "awarder": award.get("awarder", ""),
                "summary": award.get("summary", ""),
            }
            for award in (profile.get("awards") or []) if isinstance(award, dict)
        ],
        "education": [
            {
                "institution": edu.get("institution", ""),
                "degree": edu.get("degree", ""),
                "dates": _dates(edu.get("start"), edu.get("end")),
                "details": edu.get("details", ""),
            }
            for edu in (profile.get("education") or []) if isinstance(edu, dict)
        ],
        "certifications": profile.get("certifications") or [],
        "matched_skills": [_pretty_skill(s) for s in matched],
        "matched_skills_sentence": matched_sentence,
        "missing_skills": [_pretty_skill(s) for s in missing],
        "keywords": keywords,
        "company_or_team": company_or_team,
        "opening_line": opening,
        "experience_sentence": experience_sentence,
        "motivation_line": motivation,
        "email_opening": (
            f"I'm a {headline} and I've been following the kind of work this team does."
        ),
        "email_body": email_body,
        "custom_note": application.get("custom_note", ""),
        "sign_off": application.get("sign_off", "Best regards"),
        "tone": application.get("tone", "warm-professional"),
        "recipient": recipient,
        "recipient_name": "",
        "subject": subject,
        "salary": _format_salary(job),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _jinja_env():
    try:
        from jinja2 import Environment, FileSystemLoader, select_autoescape
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("jinja2 is required for application drafting") from exc
    return Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True, lstrip_blocks=True,
    )


def render_resume_markdown(ctx: dict) -> str:
    lines = [f"# {ctx['name']}"]
    if ctx["headline"]:
        lines.append(f"_{ctx['headline']}_")
    contact = [bit for bit in (ctx["email"], ctx["phone"], ctx["location"]) if bit]
    contact += [f"[{link['label']}]({link['url']})" for link in ctx["links"]]
    if contact:
        lines.append(" · ".join(contact))
    lines.append("")
    if ctx["summary"]:
        lines += ["## Summary", ctx["summary"], ""]
    if ctx["skills"]:
        lines += ["## Skills", ", ".join(ctx["skills"]), ""]
    if ctx["experience"]:
        lines.append("## Experience")
        for role in ctx["experience"]:
            header = role["title"]
            if role["company"]:
                header += f" — {role['company']}"
            meta = " · ".join(bit for bit in (role["dates"], role["location"]) if bit)
            lines.append(f"### {header}")
            if meta:
                lines.append(f"_{meta}_")
            lines += [f"- {bullet}" for bullet in role["bullets"]]
            lines.append("")
    if ctx["projects"]:
        lines.append("## Projects")
        for project in ctx["projects"]:
            link = f" — {project.get('link')}" if project.get("link") else ""
            lines.append(f"### {project.get('name', '')}{link}")
            if project.get("description"):
                lines.append(project["description"])
            lines.append("")
    if ctx["education"]:
        lines.append("## Education")
        for edu in ctx["education"]:
            detail = f" — {edu['institution']}" if edu["institution"] else ""
            dates = f" ({edu['dates']})" if edu["dates"] else ""
            lines.append(f"- **{edu['degree']}**{detail}{dates}")
        lines.append("")
    if ctx.get("publications"):
        lines.append("## Publications")
        for pub in ctx["publications"]:
            meta = " · ".join(bit for bit in (pub["publisher"], pub["date"]) if bit)
            link = f" — {pub['url']}" if pub["url"] else ""
            lines.append(f"- **{pub['name']}**" + (f" ({meta})" if meta else "") + link)
        lines.append("")
    if ctx.get("awards"):
        lines.append("## Achievements")
        for award in ctx["awards"]:
            meta = " · ".join(bit for bit in (award["awarder"], award["date"]) if bit)
            lines.append(f"- **{award['title']}**" + (f" — {meta}" if meta else ""))
        lines.append("")
    if ctx["certifications"]:
        lines.append("## Certifications")
        for cert in ctx["certifications"]:
            year = f" ({cert.get('year')})" if cert.get("year") else ""
            lines.append(f"- {cert.get('name', '')}{year}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_cover_letter(ctx: dict) -> str:
    job = ctx["job"]
    lines = [f"# Application — {job.get('title', '')}"
             + (f" at {job.get('company')}" if job.get("company") else "")]
    lines.append("")
    lines.append(f"**Candidate:** {ctx['name']}"
                 + (f" — {ctx['headline']}" if ctx["headline"] else ""))
    contact = " · ".join(bit for bit in (ctx["email"], ctx["phone"], ctx["location"]) if bit)
    if contact:
        lines.append(f"**Contact:** {contact}")
    if ctx["links"]:
        lines.append("**Links:** " + " · ".join(
            f"[{link['label']}]({link['url']})" for link in ctx["links"]
        ))
    role = f"[{job.get('title', '')}]({job.get('url', '')})"
    if job.get("location"):
        role += f" · {job['location']}"
    lines.append(f"**Role:** {role}")
    lines += ["", "---", "", f"Dear {ctx['company_or_team']} team,", "",
              f"I am writing to apply for the **{job.get('title', '')}** position"
              + (f" at {job.get('company')}" if job.get("company") else "")
              + f". {ctx['opening_line']}", ""]
    if ctx["matched_skills_sentence"]:
        lines.append(
            "What draws me to this role is the overlap with the work I already do "
            f"day to day: {ctx['matched_skills_sentence']}. {ctx['experience_sentence']}"
        )
    else:
        lines.append(ctx["experience_sentence"])
    lines += ["", ctx["motivation_line"], ""]
    if ctx["custom_note"]:
        lines += [ctx["custom_note"], ""]
    lines += [
        "I have attached my CV and would welcome the chance to talk about how I "
        "can contribute. Thank you for your time and consideration.",
        "", f"{ctx['sign_off']},", "", ctx["name"],
    ]
    tail = " · ".join(bit for bit in (ctx["email"], ctx["phone"]) if bit)
    if tail:
        lines.append(tail)
    for link in ctx["links"]:
        lines.append(f"{link['label']}: {link['url']}")
    return "\n".join(lines).strip() + "\n"


def render_email(ctx: dict) -> str:
    job = ctx["job"]
    lines = [f"To: {ctx['recipient']}", f"Subject: {ctx['subject']}", ""]
    if ctx["recipient"]:
        lines.append(f"Dear {ctx['recipient_name'] or 'Hiring Team'},")
    else:
        lines.append(f"Dear {ctx['company_or_team']} Hiring Team,")
    lines.append("")
    location = f" ({job['location']})" if job.get("location") else ""
    lines.append(
        f"I'd like to apply for the {job.get('title', '')} role"
        + (f" at {job.get('company')}" if job.get("company") else "")
        + f"{location}. {ctx['email_opening']}"
    )
    lines += ["", ctx["email_body"]]
    if ctx["custom_note"]:
        lines += ["", ctx["custom_note"]]
    lines += ["", "My CV is attached. I'd be glad to arrange a short call whenever "
              "convenient.", "", f"{ctx['sign_off']},", ctx["name"]]
    contact = " · ".join(bit for bit in (ctx["email"], ctx["phone"]) if bit)
    if contact:
        lines.append(contact)
    for link in ctx["links"]:
        lines.append(f"{link['label']}: {link['url']}")
    lines += ["", "---", "", f"Apply link: {job.get('url', '')}"]
    if job.get("match_score") is not None:
        lines.append(f"Match score: {job['match_score']}/100")
    if ctx["matched_skills"]:
        lines.append("Skills highlighted for this role: " + ", ".join(ctx["matched_skills"]))
    if ctx["missing_skills"]:
        lines.append("Gaps to address in interview prep: " + ", ".join(ctx["missing_skills"]))
    return "\n".join(lines).strip() + "\n"


def render_resume_html(ctx: dict) -> str:
    return _jinja_env().get_template("resume.html.j2").render(**ctx)


def html_to_pdf(html_path: Path, pdf_path: Path) -> bool:
    """Best-effort PDF via headless Chromium, then pandoc. Never raises."""
    chromium = (shutil.which("chromium") or shutil.which("chromium-browser")
                or shutil.which("google-chrome"))
    if chromium:
        try:
            subprocess.run(
                [chromium, "--headless=new", "--disable-gpu", "--no-sandbox",
                 "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
                 html_path.resolve().as_uri()],
                check=True, capture_output=True, timeout=90,
            )
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                return True
        except (subprocess.SubprocessError, OSError):
            pass
    pandoc = shutil.which("pandoc")
    if pandoc:
        try:
            subprocess.run(
                [pandoc, str(html_path), "-o", str(pdf_path)],
                check=True, capture_output=True, timeout=90,
            )
            return pdf_path.exists() and pdf_path.stat().st_size > 0
        except (subprocess.SubprocessError, OSError):
            pass
    return False


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def tailor_job(job: dict, profile: dict, out_root: Path | None = None,
               make_pdf: bool = True) -> dict:
    """Generate the full application pack for one job. Returns a manifest."""
    if not profile:
        raise RuntimeError("a master profile is required to tailor an application")
    out_root = Path(out_root or APPLICATIONS_DIR)
    job_id = str(job.get("id") or job.get("job_id") or "unknown")
    out_dir = out_root / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = build_context(job, profile)

    files: dict[str, str] = {}
    resume_md = render_resume_markdown(ctx)
    (out_dir / "resume.md").write_text(resume_md)
    files["resume_md"] = str(out_dir / "resume.md")

    resume_html = render_resume_html(ctx)
    (out_dir / "resume.html").write_text(resume_html)
    files["resume_html"] = str(out_dir / "resume.html")

    if make_pdf and html_to_pdf(out_dir / "resume.html", out_dir / "resume.pdf"):
        files["resume_pdf"] = str(out_dir / "resume.pdf")

    cover_md = render_cover_letter(ctx)
    (out_dir / "cover_letter.md").write_text(cover_md)
    (out_dir / "cover_letter.txt").write_text(cover_md)
    files["cover_letter_md"] = str(out_dir / "cover_letter.md")
    files["cover_letter_txt"] = str(out_dir / "cover_letter.txt")

    email_text = render_email(ctx)
    (out_dir / "email.txt").write_text(email_text)
    files["email_txt"] = str(out_dir / "email.txt")

    (out_dir / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False))
    files["job_json"] = str(out_dir / "job.json")

    manifest = {
        "job_id": job_id,
        "job_title": job.get("title", ""),
        "company": job.get("company", ""),
        "url": job.get("url", ""),
        "match_score": job.get("match_score"),
        "recipient": ctx["recipient"],
        "subject": ctx["subject"],
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "files": files,
    }
    (out_dir / "meta.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


def tailor_from_turso(job_id: str, db_url: str, token: str, profile: dict,
                      out_root: Path | None = None, make_pdf: bool = True) -> dict:
    job = turso.get_job(db_url, token, job_id)
    if not job:
        raise RuntimeError(f"job {job_id} not found in the database")
    manifest = tailor_job(job, profile, out_root=out_root, make_pdf=make_pdf)
    manifest["db"] = True
    return manifest


def record_draft(db_url: str, token: str, manifest: dict) -> None:
    """Persist the draft in the applications table, preserving prior progress.

    Re-tailoring a job must not downgrade an ``applied``/``interview`` state
    back to ``draft``, so we read any existing row first and carry its status,
    applied_at, and created_at forward.
    """
    existing = {}
    try:
        existing = turso.get_application(db_url, token, manifest["job_id"]) or {}
    except Exception:  # noqa: BLE001 - a lookup failure shouldn't block drafting
        existing = {}
    app = {
        "job_id": manifest["job_id"],
        "status": existing.get("status") or "draft",
        "method": "email" if manifest.get("recipient") else "manual",
        "recipient": manifest.get("recipient") or "",
        "subject": manifest.get("subject") or "",
        "resume_path": manifest.get("files", {}).get("resume_pdf")
        or manifest.get("files", {}).get("resume_html") or "",
        "cover_path": manifest.get("files", {}).get("cover_letter_md") or "",
        "email_path": manifest.get("files", {}).get("email_txt") or "",
        "match_score": manifest.get("match_score"),
    }
    if existing.get("applied_at"):
        app["applied_at"] = existing["applied_at"]
    if existing.get("created_at"):
        app["created_at"] = existing["created_at"]
    turso.upsert_application(db_url, token, app)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _load_db_args(args) -> tuple[str | None, str | None]:
    import os
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return (args.turso_url or os.environ.get("TURSO_URL"),
            args.turso_token or os.environ.get("TURSO_TOKEN"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    tailor = sub.add_parser("tailor", help="Generate an application pack")
    tailor.add_argument("--job-id", default=None, help="Job id stored in Turso")
    tailor.add_argument("--job-json", default=None, help="Local job JSON snapshot instead of Turso")
    tailor.add_argument("--profile", default=None, help="Master profile path")
    tailor.add_argument("--out-dir", default=None, help="Output root (default applications/)")
    tailor.add_argument("--no-pdf", action="store_true", help="Skip PDF generation")
    tailor.add_argument("--turso-url", default=None)
    tailor.add_argument("--turso-token", default=None)

    listing = sub.add_parser("list", help="List generated applications from Turso")
    listing.add_argument("--status", default=None)
    listing.add_argument("--turso-url", default=None)
    listing.add_argument("--turso-token", default=None)

    status = sub.add_parser("status", help="Update an application's status")
    status.add_argument("--job-id", required=True)
    status.add_argument("--status", required=True,
                        choices=list(turso.APPLICATION_STATUSES))
    status.add_argument("--notes", default=None)
    status.add_argument("--turso-url", default=None)
    status.add_argument("--turso-token", default=None)

    args = parser.parse_args()

    if args.command == "tailor":
        profile = master_profile.load_profile(args.profile)
        if not profile:
            parser.error("no usable profile found — create profile/master_profile.json first")
        out_root = Path(args.out_dir) if args.out_dir else None
        if args.job_json:
            job = json.loads(Path(args.job_json).read_text())
            manifest = tailor_job(job, profile, out_root=out_root, make_pdf=not args.no_pdf)
        else:
            db_url, token = _load_db_args(args)
            if not (args.job_id and db_url and token):
                parser.error("tailor needs --job-id with Turso configured, or --job-json")
            manifest = tailor_from_turso(args.job_id, db_url, token, profile,
                                         out_root=out_root, make_pdf=not args.no_pdf)
            record_draft(db_url, token, manifest)
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return

    db_url, token = _load_db_args(args)
    if not (db_url and token):
        parser.error("TURSO_URL / TURSO_TOKEN are required for this command")
    if args.command == "list":
        rows = turso.list_applications(db_url, token, args.status)
        for row in rows:
            print(f"{row.get('status','?'):<10} {row.get('job_id')}  "
                  f"{row.get('match_score') or '-':>3}  {row.get('subject') or ''}")
        print(f"\n{len(rows)} application(s)")
    elif args.command == "status":
        turso.update_application_status(db_url, token, args.job_id, args.status, args.notes)
        print(f"  [info] {args.job_id} → {args.status}")


if __name__ == "__main__":
    main()
