#!/usr/bin/env bash
# check-project-paths.sh
# PreToolUse hook — enforces Rule 4 (relative asset paths only) for files written
# inside projects/<slug>/src/. Raises an "ask" gate on violation.

set -euo pipefail

raw=$(cat)
[ -z "$raw" ] && exit 0

tool=$(echo "$raw"     | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null || true)
[ -z "$tool" ] && exit 0

# Extract (path, content) pairs per tool
get_candidates() {
    python3 - "$tool" <<'PYEOF'
import sys, json, os

tool = sys.argv[1]
raw  = os.environ.get("HOOK_RAW", "")
try:
    d = json.loads(raw)
except Exception:
    sys.exit(0)

ti = d.get("tool_input", {})
pairs = []

if tool == "create_file":
    if ti.get("filePath") and ti.get("content"):
        pairs.append((ti["filePath"], ti["content"]))
elif tool == "replace_string_in_file":
    if ti.get("filePath") and ti.get("newString"):
        pairs.append((ti["filePath"], ti["newString"]))
elif tool == "multi_replace_string_in_file":
    for r in ti.get("replacements", []):
        if r.get("filePath") and r.get("newString"):
            pairs.append((r["filePath"], r["newString"]))

import re
patterns = [
    (r'src=["\x27]/[^/"\x27\s]',             'Absolute src= path. Use src="js/..."'),
    (r'href=["\x27]/[^/"\x27\s]',            'Absolute href= path. Use href="css/..."'),
    (r"url\(\s*['\"]?/[^'\"\)\s]",           'Absolute CSS url() path. Use relative url()'),
    (r'from\s+["\x27]/|require\(\s*["\x27]/', 'Absolute JS import/require path'),
    (r'(src|href|url)\s*[=(]["\x27](?:[A-Za-z]:\\|/Users/|/home/)', 'Absolute filesystem path'),
]

violations = []
for path, content in pairs:
    norm = path.replace("\\", "/")
    if not re.search(r'/projects/[^/]+/src/', norm):
        continue
    for regex, msg in patterns:
        if re.search(regex, content):
            short = re.sub(r'.*(projects/)', r'\1', norm)
            violations.append(f"[{short}] {msg}")

if not violations:
    sys.exit(0)

reason = "Rule 4 violation — absolute paths detected in project source file(s):\n"
reason += "\n".join(f"  • {v}" for v in violations)
reason += "\n\nFix: use relative paths (src=\"js/game.js\", href=\"css/style.css\")."
reason += "\nProjects are served via /play/<slug>/ — absolute paths will 404."

import json as _json
out = {
    "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason
    }
}
print(_json.dumps(out))
PYEOF
}

export HOOK_RAW="$raw"
get_candidates
exit 0
