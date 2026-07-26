$ErrorActionPreference = "Stop"

$Root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..\.."))
$Installer = Join-Path $Root "install.ps1"
$script:NetworkCalled = $false
$script:HostScenario = @{
    ProductType = 1
    BuildNumber = "26100"
    Architectures = @(9)
}

function Invoke-WebRequest {
    $script:NetworkCalled = $true
    throw "installer contract reached the network"
}

function Get-CimInstance([string]$ClassName) {
    if ($ClassName -eq "Win32_OperatingSystem") {
        return [pscustomobject]@{
            ProductType = $script:HostScenario.ProductType
            BuildNumber = $script:HostScenario.BuildNumber
        }
    }
    if ($ClassName -eq "Win32_Processor") {
        return @($script:HostScenario.Architectures | ForEach-Object {
            [pscustomobject]@{ Architecture = $_ }
        })
    }
    throw "unexpected CIM class: $ClassName"
}

function Invoke-Guard(
    [string]$Mode,
    [AllowEmptyString()][string]$Base,
    [string]$Expected,
    [hashtable]$Scenario = $script:HostScenario
) {
    $previousMode = $env:AWESOME_INSTALL_CANDIDATE
    $previousBase = $env:AWESOME_INSTALL_CANDIDATE_ASSET_BASE
    $previousLocalAppData = $env:LOCALAPPDATA
    $script:NetworkCalled = $false
    $script:HostScenario = $Scenario
    try {
        $env:AWESOME_INSTALL_CANDIDATE = $Mode
        $env:AWESOME_INSTALL_CANDIDATE_ASSET_BASE = $Base
        $env:LOCALAPPDATA = ""
        try {
            . $Installer
            throw "installer guard unexpectedly succeeded"
        }
        catch {
            if ($_.Exception.Message -notlike "*$Expected*") {
                throw "unexpected installer diagnostic: $($_.Exception.Message)"
            }
        }
        if ($script:NetworkCalled) {
            throw "installer guard reached the network"
        }
    }
    finally {
        $env:AWESOME_INSTALL_CANDIDATE = $previousMode
        $env:AWESOME_INSTALL_CANDIDATE_ASSET_BASE = $previousBase
        $env:LOCALAPPDATA = $previousLocalAppData
    }
}

Invoke-Guard "0" "http://127.0.0.1:1" "requires candidate mode"
Invoke-Guard "2" "" "mode must be 0 or 1"
@(
    "",
    "https://127.0.0.1:1",
    "http://localhost:1",
    "http://127.0.0.1:0",
    "http://127.0.0.1:65536",
    "http://127.0.0.1:999999999999999999999",
    "http://127.0.0.1:1/path",
    "http://user@127.0.0.1:1",
    "http://127.0.0.1:1?query",
    "http://127.0.0.1:1#fragment"
) | ForEach-Object {
    Invoke-Guard "1" $_ "Candidate asset base must be loopback HTTP"
}

$validHost = @{
    ProductType = 1
    BuildNumber = "26100"
    Architectures = @(9)
}
Invoke-Guard "1" "http://127.0.0.1:1" "LOCALAPPDATA is unavailable" $validHost
Invoke-Guard "1" "http://127.0.0.1:65535/" "LOCALAPPDATA is unavailable" $validHost
Invoke-Guard "1" "http://127.0.0.1:1" "Windows 11 x64 only" @{
    ProductType = 3
    BuildNumber = "26100"
    Architectures = @(9)
}
Invoke-Guard "1" "http://127.0.0.1:1" "Windows 11 x64 only" @{
    ProductType = 1
    BuildNumber = "19045"
    Architectures = @(9)
}
Invoke-Guard "1" "http://127.0.0.1:1" "Windows 11 x64 only" @{
    ProductType = 1
    BuildNumber = "26100"
    Architectures = @(12)
}
Invoke-Guard "1" "http://127.0.0.1:1" "Windows 11 x64 only" @{
    ProductType = 1
    BuildNumber = "26100"
    Architectures = @()
}

