[CmdletBinding()]
param(
    [switch]$Restart,
    [switch]$Manual
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RuntimeDir = Join-Path $ProjectRoot ".runtime"
$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $RuntimeDir, $LogDir | Out-Null
Set-Location $ProjectRoot

$PreviousWorkerEnvironment = @{}
try {
if ($Manual) {
    foreach ($Name in @("NOVEL_WRITER_LOCAL_TASK_WORKER_ENABLED")) {
        $PreviousWorkerEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
    }
    $env:NOVEL_WRITER_LOCAL_TASK_WORKER_ENABLED = "false"
    Write-Host "Manual maintenance mode: background workers are disabled for this launch."
}

# ``uv run`` deliberately manages the project's ``.venv``. An unrelated parent
# shell may export VIRTUAL_ENV (for example an agent/bootstrap interpreter); uv
# writes a warning to stderr in that case and PowerShell's Stop policy turns the
# harmless warning into a startup failure. Never let a foreign environment leak
# into the tracked backend process.
if ($env:VIRTUAL_ENV) {
    $ProjectVenv = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ".venv"))
    $ActiveVenvPath = if ([IO.Path]::IsPathRooted($env:VIRTUAL_ENV)) {
        $env:VIRTUAL_ENV
    } else {
        Join-Path $ProjectRoot $env:VIRTUAL_ENV
    }
    $ActiveVenv = [IO.Path]::GetFullPath($ActiveVenvPath)
    if ($ActiveVenv -ne $ProjectVenv) {
        Remove-Item Env:VIRTUAL_ENV
    }
}

if (-not (Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue)) {
    throw "pnpm is not available. Run ./scripts/setup.ps1 first."
}

$PnpmExecutable = (Get-Command "pnpm.cmd" -ErrorAction Stop).Source
$ProjectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$AlembicExecutable = Join-Path $ProjectRoot ".venv\Scripts\alembic.exe"
if (-not (Test-Path $ProjectPython) -or -not (Test-Path $AlembicExecutable)) {
    throw "The project virtual environment is unavailable. Run ./scripts/setup.ps1 first."
}

function Test-DatabaseConnection {
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $ProjectPython scripts/check_database.py *> $null
    $ExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousPreference
    return ($ExitCode -eq 0)
}

function Test-DockerEngine {
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker info --format "{{.ServerVersion}}" *> $null
    $ExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousPreference
    return ($ExitCode -eq 0)
}

function Test-BackendHealth {
    try {
        $Response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
        return ($Response.status -eq "ok")
    } catch {
        return $false
    }
}

function Assert-RestartSafe {
    $PreviousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $ProjectPython scripts/assert_restart_safe.py
    $ExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousPreference
    if ($ExitCode -ne 0) {
        throw "Backend restart aborted because an AgentCall is executing. No process was stopped."
    }
}

