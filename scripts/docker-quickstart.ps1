param(
    [switch]$PlanOnly,
    [switch]$KeepRuntime,
    [string]$ApiUrl = "http://127.0.0.1:8000",
    [int]$ReadyTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Name)
    Write-Output "docker-quickstart.step=$Name"
}

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

function Wait-JsonEndpoint {
    param(
        [string]$Url,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $lastError = ""
    do {
        try {
            return Invoke-RestMethod -Uri $Url -TimeoutSec 5
        }
        catch {
            $lastError = $_.Exception.Message
            if ($_.Exception.Response -and $_.Exception.Response.GetResponseStream()) {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                $body = $reader.ReadToEnd()
                if ($body) {
                    $lastError = "$lastError body=$body"
                }
            }
            Start-Sleep -Seconds 1
        }
    } while ((Get-Date) -lt $deadline)

    throw "Endpoint did not become ready: $Url last_error=$lastError"
}

function Get-AwesomeHome {
    if ($env:AWESOME_HOME) {
        return $env:AWESOME_HOME
    }
    if ($env:LOCALAPPDATA) {
        return (Join-Path $env:LOCALAPPDATA "awesome-agent")
    }
    return (Join-Path $HOME ".awesome-agent")
}

if ($PlanOnly) {
    foreach ($Step in @(
        "config",
        "compose_up",
        "readiness",
        "next_steps"
    )) {
        Write-Step $Step
    }
    Write-Output "docker-quickstart.status=plan"
    exit 0
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$AwesomeHome = Get-AwesomeHome
$env:AWESOME_HOME = $AwesomeHome

Write-Step "config"
$EnvPath = Join-Path $AwesomeHome ".env"
$EnvExisted = Test-Path -LiteralPath $EnvPath
Push-Location $Root
try {
    Invoke-Checked -Command "uv" -Arguments @(
        "run",
        "python",
        "-c",
        "from awesome_agent.cli.config_flow import initialize_user_config; from awesome_agent.paths import awesome_paths; initialize_user_config(awesome_paths())"
    )
}
finally {
    Pop-Location
}
if ($EnvExisted) {
    Write-Output "docker-quickstart.config=exists awesome_env $EnvPath"
}
else {
    Write-Output "docker-quickstart.config=created awesome_env $EnvPath"
}

Write-Step "compose_up"
Push-Location $Root
try {
    # Equivalent command: docker compose up -d --build postgres sandbox api worker
    Invoke-Checked -Command "docker" -Arguments @(
        "compose",
        "up",
        "-d",
        "--build",
        "postgres",
        "sandbox",
        "api",
        "worker"
    )
}
finally {
    Pop-Location
}

Write-Step "readiness"
Wait-JsonEndpoint -Url "$ApiUrl/health" -TimeoutSeconds $ReadyTimeoutSeconds | Out-Null
Wait-JsonEndpoint -Url "$ApiUrl/ready?profile=api" -TimeoutSeconds $ReadyTimeoutSeconds | Out-Null

Write-Step "next_steps"
Write-Output "docker-quickstart.api=$ApiUrl"
Write-Output "docker-quickstart.api_docs=$ApiUrl/docs"
Write-Output "docker-quickstart.next=probe .\.venv\Scripts\awesome-agent.exe probe --repo <repository-path> --api-url $ApiUrl"
Write-Output "docker-quickstart.next=diagnostics .\.venv\Scripts\awesome-agent.exe diagnostics <run-id> --api-url $ApiUrl"
Write-Output "docker-quickstart.next=logs docker compose logs sandbox api worker"
if ($KeepRuntime) {
    Write-Output "docker-quickstart.shutdown=manual docker compose down"
}
else {
    Write-Output "docker-quickstart.shutdown=run docker compose down"
}
Write-Output "docker-quickstart.status=completed"
