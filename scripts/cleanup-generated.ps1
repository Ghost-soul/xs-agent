[CmdletBinding(SupportsShouldProcess)]
param()

$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$ProjectPrefix = $ProjectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
$script:Skipped = 0

function Assert-WorkspacePath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $Resolved = [IO.Path]::GetFullPath($Path)
    if (-not $Resolved.StartsWith($ProjectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean path outside the workspace: $Resolved"
    }
    return $Resolved
}

function Remove-GeneratedPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    $SafePath = Assert-WorkspacePath -Path $Path
    if (-not (Test-Path -LiteralPath $SafePath)) { return 0 }
    if ($PSCmdlet.ShouldProcess($SafePath, "Remove reproducible generated artifact")) {
        try {
            Remove-Item -LiteralPath $SafePath -Recurse -Force
        } catch [System.UnauthorizedAccessException] {
            $script:Skipped += 1
            Write-Warning "Skipped inaccessible generated artifact: $SafePath"
            return 0
        }
        Write-Host "Removed $SafePath"
    }
    return 1
}

$Removed = 0
$KnownDirectories = @(
    (Join-Path $ProjectRoot ".mypy_cache"),
    (Join-Path $ProjectRoot ".pytest_cache"),
    (Join-Path $ProjectRoot ".ruff_cache"),
    (Join-Path $ProjectRoot "frontend\dist"),
    (Join-Path $ProjectRoot ".runtime\chrome-approval-mobile"),
    (Join-Path $ProjectRoot ".runtime\chrome-cdp-qa")
)
foreach ($Directory in $KnownDirectories) {
    $Removed += Remove-GeneratedPath -Path $Directory
}

foreach ($SearchRoot in @("src", "tests", "scripts", "migrations")) {
    $AbsoluteSearchRoot = Assert-WorkspacePath -Path (Join-Path $ProjectRoot $SearchRoot)
    if (-not (Test-Path -LiteralPath $AbsoluteSearchRoot)) { continue }
    Get-ChildItem -LiteralPath $AbsoluteSearchRoot -Directory -Filter "__pycache__" -Recurse -Force |
        ForEach-Object { $script:Removed += Remove-GeneratedPath -Path $_.FullName }
}

$FrontendRoot = Assert-WorkspacePath -Path (Join-Path $ProjectRoot "frontend")
if (Test-Path -LiteralPath $FrontendRoot) {
    Get-ChildItem -LiteralPath $FrontendRoot -File -Filter "*.tsbuildinfo" -Recurse -Force |
        Where-Object { $_.FullName -notlike (Join-Path $FrontendRoot "node_modules\*") } |
        ForEach-Object { $script:Removed += Remove-GeneratedPath -Path $_.FullName }
}

Write-Host "Cleanup complete. Matched $Removed reproducible artifact path(s)."
Write-Host "Skipped $script:Skipped inaccessible generated artifact path(s)."
Write-Host "Protected and untouched: data, data/content/provider-outputs, logs, .env, active .runtime files, PostgreSQL state, migrations, node_modules, and .uv dependency runtimes."
