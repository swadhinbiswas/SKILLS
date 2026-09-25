"""
telegram_job_formatter.py
==========================

Turns the raw EU job-search dump (the "JOBSEEKING" export) into properly
formatted Telegram output.

Telegram has no real <table> tag, so "table design" here means two things
depending on mode:

  BASIC MODE (format_static_messages)
      Plain text messages using Telegram's HTML parse_mode:
        - a monospaced <pre> block for the summary counts (the closest
          thing Telegram has to a table)
        - real clickable <a href="URL">Title</a> links instead of the
          "Title ... Apply (https://...)" text-URL pattern
        - jobs grouped under bold category headers
        - auto-split into multiple messages so none exceeds Telegram's
          4096-character message limit

  ADVANCED MODE (build_bot)
      A small interactive python-telegram-bot (v20+) application:
        - /jobs shows category buttons
        - picking a category shows a paginated list (one InlineKeyboardButton
          per job, the button itself opens the "Apply" URL) with
          ◀️ Prev / Next ▶️ navigation that EDITS the same message instead
          of spamming the chat
        - a "back to categories" button to switch category without re-running
          the command

Input format
------------
The export is the raw HTML the channel was sent (from `render_report_telegram()`
in job_search.py), e.g.:

    <b>🛠️ Data Engineer</b>
    • <b>~ Senior Data Engineer ⚠️</b> · (Mid+) · Acme GmbH &amp; Co · Berlin · 🏢 On-site/Hybrid · ✈️ Relocation · 2026-07-30 · 💰 €60000-75000 · <a href="https://...">Apply</a>
    <b>💻 Software Engineer</b>
    • <b>Software Engineer</b> · (Junior) · Remote Inc · Remote · 🌍 Remote · <a href="https://...">Apply</a>

HTML entities are unescaped before parsing and re-escaped on output, so a
Telegram HTML export (where <b>/<a> appear as &lt;b&gt;/&lt;a&gt;) also works.

Requirements for advanced mode only:
    pip install "python-telegram-bot>=20.0" --break-system-packages

Usage
-----
    # parse + print basic-mode messages for a raw export file
    python telegram_job_formatter.py basic export.txt

    # parse + print basic-mode messages AND send them via a bot
    python telegram_job_formatter.py basic export.txt --token <BOT_TOKEN> --chat-id <CHAT_ID>

    # run the interactive bot (long-polling)
    python telegram_job_formatter.py advanced export.txt --token <BOT_TOKEN>
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

TELEGRAM_MSG_LIMIT = 4096
JOBS_PER_PAGE = 8  # for advanced/paginated mode


# ---------------------------------------------------------------------------
# 1. Data model
# ---------------------------------------------------------------------------

@dataclass
class Job:
    category: str
    title: str
    company: str
    location: str
    work_mode: str
    date: str
    salary: Optional[str]
    url: str
    level: str = ""
    is_new: bool = False

    def to_dict(self) -> dict:
        return self.__dict__

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        return cls(**d)


# ---------------------------------------------------------------------------
# 2. Parsing the raw export text
# ---------------------------------------------------------------------------

_PREFIX_RE = re.compile(r'^\[.*?\]\s*JOBSEEKING:\s*')          # chat export prefix

# Category header, e.g. "<b>🛠️ Data Engineer</b>".
_CATEGORY_RE = re.compile(r'<b>(?:🛠️|⚙️|💻|📦)\s*(?P<name>[^<]+)</b>')

# One posting line as emitted by job_search.render_report_telegram(), e.g.:
#   • <b>~ Senior Data Engineer ⚠️</b> · Acme GmbH &amp; Co · Berlin · 🏢 On-site/Hybrid ·
#     ✈️ Relocation · 2026-07-30 · 💰 €60000-75000 · <a href="https://...">Apply</a>
# Optional fields (relocation / date / salary) may be absent in any combination.
# The Apply link may also appear as plain text: "Apply (https://...)".
_LINE_RE = re.compile(
    r'^•\s*'
    r'<b>(?P<title>.*?)</b>\s*'
    r'(?:·\s*\((?P<level>[^)]+)\)\s*)?'
    r'·\s*(?P<company>.*?)\s*'
    r'·\s*(?P<location>.*?)\s*'
    r'·\s*(?P<mode>(?:🏢|🌍)\s*[^·]*?)\s*'
    r'(?:·\s*✈️\s*Relocation\s*)?'
    r'(?:·\s*(?P<date>\d{4}-\d{2}-\d{2})\s*)?'
    r'(?:·\s*💰\s*(?P<salary>[^·]*?)\s*)?'
    r'·\s*(?:<a href="(?P<url>[^"]+)">Apply</a>|Apply\s*\((?P<url2>https?://\S+)\)|(?P<url3>https?://\S+))\s*$'
)


def _decode_export(raw_text: str) -> str:
    """A Telegram HTML export escapes the message HTML (<b> -> &lt;b&gt;).
    Detect that form and decode it once, back to the raw sent message text
    (which is itself HTML). Raw message dumps are returned unchanged."""
    if "&lt;b&gt;" in raw_text or "&lt;a " in raw_text:
        return html.unescape(raw_text)
    return raw_text


def parse_jobs(raw_text: str) -> List[Job]:
    """Parse the raw JOBSEEKING export dump into a list of Job records."""
    raw_text = _decode_export(raw_text)
    jobs: List[Job] = []
    current_category = "General"

    for raw_line in raw_text.splitlines():
        line = _PREFIX_RE.sub("", raw_line.strip())
        if not line:
            continue

        cat_match = _CATEGORY_RE.match(line)
        if cat_match:
            current_category = html.unescape(cat_match.group("name").strip())
            continue

        m = _LINE_RE.match(line)
        if not m:
            continue  # skips the header/footer/source lines, safely

        title = html.unescape(m.group("title").strip())
        is_new = "⚠️" in title
        title = title.replace("~", "").replace("⚠️", "").strip()

        url = html.unescape(m.group("url") or m.group("url2") or m.group("url3"))

        jobs.append(Job(
            category=current_category,
            title=title,
            company=html.unescape(m.group("company").strip()) or "Unknown",
            location=html.unescape(m.group("location").strip()),
            work_mode=m.group("mode").strip(),
            date=m.group("date") or "",
            salary=html.unescape((m.group("salary") or "").strip()) or None,
            url=url,
            level=html.unescape((m.group("level") or "").strip()),
            is_new=is_new,
        ))

    return jobs


def load_jobs(path: str) -> List[Job]:
    """Load jobs from either a raw .txt/.html export or a pre-parsed .json file."""
    with open(path, encoding="utf-8") as f:
        content = f.read()
    if path.endswith(".json"):
        return [Job.from_dict(d) for d in json.loads(content)]
    return parse_jobs(content)


# ---------------------------------------------------------------------------
# 3. BASIC MODE — static HTML messages, chunked to the Telegram limit
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _summary_table(jobs: List[Job]) -> str:
    """A compact monospaced 'table' of category counts (Telegram's closest
    equivalent to a real <table>, rendered via <pre>)."""
    counts = Counter(j.category for j in jobs)
    new_counts = Counter(j.category for j in jobs if j.is_new)
    name_w = max(len(c) for c in counts) + 2

    lines = [f"{'CATEGORY'.ljust(name_w)}TOTAL  NEW"]
    lines.append("-" * (name_w + 11))
    for cat, total in counts.items():
        lines.append(f"{cat.ljust(name_w)}{str(total).ljust(7)}{new_counts.get(cat, 0)}")

    return "<pre>" + _esc("\n".join(lines)) + "</pre>"


def _job_line(j: Job) -> str:
    new_tag = " 🆕" if j.is_new else ""
    meta = []
    if j.level:
        meta.append(f"🎚 {j.level}")
    if j.date:
        meta.append(j.date)
    if j.salary:
        meta.append(f"💰 {_esc(j.salary)}")
    meta_str = (" · " + " · ".join(meta)) if meta else ""
    return (
        f'• <a href="{html.escape(j.url, quote=True)}">{_esc(j.title)}</a>{new_tag}\n'
        f"  {_esc(j.company)} — {_esc(j.location)}\n"
        f"  {_esc(j.work_mode)}{meta_str}\n"
    )


def format_static_messages(jobs: List[Job], heading: str = "🔍 EU Job Search") -> List[str]:
    """Build a list of HTML-formatted strings, each safe to send as one
    Telegram message (parse_mode='HTML'), grouped by category and
    auto-split so none exceeds TELEGRAM_MSG_LIMIT characters."""
    by_category: Dict[str, List[Job]] = defaultdict(list)
    for j in jobs:
        by_category[j.category].append(j)

    messages: List[str] = []
    current = f"<b>{_esc(heading)}</b>\n{_summary_table(jobs)}\n"

    for category, cat_jobs in by_category.items():
        header = f"\n<b>{_esc(category)}</b> ({len(cat_jobs)})\n"
        block = header
        for j in cat_jobs:
            line = _job_line(j) + "\n"
            # flush block if a single category itself grows past the limit
            if len(current) + len(block) + len(line) > TELEGRAM_MSG_LIMIT:
                messages.append(current + block)
                current = ""
                block = f"<b>{_esc(category)} (cont'd)</b>\n"
            block += line

        if len(current) + len(block) > TELEGRAM_MSG_LIMIT:
            messages.append(current)
            current = block
        else:
            current += block

    if current.strip():
        messages.append(current)

    return messages


# ---------------------------------------------------------------------------
# 4. ADVANCED MODE — interactive bot with inline keyboards (python-telegram-bot)
# ---------------------------------------------------------------------------
#
# Kept in its own function so `import telegram_job_formatter` works even if
# python-telegram-bot isn't installed (only needed for advanced mode).

def build_bot(jobs: List[Job], token: str):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (
        Application, CallbackQueryHandler, CommandHandler, ContextTypes,
    )

    by_category: Dict[str, List[Job]] = defaultdict(list)
    for j in jobs:
        by_category[j.category].append(j)
    categories = sorted(by_category)

    def categories_keyboard() -> InlineKeyboardMarkup:
        rows = [
            [InlineKeyboardButton(f"{c} ({len(by_category[c])})", callback_data=f"cat:{c}:0")]
            for c in categories
        ]
        return InlineKeyboardMarkup(rows)

    def page_keyboard(category: str, page: int) -> InlineKeyboardMarkup:
        cat_jobs = by_category[category]
        start = page * JOBS_PER_PAGE
        page_jobs = cat_jobs[start:start + JOBS_PER_PAGE]

        rows = []
        for j in page_jobs:
            label = f"{'🆕 ' if j.is_new else ''}{j.title} — {j.company}"
            rows.append([InlineKeyboardButton(label[:64], url=j.url)])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"cat:{category}:{page - 1}"))
        if start + JOBS_PER_PAGE < len(cat_jobs):
            nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"cat:{category}:{page + 1}"))
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("⬅️ Categories", callback_data="home")])

        return InlineKeyboardMarkup(rows)

    def page_text(category: str, page: int) -> str:
        cat_jobs = by_category[category]
        total_pages = (len(cat_jobs) - 1) // JOBS_PER_PAGE + 1
        return (
            f"<b>{html.escape(category)}</b>\n"
            f"Page {page + 1}/{total_pages} · tap a job to open its Apply link"
        )

    async def cmd_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            f"<b>🔍 EU Job Search</b>\n{len(jobs)} postings — pick a category:",
            parse_mode="HTML",
            reply_markup=categories_keyboard(),
        )

    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()

        if query.data == "home":
            await query.edit_message_text(
                f"<b>🔍 EU Job Search</b>\n{len(jobs)} postings — pick a category:",
                parse_mode="HTML",
                reply_markup=categories_keyboard(),
            )
            return

        _, category, page_str = query.data.split(":", 2)
        page = int(page_str)
        await query.edit_message_text(
            page_text(category, page),
            parse_mode="HTML",
            reply_markup=page_keyboard(category, page),
        )

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CallbackQueryHandler(on_callback))
    return app


# ---------------------------------------------------------------------------
# 5. CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["basic", "advanced"])
    parser.add_argument("source", help="raw .txt/.html export or pre-parsed .json file")
    parser.add_argument("--token", help="Telegram bot token")
    parser.add_argument("--chat-id", help="chat id to send basic-mode messages to")
    args = parser.parse_args()

    jobs = load_jobs(args.source)
    if not jobs:
        print("No jobs parsed — check the input file.", file=sys.stderr)
        sys.exit(1)

    if args.mode == "basic":
        messages = format_static_messages(jobs)
        if args.token and args.chat_id:
            from telegram import Bot
            import asyncio

            async def _send():
                bot = Bot(args.token)
                for msg in messages:
                    await bot.send_message(
                        chat_id=args.chat_id, text=msg,
                        parse_mode="HTML", disable_web_page_preview=True,
                    )
            asyncio.run(_send())
        else:
            for i, msg in enumerate(messages, 1):
                print(f"--- message {i}/{len(messages)} ({len(msg)} chars) ---")
                print(msg)
    else:
        if not args.token:
            print("advanced mode needs --token", file=sys.stderr)
            sys.exit(1)
        app = build_bot(jobs, args.token)
        app.run_polling()


if __name__ == "__main__":
    main()
