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

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Venv = Join-Path $Root ".venv\Scripts"
$Ruff = Join-Path $Venv "ruff.exe"
$Mypy = Join-Path $Venv "mypy.exe"
$Pytest = Join-Path $Venv "pytest.exe"

foreach ($Tool in @($Ruff, $Mypy, $Pytest)) {
    if (-not (Test-Path -LiteralPath $Tool)) {
        throw "Development environment is incomplete. Run scripts\bootstrap.ps1."
    }
}

$TargetPaths = @(
    (Join-Path $Root "src\awesome_agent\paths.py"),
    (Join-Path $Root "src\awesome_agent\core"),
    (Join-Path $Root "src\awesome_agent\application"),
    (Join-Path $Root "src\awesome_agent\storage"),
    (Join-Path $Root "src\awesome_agent\modeling"),
    (Join-Path $Root "src\awesome_agent\providers"),
    (Join-Path $Root "src\awesome_agent\memory"),
    (Join-Path $Root "src\awesome_agent\safety\redaction.py"),
    (Join-Path $Root "src\awesome_agent\repositories\policy.py"),
    (Join-Path $Root "src\awesome_agent\sandbox\process.py"),
    (Join-Path $Root "tests"),
    (Join-Path $Root "scripts\make\check.py")
)

Invoke-Checked -Command $Ruff -Arguments (@("format", "--no-cache", "--check") + $TargetPaths)
Invoke-Checked -Command $Ruff -Arguments (@("check", "--no-cache") + $TargetPaths)
Invoke-Checked -Command $Mypy -Arguments (@("--no-incremental") + $TargetPaths)
Invoke-Checked -Command $Pytest -Arguments @("-p", "no:cacheprovider", (Join-Path $Root "tests"))
