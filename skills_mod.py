"""Skills system — keyword-based skill injection into worker prompts."""
from __future__ import annotations
import re
from pathlib import Path

SKILLS_DIR = Path(__file__).parent / "skills"


def load_skills(context: str) -> str:
    """Load skill content whose keywords match the given context string.

    Each skill file (skills/*.md) must have frontmatter with a 'keywords' line:
        keywords: ssh, remote, terminal, shell
    Matched skill bodies are appended to system prompts.
    """
    if not SKILLS_DIR.exists():
        return ""
    matched: list[str] = []
    ctx_lower = context.lower()
    for skill_file in sorted(SKILLS_DIR.glob("*.md")):
        try:
            raw = skill_file.read_text(encoding="utf-8")
            kw_match = re.search(r"^keywords:\s*(.+)$", raw, re.MULTILINE)
            if not kw_match:
                continue
            keywords = [k.strip() for k in kw_match.group(1).split(",") if k.strip()]
            if any(re.search(r'\b' + re.escape(kw) + r'\b', ctx_lower) for kw in keywords):
                body = re.sub(r"^---.*?---\s*", "", raw, flags=re.DOTALL).strip()
                if body:
                    matched.append(body)
        except Exception:
            pass
    if not matched:
        return ""
    return "\n\n---\nACTIVE SKILLS:\n" + "\n\n".join(matched)
