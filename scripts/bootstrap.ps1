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

$Uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
if (-not (Test-Path -LiteralPath $Uv)) {
    throw "uv was not found at $Uv. Install it from https://docs.astral.sh/uv/."
}

Invoke-Checked -Command $Uv -Arguments @("sync", "--dev")

$Doctor = Join-Path $PSScriptRoot "..\.venv\Scripts\awesome-agent.exe"
Invoke-Checked -Command $Doctor -Arguments @("doctor")
