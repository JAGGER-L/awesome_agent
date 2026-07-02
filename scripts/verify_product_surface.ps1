param(
    [string]$ApiUrl = "http://127.0.0.1:8000",
    [string]$RepositoryPath = (Get-Location).Path,
    [string]$Prompt = "Create a simple standalone HTML snake game and save it as snake.html in the thread workspace.",
    [string]$ExpectedArtifactPattern = "*.html",
    [int]$TimeoutSeconds = 180,
    [switch]$SkipRun
)

$ErrorActionPreference = "Stop"

function Invoke-AwesomeJson {
    param(
        [ValidateSet("GET", "POST")]
        [string]$Method,
        [string]$Path,
        [object]$Body = $null
    )

    $uri = "$($ApiUrl.TrimEnd('/'))$Path"
    if ($null -eq $Body) {
        return Invoke-RestMethod -Method $Method -Uri $uri -TimeoutSec 30
    }
    $json = $Body | ConvertTo-Json -Depth 20
    return Invoke-RestMethod `
        -Method $Method `
        -Uri $uri `
        -ContentType "application/json" `
        -Body $json `
        -TimeoutSec 30
}

Write-Host "Checking API readiness at $ApiUrl..."
$ready = Invoke-AwesomeJson -Method GET -Path "/ready?profile=api"
Write-Host "API status: $($ready.status)"

Write-Host "Creating product verification thread..."
$thread = Invoke-AwesomeJson `
    -Method POST `
    -Path "/threads" `
    -Body @{
        title = "Product verification snake game"
        context_kind = "repo"
        context_path = $RepositoryPath
    }
Write-Host "Thread: $($thread.id)"

Write-Host "Sending conversation prompt..."
$turnUri = "$($ApiUrl.TrimEnd('/'))/threads/$($thread.id)/turns"
$turnBody = @{ content = $Prompt } | ConvertTo-Json -Depth 20
$turnResponse = Invoke-WebRequest `
    -Method POST `
    -Uri $turnUri `
    -ContentType "application/json" `
    -Body $turnBody `
    -TimeoutSec $TimeoutSeconds
Write-Host "Conversation stream received $($turnResponse.Content.Length) characters."

if ($SkipRun) {
    Write-Host "Skipping Coding Run and artifact assertion because -SkipRun was provided."
    exit 0
}

Write-Host "Starting Coding Run from thread..."
$run = Invoke-AwesomeJson `
    -Method POST `
    -Path "/threads/$($thread.id)/runs" `
    -Body @{
        goal = $Prompt
        intent = "modifying"
        mode = "solo"
        repository_path = $RepositoryPath
    }
Write-Host "Run: $($run.id) status=$($run.status)"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    Start-Sleep -Seconds 3
    $artifacts = Invoke-AwesomeJson -Method GET -Path "/threads/$($thread.id)/artifacts"
    $match = @($artifacts.items | Where-Object {
        $_.path -like "*$ExpectedArtifactPattern" -or $_.path -like "*snake.html"
    })
    if ($match.Count -gt 0) {
        Write-Host "Found artifact: $($match[0].path)"
        exit 0
    }
} while ((Get-Date) -lt $deadline)

throw "No matching artifact appeared for thread $($thread.id) within $TimeoutSeconds seconds."
