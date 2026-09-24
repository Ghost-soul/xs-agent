[CmdletBinding()]
param(
    [switch]$Offline,
    [switch]$Integration
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".uv\cache"
$UvPython = "python"
$BootstrapPython = Join-Path $ProjectRoot ".uv\bootstrap\Scripts\python.exe"
if (Test-Path $BootstrapPython) { $UvPython = $BootstrapPython }

$UvArguments = @("-m", "uv", "run", "--frozen")
if ($Offline) { $UvArguments += "--offline" }

function Invoke-Checked([string]$Label, [scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Label failed." }
}

foreach ($Tool in @("python", "node", "pnpm", "git")) {
    if (-not (Get-Command $Tool -ErrorAction SilentlyContinue)) {
        throw "Required tool is unavailable: $Tool"
    }
}
$FrontendBin = Join-Path $ProjectRoot "frontend\node_modules\.bin"
$Vitest = Join-Path $FrontendBin "vitest.cmd"
$TypeScript = Join-Path $FrontendBin "tsc.cmd"
$Vite = Join-Path $FrontendBin "vite.cmd"
foreach ($FrontendTool in @($Vitest, $TypeScript, $Vite)) {
    if (-not (Test-Path -LiteralPath $FrontendTool)) {
        throw "Frontend dependencies are not installed; run scripts/setup.ps1 first."
    }
}
foreach ($LockFile in @("pyproject.toml", "uv.lock", "frontend/package.json", "frontend/pnpm-lock.yaml", ".python-version", ".nvmrc")) {
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $LockFile))) {
        throw "Required lock or runtime version file is missing: $LockFile"
    }
}

if ($Offline -and $Integration) {
    throw "-Offline and -Integration cannot be used together."
}

if ($Integration) {
    $Docker = (Get-Command "docker" -ErrorAction Stop).Source
    & $Docker compose -f compose.dev.yaml up -d --wait database
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL failed to start." }

    $TestDatabase = "novel_writer_test"
    if (-not $TestDatabase.EndsWith("_test", [System.StringComparison]::Ordinal)) {
        throw "Integration database name must end with _test."
    }
    $DatabaseEndpointOutput = & $Docker compose -f compose.dev.yaml port database 5432
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the published PostgreSQL port." }
    $DatabaseEndpoint = ($DatabaseEndpointOutput | Out-String).Trim()
    if ($DatabaseEndpoint -notmatch '^127\.0\.0\.1:([0-9]{1,5})$' -or
        [int]$Matches[1] -lt 1 -or [int]$Matches[1] -gt 65535) {
        throw "Expected one loopback-only PostgreSQL endpoint."
    }
    $TestDatabaseUrl = "postgresql+psycopg://novel_writer:novel_writer@$DatabaseEndpoint/$TestDatabase"
    $DatabaseExists = & $Docker compose -f compose.dev.yaml exec -T database psql -U novel_writer -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$TestDatabase'"
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect the PostgreSQL test database." }
    if (($DatabaseExists | Out-String).Trim() -ne "1") {
        & $Docker compose -f compose.dev.yaml exec -T database createdb -U novel_writer $TestDatabase
        if ($LASTEXITCODE -ne 0) { throw "Could not create the PostgreSQL test database." }
    }

    $PreviousDatabaseUrl = $env:NOVEL_WRITER_DATABASE_URL
    $PreviousTestDatabaseUrl = $env:NOVEL_WRITER_TEST_DATABASE_URL
    $PreviousIntegrationZeroSkip = $env:NOVEL_WRITER_INTEGRATION_ZERO_SKIP
    try {
        $env:NOVEL_WRITER_DATABASE_URL = $TestDatabaseUrl
        $env:NOVEL_WRITER_TEST_DATABASE_URL = $TestDatabaseUrl
        $env:NOVEL_WRITER_INTEGRATION_ZERO_SKIP = "1"
        Invoke-Checked "test database migration" { & $UvPython @UvArguments alembic upgrade head }
        Invoke-Checked "integration pytest" { & $UvPython @UvArguments pytest -m integration }
        Invoke-Checked "long-form performance gate" {
            & $UvPython @UvArguments python scripts/performance_gate.py
        }
    } finally {
        $env:NOVEL_WRITER_DATABASE_URL = $PreviousDatabaseUrl
        $env:NOVEL_WRITER_TEST_DATABASE_URL = $PreviousTestDatabaseUrl
        $env:NOVEL_WRITER_INTEGRATION_ZERO_SKIP = $PreviousIntegrationZeroSkip
    }
}

Invoke-Checked "pytest" { & $UvPython @UvArguments pytest -m "not integration" }
Invoke-Checked "ruff" { & $UvPython @UvArguments ruff check src tests scripts }
Invoke-Checked "mypy" { & $UvPython @UvArguments mypy src }
Invoke-Checked "documentation consistency" { & $UvPython @UvArguments python scripts/check-doc-consistency.py }
Invoke-Checked "unused modules" { & $UvPython @UvArguments python scripts/check-unused-modules.py }
$GeneratedApiFiles = @(
    Join-Path $ProjectRoot "frontend\openapi.json"
    Join-Path $ProjectRoot "frontend\src\generated\api.ts"
)
$GeneratedApiHashes = @{}
foreach ($GeneratedFile in $GeneratedApiFiles) {
    if (-not (Test-Path -LiteralPath $GeneratedFile)) {
        throw "Generated API file is missing before verification: $GeneratedFile"
    }
    $GeneratedApiHashes[$GeneratedFile] = (Get-FileHash -LiteralPath $GeneratedFile -Algorithm SHA256).Hash
}
Invoke-Checked "frontend API types" { pnpm --dir frontend generate:api }
foreach ($GeneratedFile in $GeneratedApiFiles) {
    $CurrentHash = (Get-FileHash -LiteralPath $GeneratedFile -Algorithm SHA256).Hash
    if ($CurrentHash -ne $GeneratedApiHashes[$GeneratedFile]) {
        throw "Generated API file was stale and changed during verification: $GeneratedFile"
    }
}
Invoke-Checked "frontend tests" {
    Push-Location frontend
    try { & $Vitest run } finally { Pop-Location }
}
Invoke-Checked "frontend typecheck" {
    Push-Location frontend
    try { & $TypeScript -b --pretty false } finally { Pop-Location }
}
Invoke-Checked "frontend build" {
    Push-Location frontend
    try { & $Vite build } finally { Pop-Location }
}
