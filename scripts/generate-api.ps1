$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$env:UV_CACHE_DIR = Join-Path $ProjectRoot ".uv\cache"
$UvPython = Join-Path $ProjectRoot ".uv\bootstrap\Scripts\python.exe"
$ProjectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path $ProjectPython) {
    & $ProjectPython "$PSScriptRoot\export_openapi.py"
} else {
    & $UvPython -m uv run --frozen --offline python "$PSScriptRoot\export_openapi.py"
}
if ($LASTEXITCODE -ne 0) { throw "OpenAPI export failed." }

$FrontendRoot = Join-Path $ProjectRoot "frontend"
$OpenApiTypeScript = Join-Path $FrontendRoot "node_modules\.bin\openapi-typescript.cmd"
if (Test-Path -LiteralPath $OpenApiTypeScript) {
    & $OpenApiTypeScript (Join-Path $FrontendRoot "openapi.json") -o (Join-Path $FrontendRoot "src\generated\api.ts")
} else {
    pnpm --dir $FrontendRoot exec openapi-typescript openapi.json -o src/generated/api.ts
}
if ($LASTEXITCODE -ne 0) { throw "OpenAPI TypeScript generation failed." }
