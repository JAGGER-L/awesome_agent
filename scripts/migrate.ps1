$ErrorActionPreference = "Stop"

$Alembic = Join-Path $PSScriptRoot "..\.venv\Scripts\alembic.exe"
if (-not (Test-Path -LiteralPath $Alembic)) {
    throw "PostgreSQL dependencies are missing. Run uv sync --extra postgres --dev."
}

& $Alembic upgrade head
if ($LASTEXITCODE -ne 0) {
    throw "Alembic migration failed with exit code $LASTEXITCODE."
}

