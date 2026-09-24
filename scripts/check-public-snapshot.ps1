[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Get-Command "git" -ErrorAction SilentlyContinue)) {
    throw "git is required."
}

$TrackedFiles = @(git ls-files)
if ($LASTEXITCODE -ne 0) { throw "Could not list tracked files." }

$ForbiddenPaths = @(
    "AGENTS.md",
    "MEMORY.md",
    "PROGRESS.md",
    "ROADMAP.md",
    "docs/CURRENT_STATE.md",
    "docs/history/",
    "docs/reports/",
    "data/",
    "logs/",
    "backups/",
    "exports/",
    "archives/",
    "artifacts/",
    "private/",
    "docs/private/"
)

$Failures = [Collections.Generic.List[string]]::new()
foreach ($File in $TrackedFiles) {
    $Normalized = $File.Replace("\", "/")
    if ($Normalized -eq ".env" -or ($Normalized.StartsWith(".env.") -and $Normalized -ne ".env.example")) {
        $Failures.Add("forbidden environment file: $File")
    }
    foreach ($Forbidden in $ForbiddenPaths) {
        if ($Normalized -eq $Forbidden.TrimEnd("/") -or $Normalized.StartsWith($Forbidden)) {
            $Failures.Add("private path is tracked: $File")
            break
        }
    }
    if ($Normalized -match '(?i)\.(pem|key|pfx|p12|jks|keystore|dump|backup|sqlite3?|db|zip|7z)$') {
        $Failures.Add("sensitive artifact type is tracked: $File")
    }
}

$SecretPatterns = [ordered]@{
    private_key = '-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'
    github_token = '(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
    openai_key = 'sk-(proj-|svcacct-)?[A-Za-z0-9_-]{20,}'
    aws_access_key = 'AKIA[0-9A-Z]{16}'
    google_api_key = 'AIza[0-9A-Za-z_-]{30,}'
    slack_token = 'xox[baprs]-[0-9A-Za-z-]{10,}'
    jwt = 'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
    user_home_path = '(?i)([A-Z]:\\Users\\[^\\\s]+|/[U]sers/[^/\s]+|/[h]ome/[^/\s]+)'
}

foreach ($File in $TrackedFiles) {
    if (-not (Test-Path -LiteralPath $File -PathType Leaf)) { continue }
    try {
        $Content = Get-Content -Raw -LiteralPath $File -Encoding UTF8 -ErrorAction Stop
    } catch {
        continue
    }
    foreach ($Pattern in $SecretPatterns.GetEnumerator()) {
        if ($Content -match $Pattern.Value) {
            $Failures.Add("$($Pattern.Key) pattern found: $File")
        }
    }
}

if ($Failures.Count -gt 0) {
    $Failures | Sort-Object -Unique | ForEach-Object { Write-Error $_ }
    throw "Public snapshot check failed with $($Failures.Count) finding(s)."
}

Write-Host "Public snapshot check passed: $($TrackedFiles.Count) tracked files inspected."
