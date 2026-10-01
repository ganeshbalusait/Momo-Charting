"""Pre-commit gate: nothing ships without a release-notes entry.

Ganesh, 2026-09-01: "Every fixes or features I need release notes" and then
"Without release notes don't update, do something like mandatory". The
in-app Release Notes page (above Settings) reads
frontend/public/release-notes.json and shows ONLY what is written there; on
2026-09-01 thirty-odd fixes shipped and not one line was written, so the page
lied by omission for a whole day.

The rule this enforces, on every commit:

    if the commit changes anything that SHIPS (app code, backend, scripts)
    then frontend/public/release-notes.json must be staged too, with a NEW
    entry at the top, stamped within the last 24 hours, with a heading and
    at least one item.

What does not count as shipping: tests, docs/, artifacts/, markdown, the
memory folder, CI config, and the release-notes file itself. A commit made
only of those passes without notes.

There is deliberately no environment-variable bypass. Git's own
`--no-verify` exists and is loud enough; the repo rule is not to use it
unless he asks.

Exit 0 = commit allowed. Exit 1 = commit refused, with the reason printed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath

NOTES_PATH = "frontend/public/release-notes.json"

#: Path prefixes / suffixes that never count as "shipping something".
NON_SHIPPING_PREFIXES = (
    "tests/",
    "docs/",
    "artifacts/",
    "memory/",
    ".github/",
    ".claude/",
    "scripts/git-hooks/",
)
NON_SHIPPING_SUFFIXES = (".md", ".txt", ".jsonl", ".log", ".png", ".jpg")
NON_SHIPPING_EXACT = {
    NOTES_PATH,
    ".gitignore",
    ".gitattributes",
    "scripts/check_release_notes.py",
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=False, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def staged_paths() -> list[str]:
    out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMRD")
    return [line.strip() for line in out.splitlines() if line.strip()]


def ships(path: str) -> bool:
    """True if a change to ``path`` is something the trader could notice."""
    posix = PurePosixPath(path).as_posix()
    if posix in NON_SHIPPING_EXACT:
        return False
    if posix.startswith(NON_SHIPPING_PREFIXES):
        return False
    if posix.endswith(NON_SHIPPING_SUFFIXES):
        return False
    name = PurePosixPath(posix).name
    if ".test." in name or name.startswith("test_") or name.endswith("_test.py"):
        return False
    if name.startswith("_patch") or name.startswith("_probe") or name.startswith("probe-"):
        return False
    return True


def load_json(ref: str) -> dict | None:
    """``ref`` is '' for the index (git's ':path'), 'HEAD' for the last commit."""
    raw = _git("show", f"{ref}:{NOTES_PATH}")
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def top_entry(data: dict | None) -> dict | None:
    releases = (data or {}).get("releases")
    if not isinstance(releases, list) or not releases:
        return None
    entry = releases[0]
    return entry if isinstance(entry, dict) else None


def parse_stamp(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None  # an entry without a zone cannot be placed on the ET day
    return stamp


def say(text: str) -> None:
    """Print without ever crashing on the console's encoding.

    The hook runs under whatever code page git hands it - cp1252 here - and a
    heading with an emoji in it (2026-09-02, "Click the [bolt] ...") raised
    UnicodeEncodeError from print() and BLOCKED THE COMMIT. A gate that
    refuses work because of the terminal's character set is worse than no
    gate; the check itself never needed the characters.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "ascii"
    print(text.encode(encoding, "replace").decode(encoding, "replace"))


def refuse(reason: str, shipping: list[str]) -> int:
    say("")
    say("COMMIT REFUSED - release notes are mandatory for anything that ships.")
    say("")
    say("  " + reason)
    say("")
    say("  Shipping files in this commit:")
    for path in shipping[:12]:
        say("    - " + path)
    if len(shipping) > 12:
        say(f"    ... and {len(shipping) - 12} more")
    say("")
    say("  Add a NEW entry at the TOP of " + NOTES_PATH + ":")
    say('    { "at": "<now, ET, e.g. 2026-09-01T23:40:00-04:00>",')
    say('      "heading": "<what he would notice, one line>",')
    say('      "subheading": "<optional>",')
    say('      "items": ["<one plain-language line per fix>"] }')
    say("  then `git add " + NOTES_PATH + "` and commit again.")
    say("")
    return 1


def main() -> int:
    staged = staged_paths()
    shipping = [path for path in staged if ships(path)]
    if not shipping:
        return 0

    if NOTES_PATH not in staged:
        return refuse(NOTES_PATH + " is not part of this commit.", shipping)

    staged_notes = load_json("")
    if staged_notes is None:
        return refuse(NOTES_PATH + " (staged) is not valid JSON with a top-level object.", shipping)

    new_top = top_entry(staged_notes)
    if new_top is None:
        return refuse("The staged release notes have no entries in 'releases'.", shipping)

    old_top = top_entry(load_json("HEAD"))
    if old_top is not None and new_top == old_top:
        return refuse(
            "The newest entry is unchanged from the last commit - nothing was added for this deploy.",
            shipping,
        )

    stamp = parse_stamp(new_top.get("at"))
    if stamp is None:
        return refuse("The newest entry's 'at' is missing or not an ISO time with a zone.", shipping)
    age = datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)
    if age > timedelta(hours=24):
        return refuse(
            f"The newest entry is stamped {new_top.get('at')} - more than 24h ago. Stamp this deploy.",
            shipping,
        )
    if age < timedelta(hours=-1):
        return refuse(f"The newest entry is stamped in the future: {new_top.get('at')}.", shipping)

    heading = str(new_top.get("heading") or "").strip()
    items = [str(item).strip() for item in (new_top.get("items") or []) if str(item or "").strip()]
    if not heading:
        return refuse("The newest entry has an empty heading.", shipping)
    if not items:
        return refuse("The newest entry has no items - say what changed.", shipping)

    say(f"release notes ok: \"{heading}\" ({len(items)} item{'s' if len(items) != 1 else ''})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
