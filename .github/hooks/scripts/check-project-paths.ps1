# check-project-paths.ps1
# PreToolUse hook - enforces Rule 4 (relative asset paths only) for files written
# inside projects/<slug>/src/. Raises an "ask" gate on violation so the user can
# review before the agent proceeds.

param()

# --- Read stdin ---
$raw = [Console]::In.ReadToEnd()
if (-not $raw.Trim()) { exit 0 }

try {
    $hook = $raw | ConvertFrom-Json
} catch {
    exit 0  # Unparseable input - let it through
}

$tool      = $hook.tool_name
$toolInput = $hook.tool_input

# --- Collect (filePath, content) pairs from whichever write tool fired ---
$candidates = @()

switch ($tool) {
    "create_file" {
        if ($toolInput.filePath -and $toolInput.content) {
            $candidates += [PSCustomObject]@{ Path = $toolInput.filePath; Content = $toolInput.content }
        }
    }
    "replace_string_in_file" {
        if ($toolInput.filePath -and $toolInput.newString) {
            $candidates += [PSCustomObject]@{ Path = $toolInput.filePath; Content = $toolInput.newString }
        }
    }
    "multi_replace_string_in_file" {
        foreach ($r in $toolInput.replacements) {
            if ($r.filePath -and $r.newString) {
                $candidates += [PSCustomObject]@{ Path = $r.filePath; Content = $r.newString }
            }
        }
    }
    default { exit 0 }  # Not a write tool
}

if ($candidates.Count -eq 0) { exit 0 }

# --- Violation patterns (Rule 4 + Rule 8 from AGENT_RULES.md) ---
$patterns = @(
    @{
        Regex   = 'src=["'']/[^/"''\s]'
        Message = 'Absolute src= path (e.g. src="/js/..."). Use src="js/..."'
    },
    @{
        Regex   = 'href=["'']/[^/"''\s]'
        Message = 'Absolute href= path (e.g. href="/css/..."). Use href="css/..."'
    },
    @{
        Regex   = "url\(\s*['""]?/[^'\"")\s]"
        Message = 'Absolute CSS url() path. Use url("img/bg.png") style relative paths'
    },
    @{
        Regex   = 'from\s+["'']/|require\(\s*["'']/[^/]'
        Message = 'Absolute JS import/require path. Use relative imports'
    },
    @{
        Regex   = '(src|href|url)\s*[=(]["''](?:[A-Za-z]:\\|/Users/|/home/)'
        Message = 'Absolute filesystem path in asset reference. Mac Mini is headless - use relative paths'
    }
)

# --- Check each candidate ---
$allViolations = @()

foreach ($c in $candidates) {
    # Normalise to forward-slash for matching
    $normPath = $c.Path -replace '\\', '/'

    # Only care about files inside projects/<slug>/src/
    if ($normPath -notmatch '/projects/[^/]+/src/') { continue }

    $fileViolations = @()
    foreach ($p in $patterns) {
        if ($c.Content -match $p.Regex) {
            $fileViolations += $p.Message
        }
    }

    if ($fileViolations.Count -gt 0) {
        $shortPath = $normPath -replace '.*/(projects/)', '$1'
        $allViolations += "[$shortPath]"
        $allViolations += $fileViolations | ForEach-Object { "  - $_" }
    }
}

if ($allViolations.Count -eq 0) { exit 0 }

# --- Emit "ask" decision so the user can review before the agent proceeds ---
$reason = @(
    "Rule 4 violation - absolute paths detected in project source file(s):"
    ""
) + $allViolations + @(
    ""
    'Fix: use relative paths (src="js/game.js", href="css/style.css").'
    "Projects are served via /play/<slug>/ - absolute paths will 404."
) | Out-String

$output = @{
    hookSpecificOutput = @{
        hookEventName            = "PreToolUse"
        permissionDecision       = "ask"
        permissionDecisionReason = $reason.Trim()
    }
} | ConvertTo-Json -Compress

Write-Output $output
exit 0
