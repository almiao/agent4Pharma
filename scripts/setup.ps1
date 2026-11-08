<#
.SYNOPSIS
Install locked project dependencies and create an empty local environment template.
.DESCRIPTION
Requires uv. -BuildFrontend also requires Node and npm, installs the frontend lockfile
and rebuilds packaged assets. Does not start services, download model weights or call LLMs.
#>
[CmdletBinding()]
param([switch]$BuildFrontend)

$ErrorActionPreference = 'Stop'
$setupRoot = Split-Path -Parent $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/'
}
if ($BuildFrontend) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js is required.' }
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'npm is required.' }
    # Use the same Node as Vite; some Windows npm.cmd files bundle an older Node.
    $setupNpmCommand = Get-Command npm
    $setupNpmCli = Join-Path (Split-Path -Parent $setupNpmCommand.Source) 'node_modules/npm/bin/npm-cli.js'
    if (-not (Test-Path -LiteralPath $setupNpmCli)) {
        throw 'Cannot locate npm CLI next to npm. Use a standard Node.js + npm installation.'
    }
    $setupNodeVersionText = & node --version
    if ($LASTEXITCODE -ne 0) { throw 'Cannot read Node.js version.' }
    $setupNodeVersion = [version]$setupNodeVersionText.TrimStart('v')
    $setupNodeSupported = ($setupNodeVersion.Major -eq 20 -and $setupNodeVersion.Minor -ge 19) -or
        ($setupNodeVersion.Major -eq 22 -and $setupNodeVersion.Minor -ge 12) -or
        $setupNodeVersion.Major -gt 22
    if (-not $setupNodeSupported) { throw 'Vite requires Node 20.19+ or 22.12+; Node 24 is recommended.' }
}

Push-Location $setupRoot
try {
    & uv sync --locked --group dev --python 3.12
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
    if ($BuildFrontend) {
        & node $setupNpmCli ci --prefix frontend --include=optional
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        & node $setupNpmCli run build --prefix frontend
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    }
    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }
    Write-Output 'Dependencies installed. Configure .env locally when model calls are needed.'
    Write-Output 'Start the workbench: uv run pharma workbench --port 8765'
} finally {
    Pop-Location
}
