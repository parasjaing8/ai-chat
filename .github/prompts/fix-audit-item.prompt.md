---
description: "Fix one finding from audit4April.md: read the finding, locate the code, apply the fix, verify. Run with /fix-audit-item <finding-id> e.g. /fix-audit-item 1.1"
argument-hint: "<finding-id>  e.g. 1.1, 2.3, 5.6"
agent: "agent"
tools: [read_file, grep_search, replace_string_in_file, multi_replace_string_in_file, get_errors]
---

Fix audit finding **$ARGUMENTS** from [audit4April.md](../audit4April.md).

## Steps

1. **Read the finding**
   Open [audit4April.md](../audit4April.md) and locate section `$ARGUMENTS`. Quote the full finding: severity, location, description, and prescribed fix.

2. **Locate the code**
   Read the exact lines cited in the finding. Show the current code as a labelled block so the problem is visible before touching anything.

3. **Apply the fix**
   Implement exactly what the audit prescribes — no more, no less. Follow all conventions in [copilot-instructions.md](./copilot-instructions.md):
   - No new abstractions unless the fix requires them
   - No unrelated refactors
   - Prefer `multi_replace_string_in_file` for multiple edits in one file

4. **Verify**
   - Run `get_errors` on the edited file(s) and confirm zero new errors introduced
   - For security fixes (CRITICAL severity), re-read the patched function and confirm the vulnerability is closed
   - Quote the final patched code

5. **Update the audit**
   Mark the finding as resolved in [audit4April.md](../audit4April.md) by appending `✓ Fixed <date>` to the finding header line.

## Output format

```
### Finding $ARGUMENTS — <title>
Severity: <severity>
Location: <file:lines>

**Before:**
<code block>

**After:**
<code block>

**Why this closes the issue:** <one sentence>
```
