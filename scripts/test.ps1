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

$Pytest = Join-Path $PSScriptRoot "..\.venv\Scripts\pytest.exe"
if (-not (Test-Path -LiteralPath $Pytest)) {
    throw "Development environment is incomplete. Run scripts\bootstrap.ps1."
}

$PytestArguments = @("-p", "no:cacheprovider") + $args
Invoke-Checked -Command $Pytest -Arguments $PytestArguments
