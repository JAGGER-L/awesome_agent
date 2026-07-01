param(
    [switch]$PlanOnly,
    [switch]$KeepRuntime,
    [switch]$UseExistingRuntime,
    [switch]$RunReadOnly,
    [string]$SampleRepoPath = "",
    [string]$ApiUrl = "http://127.0.0.1:8000",
    [int]$ProbeTimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Name)
    Write-Output "quickstart.step=$Name"
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

function Test-ConfiguredDeepSeekKey {
    param([string]$EnvPath)

    if ($env:AWESOME_AGENT_DEEPSEEK_API_KEY) {
        return $true
    }

    if (-not (Test-Path -LiteralPath $EnvPath)) {
        return $false
    }

    foreach ($line in Get-Content -LiteralPath $EnvPath) {
        if ($line -match '^\s*AWESOME_AGENT_DEEPSEEK_API_KEY\s*=\s*(.+?)\s*$') {
            $value = $Matches[1].Trim().Trim('"').Trim("'")
            return -not [string]::IsNullOrWhiteSpace($value)
        }
    }

    return $false
}

function Get-PostgresContainerId {
    $containerId = docker compose ps -q postgres 2>$null
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($containerId)) {
        throw "PostgreSQL container was not found after docker compose up."
    }
    return $containerId.Trim()
}

if ($PlanOnly) {
    foreach ($Step in @(
        "bootstrap",
        "config",
        "postgres",
        "migrate",
        "doctor",
        "sample_repo",
        "runtime",
        "readiness",
        "probe"
    )) {
        Write-Step $Step
    }
    Write-Output "quickstart.status=plan"
    exit 0
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Agent = Join-Path $Root ".venv\Scripts\awesome-agent.exe"
$QuickstartRoot = Join-Path $Root "output\quickstart"
if (-not $SampleRepoPath) {
    $SampleRepoPath = Join-Path $QuickstartRoot "sample-repo"
}
New-Item -ItemType Directory -Force -Path $QuickstartRoot | Out-Null

Write-Step "bootstrap"
& (Join-Path $PSScriptRoot "bootstrap.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "bootstrap.ps1 failed with exit code $LASTEXITCODE."
}

Write-Step "config"
$EnvPath = Join-Path $Root ".env"
if (-not (Test-Path -LiteralPath $EnvPath)) {
    Copy-Item -LiteralPath (Join-Path $Root ".env.example") -Destination $EnvPath
    Write-Output "quickstart.config=created .env"
}
else {
    Write-Output "quickstart.config=exists .env"
}

$ProjectConfig = Join-Path $Root "awesome-agent.yaml"
if (-not (Test-Path -LiteralPath $ProjectConfig)) {
    throw "awesome-agent.yaml is missing. This file configures project extension sources."
}

Write-Step "postgres"
Invoke-Checked -Command "docker" -Arguments @("compose", "up", "-d", "postgres")
$containerId = Get-PostgresContainerId
$deadline = (Get-Date).AddMinutes(2)
do {
    $health = docker inspect --format "{{.State.Health.Status}}" $containerId 2>$null
    if ($health -eq "healthy") {
        break
    }
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $deadline)
if ($health -ne "healthy") {
    throw "PostgreSQL did not become healthy."
}

Write-Step "migrate"
& (Join-Path $PSScriptRoot "migrate.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "migrate.ps1 failed with exit code $LASTEXITCODE."
}

Write-Step "doctor"
Invoke-Checked -Command $Agent -Arguments @("doctor", "--profile", "api")

Write-Step "sample_repo"
New-Item -ItemType Directory -Force -Path $SampleRepoPath | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $SampleRepoPath ".git"))) {
    Invoke-Checked -Command "git" -Arguments @("init", $SampleRepoPath)
    Invoke-Checked -Command "git" -Arguments @("-C", $SampleRepoPath, "config", "user.email", "quickstart@example.local")
    Invoke-Checked -Command "git" -Arguments @("-C", $SampleRepoPath, "config", "user.name", "awesome_agent quickstart")
    Set-Content -LiteralPath (Join-Path $SampleRepoPath "README.md") -Encoding UTF8 -Value "# Quickstart Sample`n"
    Invoke-Checked -Command "git" -Arguments @("-C", $SampleRepoPath, "add", "README.md")
    Invoke-Checked -Command "git" -Arguments @("-C", $SampleRepoPath, "commit", "-m", "Initial quickstart sample")
}

