[CmdletBinding()]
param(
    [string]$PythonExecutable = "",
    [string]$UvPythonExecutable = "",
    [string]$UvCacheDirectory = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

function Require-Command([string]$Name, [string]$InstallHint) {
    $Command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $Command) {
        throw "$Name is required. $InstallHint"
    }
    return $Command.Source
}

$Python = if ($PythonExecutable) {
    (Get-Command $PythonExecutable -ErrorAction Stop).Source
} else {
    Require-Command "python" "Install Python 3.12 and reopen PowerShell."
}
$PreviousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$PythonVersionOutput = (& $Python --version 2>&1 | Out-String).Trim()
$PythonVersionExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousPreference
if (
    $PythonVersionExitCode -ne 0 -or
    $PythonVersionOutput -notmatch '^Python\s+3\.12(?:\.|$)'
) {
    $DisplayedVersion = if ($PythonVersionOutput) { $PythonVersionOutput } else { "no output" }
    throw "Python 3.12 is required; executable '$Python' reported: $DisplayedVersion"
}

$Node = Require-Command "node" "Install Node.js 22 LTS and reopen PowerShell."
$NodeMajor = (& $Node -p "process.versions.node.split('.')[0]").Trim()
if ($NodeMajor -ne "22") {
    throw "Node.js 22 LTS is required; found $(& $Node --version)."
}

$Pnpm = Require-Command "pnpm" "Install pnpm 10.13.1 with Corepack."
$PnpmVersion = (& $Pnpm --version).Trim()
if (-not $PnpmVersion.StartsWith("10.")) {
    throw "pnpm 10 is required; found $PnpmVersion."
}

$Docker = Require-Command "docker" "Install Docker Desktop and start it."
& $Docker version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Docker Desktop is not ready." }

$env:UV_CACHE_DIR = if ($UvCacheDirectory) {
    $CachePath = if ([IO.Path]::IsPathRooted($UvCacheDirectory)) {
        $UvCacheDirectory
    } else {
        Join-Path $ProjectRoot $UvCacheDirectory
    }
    [IO.Path]::GetFullPath($CachePath)
} else {
    Join-Path $ProjectRoot ".uv\cache"
}
$PythonHasUv = (& $Python -c "import importlib.util; print('yes' if importlib.util.find_spec('uv') else 'no')").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the selected Python interpreter for uv support."
}
if ($UvPythonExecutable) {
    $UvPython = (Get-Command $UvPythonExecutable -ErrorAction Stop).Source
} elseif ($PythonHasUv -eq "yes") {
    $UvPython = $Python
} else {
    $BootstrapPython = Join-Path $ProjectRoot ".uv\bootstrap\Scripts\python.exe"
    if (-not (Test-Path $BootstrapPython)) {
        & $Python -m venv (Join-Path $ProjectRoot ".uv\bootstrap")
        if ($LASTEXITCODE -ne 0) { throw "Could not create the local uv bootstrap environment." }
        & $BootstrapPython -m pip install "uv==0.8.3"
        if ($LASTEXITCODE -ne 0) { throw "Could not install the pinned uv version." }
    }
    $UvPython = $BootstrapPython
}

& $UvPython -m uv sync --frozen --python $Python
if ($LASTEXITCODE -ne 0) { throw "Python dependency setup failed." }

& $Pnpm --dir frontend install --frozen-lockfile
if ($LASTEXITCODE -ne 0) { throw "Frontend dependency setup failed." }

Write-Host "Setup complete. Start the project with ./scripts/start.ps1"
