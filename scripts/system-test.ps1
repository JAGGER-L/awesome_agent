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

Invoke-Checked -Command "docker" -Arguments @("compose", "up", "-d", "postgres")

$deadline = (Get-Date).AddMinutes(2)
do {
    $health = docker inspect --format "{{.State.Health.Status}}" awesome_agent-postgres-1 2>$null
    if ($health -eq "healthy") {
        break
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)

if ($health -ne "healthy") {
    throw "PostgreSQL did not become healthy."
}

& (Join-Path $PSScriptRoot "migrate.ps1")

$env:AWESOME_AGENT_TEST_DATABASE_URL = "postgresql+asyncpg://awesome_agent:awesome_agent@localhost:54329/awesome_agent"
$env:AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL = "postgresql://awesome_agent:awesome_agent@localhost:54329/awesome_agent"

$Pytest = Join-Path $PSScriptRoot "..\.venv\Scripts\pytest.exe"
Invoke-Checked -Command $Pytest -Arguments @(
    "-p", "no:cacheprovider",
    "tests\integration",
    "tests\e2e",
    "--no-cov"
)

$Port = 8765
$Python = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$Server = Start-Process -FilePath $Python `
    -ArgumentList @("-m", "uvicorn", "awesome_agent.api.app:app", "--host", "127.0.0.1", "--port", "$Port") `
    -WindowStyle Hidden `
    -PassThru
try {
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-RestMethod "http://127.0.0.1:$Port/health"
            if ($response.status -eq "ok") {
                break
            }
        }
        catch {
            # The server may still be starting.
        }
    } while ((Get-Date) -lt $deadline)

    if ($response.status -ne "ok") {
        throw "FastAPI did not become healthy."
    }
}
finally {
    Stop-Process -Id $Server.Id -Force -ErrorAction SilentlyContinue
}

Write-Output "System tests passed."