$Runtime = $null
try {
    Write-Step "runtime"
    if (-not $UseExistingRuntime) {
        $RuntimeOutLog = Join-Path $QuickstartRoot "runtime.out.log"
        $RuntimeErrLog = Join-Path $QuickstartRoot "runtime.err.log"
        $Runtime = Start-Process -FilePath $Agent `
            -ArgumentList @("start") `
            -WindowStyle Hidden `
            -RedirectStandardOutput $RuntimeOutLog `
            -RedirectStandardError $RuntimeErrLog `
            -PassThru
        Write-Output "quickstart.runtime_stdout=$RuntimeOutLog"
        Write-Output "quickstart.runtime_stderr=$RuntimeErrLog"
    }
    else {
        Write-Output "quickstart.runtime=existing"
    }

    Write-Step "readiness"
    Wait-JsonEndpoint -Url "$ApiUrl/health" -TimeoutSeconds 120 | Out-Null
    Wait-JsonEndpoint -Url "$ApiUrl/ready?profile=api" -TimeoutSeconds 120 | Out-Null

    Write-Step "probe"
    $ParentRoot = Split-Path -Parent $SampleRepoPath
    Invoke-Checked -Command $Agent -Arguments @("config", "root", "add", $ParentRoot)
    Invoke-Checked -Command $Agent -Arguments @("repo", "add", $SampleRepoPath)
    $ProbeRunId = & $Agent probe --repo $SampleRepoPath --api-url $ApiUrl
    if ($LASTEXITCODE -ne 0) {
        throw "Probe creation failed."
    }

    $deadline = (Get-Date).AddSeconds($ProbeTimeoutSeconds)
    do {
        $status = & $Agent status $ProbeRunId --api-url $ApiUrl
        if ($status -in @("completed", "failed", "cancelled")) {
            break
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)

    if ($status -ne "completed") {
        throw "Probe did not complete successfully. run_id=$ProbeRunId status=$status"
    }

    Write-Output "quickstart.probe_run_id=$ProbeRunId"
    Write-Output "quickstart.next=diagnostics $Agent diagnostics $ProbeRunId --api-url $ApiUrl"
    Write-Output "quickstart.next=extensions $Agent extensions diagnostics --api-url $ApiUrl"

    if (-not (Test-ConfiguredDeepSeekKey -EnvPath $EnvPath)) {
        Write-Output "quickstart.model_run=skipped reason=missing_deepseek_key"
    }
    elseif ($RunReadOnly) {
        $RunId = & $Agent run "Inspect this sample repository" --repo $SampleRepoPath --read-only --api-url $ApiUrl
        if ($LASTEXITCODE -ne 0) {
            throw "Read-only model run failed to start."
        }
        Write-Output "quickstart.model_run=created run_id=$RunId"
    }
    else {
        Write-Output "quickstart.model_run=available action=rerun_with_runreadonly"
        Write-Output "quickstart.next=run_readonly $Agent run `"Inspect this sample repository`" --repo $SampleRepoPath --read-only --api-url $ApiUrl"
    }

    Write-Output "quickstart.status=completed"
}
finally {
    if (-not $KeepRuntime -and $Runtime -and -not $Runtime.HasExited) {
        if ($IsWindows -or $env:OS -eq "Windows_NT") {
            taskkill.exe /PID $Runtime.Id /T /F 2>$null | Out-Null
        }
        else {
            Stop-Process -Id $Runtime.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
