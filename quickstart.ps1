#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [switch]$InstallNode,
    [switch]$Check,
    [switch]$NoRun
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

function Find-CommandPath([string[]]$Names) {
    foreach ($Name in $Names) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($null -ne $Command) {
            return $Command.Source
        }
    }
    return $null
}

Write-Host "==> Checking prerequisites"
$Python = Find-CommandPath @("python", "py", "python3")
if ($null -eq $Python) {
    Fail "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/windows/."
}

$PythonVersion = & $Python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
if ($LASTEXITCODE -ne 0) {
    Fail "Unable to determine the Python version."
}
$VersionParts = $PythonVersion.Trim().Split(".")
if ([int]$VersionParts[0] -lt 3 -or
    ([int]$VersionParts[0] -eq 3 -and [int]$VersionParts[1] -lt 11)) {
    Fail "Python 3.11 or newer is required; found $PythonVersion."
}
Write-Host "python $PythonVersion OK"

$Node = Find-CommandPath @("node")
$Npm = Find-CommandPath @("npm", "npm.cmd")
$LocalNode = Join-Path $PSScriptRoot ".node\node.exe"
$LocalNpm = Join-Path $PSScriptRoot ".node\npm.cmd"
if ((Test-Path $LocalNode) -and (Test-Path $LocalNpm)) {
    $Node = $LocalNode
    $Npm = $LocalNpm
}

if ($null -eq $Node -or $null -eq $Npm) {
    if ($InstallNode -and -not $Check) {
        Write-Host "Node not found: installing a project-local Node LTS into .\.node"
        & $Python -m pip install -q nodeenv
        if ($LASTEXITCODE -ne 0) {
            Fail "Could not install nodeenv."
        }
        & $Python -m nodeenv --node=lts --prebuilt .node
        if ($LASTEXITCODE -ne 0) {
            Fail "Could not install project-local Node. Install Node 20+ manually and rerun this script."
        }
        $Node = $LocalNode
        $Npm = $LocalNpm
    } else {
        Fail "Node 20+ was not found. Install it from https://nodejs.org/, or rerun with -InstallNode."
    }
}

$NodeVersion = & $Node --version
$NodeVersionParts = $NodeVersion.Trim().TrimStart("v").Split(".")
if ([int]$NodeVersionParts[0] -lt 20) {
    Fail "Node 20 or newer is required; found $NodeVersion."
}
Write-Host "node $NodeVersion OK"
if ($Check) {
    Write-Host "All required prerequisites are present."
    exit 0
}

$VenvPython = Join-Path $PSScriptRoot "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "==> Creating the backend virtual environment"
    & $Python -m venv (Join-Path $PSScriptRoot "backend\.venv")
    if ($LASTEXITCODE -ne 0) {
        Fail "Could not create the backend virtual environment."
    }
}

Write-Host "==> Installing dependencies"
& $VenvPython -m pip install -q --upgrade pip
& $VenvPython -m pip install -q -r (Join-Path $PSScriptRoot "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Fail "Could not install Python dependencies."
}
Push-Location (Join-Path $PSScriptRoot "frontend")
try {
    & $Npm install
} finally {
    Pop-Location
}
if ($LASTEXITCODE -ne 0) {
    Fail "Could not install frontend dependencies."
}

$EnvFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $PSScriptRoot ".env.example") $EnvFile
}

Write-Host "==> Seeding synthetic demo data"
Push-Location (Join-Path $PSScriptRoot "backend")
try {
    & $VenvPython -m app.seed.seed
    if ($LASTEXITCODE -ne 0) {
        Fail "Could not seed demo data."
    }
} finally {
    Pop-Location
}

if ($NoRun) {
    Write-Host "Ready. Start later with: .\quickstart.ps1"
    exit 0
}

Write-Host "==> Starting backend and frontend in separate PowerShell windows"
$BackendCommand = "Set-Location '$PSScriptRoot\backend'; & '$VenvPython' -m uvicorn app.main:app --reload --port 8000"
$FrontendCommand = "Set-Location '$PSScriptRoot\frontend'; & '$Npm' run dev"
Start-Process powershell.exe -ArgumentList @("-NoExit", "-Command", $BackendCommand) | Out-Null
Start-Process powershell.exe -ArgumentList @("-NoExit", "-Command", $FrontendCommand) | Out-Null

Write-Host ""
Write-Host "Ready. Open http://localhost:5173"
Write-Host "Backend health check: http://localhost:8000/api/health"
