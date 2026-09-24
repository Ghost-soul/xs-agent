[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("openai", "openai_compatible", "deepseek")]
    [string]$Provider
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot
$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".uv\cache"

$UvPython = "python"
$BootstrapPython = Join-Path $ProjectRoot ".uv\bootstrap\Scripts\python.exe"
if (Test-Path $BootstrapPython) { $UvPython = $BootstrapPython }

& $UvPython -m uv run --frozen python -m novel_writer.core.credentials $Provider
if ($LASTEXITCODE -ne 0) { throw "Could not store the API key." }