function Test-FrontendHealth {
    try {
        $Response = Invoke-WebRequest `
            -Uri "http://127.0.0.1:5173" `
            -TimeoutSec 1 `
            -UseBasicParsing
        return ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

function Get-TrackedProcessCommandLine {
    param([int]$ProcessId)

    try {
        $Process = Get-CimInstance Win32_Process `
            -Filter "ProcessId = $ProcessId" `
            -ErrorAction Stop
        if ($null -eq $Process) { return $null }
        return [string]$Process.CommandLine
    } catch {
        return $null
    }
}

if (Test-DatabaseConnection) {
    Write-Host "PostgreSQL is already reachable; Docker control is not required for this startup."
} else {
    if (-not (Get-Command "docker" -ErrorAction SilentlyContinue)) {
        throw "PostgreSQL is unavailable and docker is not installed. Run ./scripts/setup.ps1 first."
    }
    if (-not (Test-DockerEngine)) {
        $DockerDesktopPath = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
        if (-not (Test-Path -LiteralPath $DockerDesktopPath)) {
            throw "PostgreSQL is unavailable and Docker Desktop was not found at $DockerDesktopPath."
        }
        Write-Host "PostgreSQL is unavailable. Starting Docker Desktop..."
        Start-Process -FilePath $DockerDesktopPath -WindowStyle Hidden
        $DockerReady = $false
        for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
            if (Test-DockerEngine) {
                $DockerReady = $true
                break
            }
            Start-Sleep -Seconds 1
        }
        if (-not $DockerReady) {
            throw "Docker Desktop did not become ready within 60 seconds. Open Docker Desktop and check its status."
        }
        Write-Host "Docker engine is ready."
    }

    docker compose -f compose.dev.yaml up -d --wait database
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL failed to start." }
}

$PreviousPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $AlembicExecutable upgrade head
$MigrationExitCode = $LASTEXITCODE
$ErrorActionPreference = $PreviousPreference
if ($MigrationExitCode -ne 0) { throw "Database migration failed." }

$TokenPath = Join-Path $RuntimeDir "local-token"
if (Test-Path $TokenPath) {
    $LocalToken = (Get-Content -Raw -LiteralPath $TokenPath).Trim()
} else {
    $TokenBytes = [byte[]]::new(32)
    $Random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $Random.GetBytes($TokenBytes)
    } finally {
        $Random.Dispose()
    }
    $LocalToken = -join ($TokenBytes | ForEach-Object { $_.ToString("x2") })
    [IO.File]::WriteAllText($TokenPath, $LocalToken)
}
$env:NOVEL_WRITER_LOCAL_TOKEN = $LocalToken
$env:NOVEL_WRITER_LOG_DIR = $LogDir

function Start-TrackedProcess {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$ArgumentList,
        [string]$ExpectedCommandFragment,
        [scriptblock]$HealthCheck
    )
    $PidPath = Join-Path $RuntimeDir "$Name.pid"
    if (Test-Path $PidPath) {
        $RawPid = (Get-Content -Raw -LiteralPath $PidPath).Trim()
        $ExistingPid = 0
        $ValidPid = [int]::TryParse($RawPid, [ref]$ExistingPid)
        $ExistingProcess = if ($ValidPid) {
            Get-Process -Id $ExistingPid -ErrorAction SilentlyContinue
        } else {
            $null
        }
        $CommandLine = if ($null -ne $ExistingProcess) {
            Get-TrackedProcessCommandLine -ProcessId $ExistingPid
        } else {
            $null
        }
        $ServiceHealthy = if ($null -ne $HealthCheck) {
            & $HealthCheck
        } else {
            $false
        }
        if (
            $null -ne $ExistingProcess -and
            (
                $CommandLine -like "*$ExpectedCommandFragment*" -or
                $ServiceHealthy
            )
        ) {
            Write-Host "$Name is already running (PID $ExistingPid)."
            return
        }
        Remove-Item -LiteralPath $PidPath -Force
        Write-Host "Removed stale $Name PID record $RawPid; the referenced process was not stopped."
    }
    $Process = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList `
        -WorkingDirectory $ProjectRoot -PassThru -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir "$Name.stdout.log") `
        -RedirectStandardError (Join-Path $LogDir "$Name.stderr.log")
    [IO.File]::WriteAllText($PidPath, [string]$Process.Id)
    Write-Host "Started $Name (PID $($Process.Id))."
}

function Stop-TrackedProcessTree {
    param(
        [string]$Name,
        [string]$ExpectedCommandFragment
    )
    $PidPath = Join-Path $RuntimeDir "$Name.pid"
    if (-not (Test-Path $PidPath)) { return }
    $ExistingPid = [int](Get-Content -LiteralPath $PidPath)
    $Processes = @(Get-CimInstance Win32_Process)
    $RootProcess = $Processes | Where-Object { $_.ProcessId -eq $ExistingPid }
    if ($null -eq $RootProcess) {
        Remove-Item -LiteralPath $PidPath -Force
        return
    }
    if (($RootProcess.CommandLine | Out-String) -notlike "*$ExpectedCommandFragment*") {
        Remove-Item -LiteralPath $PidPath -Force
        Write-Host "Removed stale $Name PID record $ExistingPid; the unrelated process was not stopped."
        return
    }
    $ProcessIds = @($ExistingPid)
    do {
        $Children = @(
            $Processes | Where-Object {
                $ProcessIds -contains [int]$_.ParentProcessId -and
                $ProcessIds -notcontains [int]$_.ProcessId
            }
        )
        foreach ($Child in $Children) { $ProcessIds += [int]$Child.ProcessId }
    } while ($Children.Count -gt 0)
    for ($Index = $ProcessIds.Count - 1; $Index -ge 0; $Index--) {
        Stop-Process -Id $ProcessIds[$Index] -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $PidPath -Force
    Write-Host "Stopped $Name process tree."
}

if ($Restart) {
    if (Test-BackendHealth) { Assert-RestartSafe }
    Stop-TrackedProcessTree -Name "frontend" -ExpectedCommandFragment "frontend dev"
    Stop-TrackedProcessTree -Name "backend" -ExpectedCommandFragment "novel_writer"
}

Start-TrackedProcess -Name "backend" -FilePath $ProjectPython -ArgumentList @(
    "-m", "novel_writer"
) -ExpectedCommandFragment "-m novel_writer" -HealthCheck { Test-BackendHealth }
Start-TrackedProcess -Name "frontend" -FilePath $PnpmExecutable -ArgumentList @(
    "--dir", "frontend", "dev"
) -ExpectedCommandFragment "frontend dev" -HealthCheck { Test-FrontendHealth }

$Ready = $false
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
    try {
        $Response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 1
        if ($Response.status -eq "ok") { $Ready = $true; break }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $Ready) {
    throw "Backend did not become healthy. Check logs/backend.stderr.log."
}

$FrontendReady = $false
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
    try {
        $FrontendResponse = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -TimeoutSec 1 -UseBasicParsing
        if ($FrontendResponse.StatusCode -ge 200 -and $FrontendResponse.StatusCode -lt 500) {
            $FrontendReady = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $FrontendReady) {
    throw "Frontend did not become ready. Check logs/frontend.stderr.log."
}

Write-Host "API:       http://127.0.0.1:8000/docs"
Write-Host "Workbench: http://127.0.0.1:5173"
} finally {
    foreach ($Name in $PreviousWorkerEnvironment.Keys) {
        if ($null -eq $PreviousWorkerEnvironment[$Name]) {
            Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($Name, $PreviousWorkerEnvironment[$Name], "Process")
        }
    }
}
