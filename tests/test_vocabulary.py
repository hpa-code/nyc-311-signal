"""User-facing text uses the glossary's terms: none of the unambiguous
_Avoid_ words from GLOSSARY.md (CODING_STANDARDS.md, Vocabulary). Words that
are only wrong in one sense ("snapshot", "ticket") are left to review. Quote a
banned word on purpose by ending its line with `glossary-ok`."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# README, the docs a reviewer reads, and the dashboard's text. GLOSSARY.md
# names these words; instruction.md and docs/agents/ are written for the
# builders, not the public.
SCANNED = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), *sorted((ROOT / "dashboard").rglob("*.py"))]
AVOID = {
    r"\btargets?\b": "Label",
    r"\brisk scores?\b": "Admission Score",
    r"\bpriority queues?\b": "Risk Queue",
    r"\btriage lists?\b": "Risk Queue",
    r"\bSLAs?\b": "Seven-Day Horizon",
    r"\bdeadlines?\b": "Seven-Day Horizon",
    r"\bmissed SLA\b": "Delayed",
}


def test_user_facing_text_uses_glossary_terms():
    found = []
    for path in SCANNED:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if line.rstrip().endswith("glossary-ok"):
                continue
            for pattern, term in AVOID.items():
                if match := re.search(pattern, line, re.IGNORECASE):
                    found.append(f"{path.relative_to(ROOT)}:{number}: '{match.group()}' (use {term})")
    assert not found, "GLOSSARY.md _Avoid_ words:\n" + "\n".join(found)
