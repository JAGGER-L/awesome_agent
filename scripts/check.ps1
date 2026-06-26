$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Command,
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Command failed with exit code $LASTEXITCODE."
    }
}

$Venv = Join-Path $PSScriptRoot "..\.venv\Scripts"
$Ruff = Join-Path $Venv "ruff.exe"
$Mypy = Join-Path $Venv "mypy.exe"
$Pytest = Join-Path $Venv "pytest.exe"
$DefaultDatabaseUrl = "postgresql+asyncpg://awesome_agent:awesome_agent@localhost:54329/awesome_agent"
$DefaultCheckpointDatabaseUrl = "postgresql://awesome_agent:awesome_agent@localhost:54329/awesome_agent"

foreach ($Tool in @($Ruff, $Mypy, $Pytest)) {
    if (-not (Test-Path -LiteralPath $Tool)) {
        throw "Development environment is incomplete. Run scripts\bootstrap.ps1."
    }
}

if (-not $env:AWESOME_AGENT_DATABASE_URL) {
    $env:AWESOME_AGENT_DATABASE_URL = $DefaultDatabaseUrl
}

if (-not $env:AWESOME_AGENT_CHECKPOINT_DATABASE_URL) {
    $env:AWESOME_AGENT_CHECKPOINT_DATABASE_URL = $DefaultCheckpointDatabaseUrl
}

Write-Host "Using AWESOME_AGENT_DATABASE_URL=$($env:AWESOME_AGENT_DATABASE_URL)"
Write-Host "Using AWESOME_AGENT_CHECKPOINT_DATABASE_URL=$($env:AWESOME_AGENT_CHECKPOINT_DATABASE_URL)"
Write-Host "PostgreSQL tests require a reachable database. Run scripts\migrate.ps1 first if migrations are not current."

Invoke-Checked -Command $Ruff -Arguments @("format", "--no-cache", "--check", ".")
Invoke-Checked -Command $Ruff -Arguments @("check", "--no-cache", ".")
Invoke-Checked -Command $Mypy -Arguments @("--no-incremental")
Invoke-Checked -Command $Pytest -Arguments @("-p", "no:cacheprovider")
