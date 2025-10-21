param(
    [string]$Message = "Update: apply Copilot changes",
    [switch]$Tag
)

# Safety: prevent committing large data files by mistake; tweak if needed
$block = @("*.xlsx","*.xls","*.csv","*.zip")
$staged = git diff --name-only --cached
if (-not $staged) {
    # Stage only code files by default
    git add app.py *.py -A
}

# Optional safety tag/branch
if ($Tag) {
    $ts = Get-Date -Format "yyyyMMdd-HHmmss"
    git tag -a "pre-update-$ts" -m "Pre-update tag $ts"
}

git commit -m $Message 2>$null
git push origin main --force
