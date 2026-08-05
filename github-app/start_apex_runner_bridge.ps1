[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-Executable {
    param(
        [Parameter(Mandatory = $true)]
        [string[]] $Names,
        [string[]] $KnownPaths = @()
    )

    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $command) {
            return $command.Source
        }
    }

    foreach ($path in $KnownPaths) {
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            return $path
        }
    }

    return $null
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Executable,
        [Parameter(Mandatory = $true)]
        [string[]] $Arguments,
        [string] $FailureMessage = 'Command failed.'
    )

    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage Exit code: $LASTEXITCODE"
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$bootstrapPath = Join-Path $PSScriptRoot 'bootstrap_apex_github_app.py'
$manifestPath = Join-Path $PSScriptRoot 'app-manifest.json'

if (-not (Test-Path -LiteralPath $bootstrapPath -PathType Leaf)) {
    throw "Bootstrap script not found: $bootstrapPath"
}
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "GitHub App manifest not found: $manifestPath"
}

$gh = Resolve-Executable -Names @('gh.exe', 'gh') -KnownPaths @(
    "$env:ProgramFiles\GitHub CLI\gh.exe",
    "$env:LOCALAPPDATA\Programs\GitHub CLI\gh.exe"
)
if ($null -eq $gh) {
    throw 'GitHub CLI is not installed. Install GitHub CLI once, then double-click START_APEX_RUNNER_BRIDGE.cmd again.'
}

$python = Resolve-Executable -Names @('py.exe', 'py', 'python.exe', 'python') -KnownPaths @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:ProgramFiles\Python312\python.exe",
    "$env:ProgramFiles\Python311\python.exe"
)
if ($null -eq $python) {
    throw 'Python 3 is not installed. Install Python 3 once, then double-click START_APEX_RUNNER_BRIDGE.cmd again.'
}

Write-Host 'Checking GitHub authentication...'
& $gh auth status --hostname github.com *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'GitHub requires account consent. A browser window will open; sign in and approve the CLI session.'
    Invoke-Checked -Executable $gh -Arguments @(
        'auth', 'login', '--web', '--hostname', 'github.com', '--git-protocol', 'https'
    ) -FailureMessage 'GitHub authentication did not complete.'
}

Invoke-Checked -Executable $gh -Arguments @(
    'auth', 'status', '--hostname', 'github.com'
) -FailureMessage 'GitHub authentication verification failed.'

$pythonArguments = @()
if ([System.IO.Path]::GetFileName($python).ToLowerInvariant().StartsWith('py')) {
    $pythonArguments += '-3'
}
$pythonArguments += @(
    $bootstrapPath,
    '--manifest',
    $manifestPath
)

Write-Host ''
Write-Host 'Launching the no-manual-key GitHub App bootstrap...'
Write-Host 'The only human boundary is GitHub account consent and repository-install approval in the browser.'
Write-Host 'No private key is displayed, copied, pasted, written to disk, or transported through chat.'
Write-Host ''

Push-Location $repoRoot
try {
    Invoke-Checked -Executable $python -Arguments $pythonArguments -FailureMessage 'APEX Runner Bridge bootstrap failed closed.'
}
finally {
    Pop-Location
}

Write-Host ''
Write-Host 'APEX Runner Bridge activation and verification completed successfully.'
exit 0