$tokens = $null
$errors = $null
$installerAst = [Management.Automation.Language.Parser]::ParseFile(
    $Installer,
    [ref]$tokens,
    [ref]$errors
)
if ($errors.Count -ne 0) {
    throw "installer transaction functions could not be parsed"
}
$requiredFunctions = @(
    "Enter-InstallerLock",
    "Assert-InstallDirectorySlot",
    "Remove-InstallDirectory",
    "Undo-InstallTransaction",
    "Reconcile-InstallTransaction",
    "Invoke-InstallTransaction",
    "Install-LauncherAtomically",
    "Exit-InstallerScope"
)
$definitions = @($installerAst.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst]
}, $true))
foreach ($name in $requiredFunctions) {
    $definition = $definitions | Where-Object { $_.Name -eq $name } |
        Select-Object -First 1
    if ($null -eq $definition) {
        throw "missing installer transaction function: $name"
    }
    . ([scriptblock]::Create($definition.Extent.Text))
}

function Assert-AppVersion([string]$Root, [string]$Expected) {
    $actual = Get-Content -LiteralPath (Join-Path $Root "app\VERSION") -Raw
    if ($actual.Trim() -ne $Expected) {
        throw "expected app version $Expected, found $actual"
    }
}

$TransactionRoot = Join-Path ([IO.Path]::GetTempPath()) (
    "awesome-windows-transaction-contract-" + [Guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $TransactionRoot | Out-Null
$holder = $null
$crashed = $null
try {
    $faultRoot = Join-Path $TransactionRoot "fault"
    $candidate = Join-Path $faultRoot "candidate"
    $launcherDir = Join-Path $faultRoot "bin"
    New-Item -ItemType Directory -Force -Path (
        Join-Path $faultRoot "app"
    ), $candidate, $launcherDir | Out-Null
    Set-Content -LiteralPath (Join-Path $faultRoot "app\VERSION") -Value "old"
    Set-Content -LiteralPath (Join-Path $candidate "VERSION") -Value "new"
    $lock = Enter-InstallerLock -InstallRoot $faultRoot
    $originalLauncher = (Get-Item Function:\Install-LauncherAtomically).ScriptBlock
    try {
        Set-Item Function:\Install-LauncherAtomically -Value {
            param([string]$InstallRoot, [string]$LauncherDirectory)
            throw "injected launcher failure"
        }
        try {
            Invoke-InstallTransaction `
                -InstallRoot $faultRoot `
                -StagedApp $candidate `
                -LauncherDirectory $launcherDir
            throw "fault-injected transaction unexpectedly committed"
        }
        catch {
            if ($_.Exception.Message -eq "fault-injected transaction unexpectedly committed") {
                throw
            }
        }
        Assert-AppVersion $faultRoot "old"
        if ((Test-Path (Join-Path $faultRoot "app.rollback")) -or
            (Test-Path (Join-Path $faultRoot ".install-transaction"))) {
            throw "fault rollback left transaction residue"
        }
    }
    finally {
        Set-Item Function:\Install-LauncherAtomically -Value $originalLauncher
        $lock.Dispose()
    }

    $freshFaultRoot = Join-Path $TransactionRoot "fresh-fault"
    $candidate = Join-Path $freshFaultRoot "candidate"
    $launcherDir = Join-Path $freshFaultRoot "bin"
    New-Item -ItemType Directory -Force -Path $candidate, $launcherDir | Out-Null
    Set-Content -LiteralPath (Join-Path $candidate "VERSION") -Value "new"
    $lock = Enter-InstallerLock -InstallRoot $freshFaultRoot
    $originalLauncher = (Get-Item Function:\Install-LauncherAtomically).ScriptBlock
    try {
        Set-Item Function:\Install-LauncherAtomically -Value {
            param([string]$InstallRoot, [string]$LauncherDirectory)
            throw "injected fresh launcher failure"
        }
        try {
            Invoke-InstallTransaction `
                -InstallRoot $freshFaultRoot `
                -StagedApp $candidate `
                -LauncherDirectory $launcherDir
            throw "fresh fault-injected transaction unexpectedly committed"
        }
        catch {
            if ($_.Exception.Message -eq (
                "fresh fault-injected transaction unexpectedly committed"
            )) {
                throw
            }
        }
        if ((Test-Path (Join-Path $freshFaultRoot "app")) -or
            (Test-Path (Join-Path $freshFaultRoot "app.rollback")) -or
            (Test-Path (Join-Path $freshFaultRoot ".install-transaction"))) {
            throw "fresh fault rollback left a partial installation"
        }
    }
    finally {
        Set-Item Function:\Install-LauncherAtomically -Value $originalLauncher
        $lock.Dispose()
    }

    $successRoot = Join-Path $TransactionRoot "success"
    $candidate = Join-Path $successRoot "candidate"
    $launcherDir = Join-Path $successRoot "bin"
    New-Item -ItemType Directory -Force -Path (
        Join-Path $successRoot "app"
    ), $candidate, $launcherDir | Out-Null
    Set-Content -LiteralPath (Join-Path $successRoot "app\VERSION") -Value "old"
    Set-Content -LiteralPath (Join-Path $candidate "VERSION") -Value "new"
    Set-Content -LiteralPath (Join-Path $launcherDir "awesome.cmd") `
        -Value "old launcher"
    $lock = Enter-InstallerLock -InstallRoot $successRoot
    try {
        Invoke-InstallTransaction `
            -InstallRoot $successRoot `
            -StagedApp $candidate `
            -LauncherDirectory $launcherDir
        Assert-AppVersion $successRoot "new"
        if ((Test-Path (Join-Path $successRoot "app.rollback")) -or
            (Test-Path (Join-Path $successRoot ".install-transaction")) -or
            (Test-Path $candidate)) {
            throw "successful transaction left residue"
        }
        $launcherBody = Get-Content -LiteralPath (
            Join-Path $launcherDir "awesome.cmd"
        ) -Raw
        if ($launcherBody -notlike '*set "APP_ROOT=%~dp0..\app"*' -or
            $launcherBody -like "*$successRoot*") {
            throw "launcher is not relocatable"
        }
        if (@(Get-ChildItem -LiteralPath $launcherDir -Filter ".awesome.*").Count -ne 0) {
            throw "atomic launcher replacement left a temporary file"
        }
    }
    finally {
        $lock.Dispose()
    }

    $postCommitRoot = Join-Path $TransactionRoot "post-commit-cleanup"
    $candidate = Join-Path $postCommitRoot "candidate"
    $launcherDir = Join-Path $postCommitRoot "bin"
    New-Item -ItemType Directory -Force -Path (
        Join-Path $postCommitRoot "app"
    ), $candidate, $launcherDir | Out-Null
    Set-Content -LiteralPath (Join-Path $postCommitRoot "app\VERSION") -Value "old"
    Set-Content -LiteralPath (Join-Path $candidate "VERSION") -Value "new"
    $lock = Enter-InstallerLock -InstallRoot $postCommitRoot
    $originalRemove = (Get-Item Function:\Remove-InstallDirectory).ScriptBlock
    try {
        Set-Item Function:\Remove-InstallDirectory -Value {
            param([string]$Path)
            if ([IO.Path]::GetFileName($Path) -eq "app.rollback") {
                throw "injected committed cleanup failure"
            }
            & $originalRemove -Path $Path
        }
        Invoke-InstallTransaction `
            -InstallRoot $postCommitRoot `
            -StagedApp $candidate `
            -LauncherDirectory $launcherDir
        Assert-AppVersion $postCommitRoot "new"
        if (-not (Test-Path (Join-Path $postCommitRoot "app.rollback")) -or
            (Test-Path (Join-Path $postCommitRoot ".install-transaction"))) {
            throw "post-commit cleanup failure changed commit state"
        }
    }
    finally {
        Set-Item Function:\Remove-InstallDirectory -Value $originalRemove
    }
    Reconcile-InstallTransaction -InstallRoot $postCommitRoot
    Assert-AppVersion $postCommitRoot "new"
    if (Test-Path (Join-Path $postCommitRoot "app.rollback")) {
        throw "post-commit cleanup residue was not reconciled"
    }
    $lock.Dispose()

    $unicodeRoot = Join-Path $TransactionRoot "用户 安装"
    $unicodeLauncher = Join-Path $unicodeRoot "bin"
    New-Item -ItemType Directory -Force -Path $unicodeRoot | Out-Null
    Install-LauncherAtomically `
        -InstallRoot $unicodeRoot `
        -LauncherDirectory $unicodeLauncher
    $unicodeBody = Get-Content -LiteralPath (
        Join-Path $unicodeLauncher "awesome.cmd"
    ) -Raw
    if ($unicodeBody -like "*$unicodeRoot*" -or
        $unicodeBody -notlike '*%~dp0..\app*') {
        throw "launcher embedded a locale-sensitive absolute path"
    }

    $crashRoot = Join-Path $TransactionRoot "crash-residue"
    New-Item -ItemType Directory -Force -Path (
        Join-Path $crashRoot "app"
    ), (Join-Path $crashRoot "app.rollback"), (
        Join-Path $crashRoot ".install-transaction"
    ) | Out-Null
    Set-Content -LiteralPath (Join-Path $crashRoot "app\VERSION") `
        -Value "interrupted-new"
    Set-Content -LiteralPath (Join-Path $crashRoot "app.rollback\VERSION") `
        -Value "old"
    $lock = Enter-InstallerLock -InstallRoot $crashRoot
    try {
        Reconcile-InstallTransaction -InstallRoot $crashRoot
        Assert-AppVersion $crashRoot "old"
        if ((Test-Path (Join-Path $crashRoot "app.rollback")) -or
            (Test-Path (Join-Path $crashRoot ".install-transaction"))) {
            throw "crash reconciliation left transaction residue"
        }
    }
    finally {
        $lock.Dispose()
    }

    $blockedMarkerRoot = Join-Path $TransactionRoot "blocked-marker"
    New-Item -ItemType Directory -Force -Path (
        Join-Path $blockedMarkerRoot "app"
    ), (Join-Path $blockedMarkerRoot "app.rollback"), (
        Join-Path $blockedMarkerRoot ".install-transaction"
    ) | Out-Null
    Set-Content -LiteralPath (Join-Path $blockedMarkerRoot "app\VERSION") `
        -Value "interrupted-new"
    Set-Content -LiteralPath (Join-Path $blockedMarkerRoot "app.rollback\VERSION") `
        -Value "old"
    $markerBlocker = Join-Path $blockedMarkerRoot ".install-transaction\blocker"
    Set-Content -LiteralPath $markerBlocker -Value "blocker"
    $lock = Enter-InstallerLock -InstallRoot $blockedMarkerRoot
    try {
        try {
            Reconcile-InstallTransaction -InstallRoot $blockedMarkerRoot
            throw "nonempty transaction marker unexpectedly reconciled"
        }
        catch {
            if ($_.Exception.Message -eq (
                "nonempty transaction marker unexpectedly reconciled"
            )) {
                throw
            }
        }
        if ((Test-Path (Join-Path $blockedMarkerRoot "app")) -or
            -not (Test-Path (Join-Path $blockedMarkerRoot "app.rollback")) -or
            -not (Test-Path (Join-Path $blockedMarkerRoot ".install-transaction"))) {
            throw "failed marker cleanup lost recoverable rollback state"
        }
        Remove-Item -LiteralPath $markerBlocker -Force
        Reconcile-InstallTransaction -InstallRoot $blockedMarkerRoot
        Assert-AppVersion $blockedMarkerRoot "old"
    }
    finally {
        $lock.Dispose()
    }

    $onlyAppRoot = Join-Path $TransactionRoot "fresh-crash-residue"
    New-Item -ItemType Directory -Force -Path (
        Join-Path $onlyAppRoot "app"
    ), (Join-Path $onlyAppRoot ".install-transaction") | Out-Null
    Set-Content -LiteralPath (Join-Path $onlyAppRoot "app\VERSION") `
        -Value "interrupted-new"
    $lock = Enter-InstallerLock -InstallRoot $onlyAppRoot
    try {
        Reconcile-InstallTransaction -InstallRoot $onlyAppRoot
        Reconcile-InstallTransaction -InstallRoot $onlyAppRoot
        if (Test-Path (Join-Path $onlyAppRoot "app")) {
            throw "fresh crash reconciliation kept a partial application"
        }
        if (Test-Path (Join-Path $onlyAppRoot ".install-transaction")) {
            throw "first-install reconciliation left a marker"
        }
    }
    finally {
        $lock.Dispose()
    }

    $onlyRollbackRoot = Join-Path $TransactionRoot "only-rollback-residue"
    New-Item -ItemType Directory -Force -Path (
        Join-Path $onlyRollbackRoot "app.rollback"
    ) | Out-Null
    Set-Content -LiteralPath (Join-Path $onlyRollbackRoot "app.rollback\VERSION") `
        -Value "old"
    $lock = Enter-InstallerLock -InstallRoot $onlyRollbackRoot
    try {
        Reconcile-InstallTransaction -InstallRoot $onlyRollbackRoot
        Reconcile-InstallTransaction -InstallRoot $onlyRollbackRoot
        Assert-AppVersion $onlyRollbackRoot "old"
        if (Test-Path (Join-Path $onlyRollbackRoot "app.rollback")) {
            throw "rollback-only reconciliation left residue"
        }
    }
    finally {
        $lock.Dispose()
    }

    $reparseRoot = Join-Path $TransactionRoot "reparse-lock"
    $externalLockTarget = Join-Path $TransactionRoot "external-lock-target"
    New-Item -ItemType Directory -Force -Path $reparseRoot, $externalLockTarget |
        Out-Null
    Set-Content -LiteralPath (Join-Path $externalLockTarget "sentinel") -Value "safe"
    New-Item -ItemType Junction -Path (Join-Path $reparseRoot ".install.lock") `
        -Target $externalLockTarget | Out-Null
    try {
        $unexpected = Enter-InstallerLock -InstallRoot $reparseRoot
        $unexpected.Dispose()
        throw "reparse lock unexpectedly succeeded"
    }
    catch {
        if ($_.Exception.Message -eq "reparse lock unexpectedly succeeded") {
            throw
        }
    }
    if ((Get-Content -LiteralPath (Join-Path $externalLockTarget "sentinel") -Raw).Trim() `
        -ne "safe") {
        throw "reparse lock changed the external target"
    }

    $cleanupRoot = Join-Path $TransactionRoot "cleanup-lock"
    $cleanupStage = Join-Path $cleanupRoot ".install-stage-fault"
    New-Item -ItemType Directory -Force -Path $cleanupStage | Out-Null
    $lock = Enter-InstallerLock -InstallRoot $cleanupRoot
    $originalRemove = (Get-Item Function:\Remove-InstallDirectory).ScriptBlock
    $previousUv = $env:UV_PYTHON_INSTALL_DIR
    try {
        $env:UV_PYTHON_INSTALL_DIR = "changed-by-installer"
        Set-Item Function:\Remove-InstallDirectory -Value {
            param([string]$Path)
            throw "injected stage cleanup failure"
        }
        try {
            Exit-InstallerScope `
                -Stage $cleanupStage `
                -InstallLock $lock `
                -HadUvPythonInstallDir $true `
                -PreviousUvPythonInstallDir "original-uv-value"
            throw "cleanup fault unexpectedly succeeded"
        }
        catch {
            if ($_.Exception.Message -notlike "*injected stage cleanup failure*") {
                throw
            }
        }
        if ($env:UV_PYTHON_INSTALL_DIR -ne "original-uv-value") {
            throw "installer scope did not restore UV_PYTHON_INSTALL_DIR"
        }
        $afterCleanupFault = Enter-InstallerLock -InstallRoot $cleanupRoot
        $afterCleanupFault.Dispose()
    }
    finally {
        Set-Item Function:\Remove-InstallDirectory -Value $originalRemove
        $env:UV_PYTHON_INSTALL_DIR = $previousUv
    }

    $committedRoot = Join-Path $TransactionRoot "committed-residue"
    New-Item -ItemType Directory -Force -Path (
        Join-Path $committedRoot "app"
    ), (Join-Path $committedRoot "app.rollback") | Out-Null
    Set-Content -LiteralPath (Join-Path $committedRoot "app\VERSION") -Value "new"
    Set-Content -LiteralPath (Join-Path $committedRoot "app.rollback\VERSION") `
        -Value "old"
    $lock = Enter-InstallerLock -InstallRoot $committedRoot
    try {
        Reconcile-InstallTransaction -InstallRoot $committedRoot
        Assert-AppVersion $committedRoot "new"
        if (Test-Path (Join-Path $committedRoot "app.rollback")) {
            throw "committed rollback residue was not removed"
        }
    }
    finally {
        $lock.Dispose()
    }

    $childScript = Join-Path $TransactionRoot "lock-holder.ps1"
    @'
$ErrorActionPreference = "Stop"
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:AWESOME_TEST_INSTALLER,
    [ref]$tokens,
    [ref]$errors
)
$definition = @($ast.FindAll({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq "Enter-InstallerLock"
}, $true))[0]
. ([scriptblock]::Create($definition.Extent.Text))
$lock = Enter-InstallerLock -InstallRoot $env:AWESOME_TEST_LOCK_ROOT
try {
    New-Item -ItemType File -Path $env:AWESOME_TEST_LOCK_READY | Out-Null
    while (-not (Test-Path -LiteralPath $env:AWESOME_TEST_LOCK_RELEASE)) {
        Start-Sleep -Milliseconds 100
    }
}
finally {
    $lock.Dispose()
}
'@ | Set-Content -LiteralPath $childScript -Encoding UTF8

    $lockRoot = Join-Path $TransactionRoot "concurrent-lock"
    $ready = Join-Path $TransactionRoot "lock-ready"
    $release = Join-Path $TransactionRoot "lock-release"
    $env:AWESOME_TEST_INSTALLER = $Installer
    $env:AWESOME_TEST_LOCK_ROOT = $lockRoot
    $env:AWESOME_TEST_LOCK_READY = $ready
    $env:AWESOME_TEST_LOCK_RELEASE = $release
    $powerShellExecutable = (Get-Process -Id $PID).Path
    $holder = Start-Process -FilePath $powerShellExecutable `
        -ArgumentList "-NoProfile", "-File", $childScript `
        -WindowStyle Hidden -PassThru
    try {
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        while (-not (Test-Path -LiteralPath $ready)) {
            if ([DateTime]::UtcNow -ge $deadline) {
                throw "timed out waiting for installer lock holder"
            }
            Start-Sleep -Milliseconds 100
        }
        try {
            $unexpected = Enter-InstallerLock -InstallRoot $lockRoot
            $unexpected.Dispose()
            throw "concurrent installer acquired an active lock"
        }
        catch {
            if ($_.Exception.Message -eq "concurrent installer acquired an active lock") {
                throw
            }
        }
        New-Item -ItemType File -Path $release | Out-Null
        $holder.WaitForExit(15000) | Out-Null
        if (-not $holder.HasExited -or $holder.ExitCode -ne 0) {
            throw "installer lock holder did not exit cleanly"
        }
        $afterRelease = Enter-InstallerLock -InstallRoot $lockRoot
        $afterRelease.Dispose()
    }
    finally {
        if (-not $holder.HasExited) {
            Stop-Process -Id $holder.Id -Force
            $holder.WaitForExit() | Out-Null
        }
    }

    Remove-Item -LiteralPath $ready, $release -Force
    $crashed = Start-Process -FilePath $powerShellExecutable `
        -ArgumentList "-NoProfile", "-File", $childScript `
        -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $ready)) {
        if ([DateTime]::UtcNow -ge $deadline) {
            throw "timed out waiting for crash lock holder"
        }
        Start-Sleep -Milliseconds 100
    }
    Stop-Process -Id $crashed.Id -Force
    $crashed.WaitForExit() | Out-Null
    $afterCrash = Enter-InstallerLock -InstallRoot $lockRoot
    $afterCrash.Dispose()
}
finally {
    foreach ($backgroundProcess in @($holder, $crashed)) {
        if ($null -ne $backgroundProcess -and -not $backgroundProcess.HasExited) {
            Stop-Process -Id $backgroundProcess.Id -Force -ErrorAction SilentlyContinue
            $backgroundProcess.WaitForExit() | Out-Null
        }
    }
    Remove-Item Env:AWESOME_TEST_INSTALLER, Env:AWESOME_TEST_LOCK_ROOT, `
        Env:AWESOME_TEST_LOCK_READY, Env:AWESOME_TEST_LOCK_RELEASE `
        -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $TransactionRoot) {
        Remove-Item -LiteralPath $TransactionRoot -Recurse -Force
    }
}
