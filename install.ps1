$ErrorActionPreference = "Stop"
$Version = "1.3.1"
$UvVersion = "0.11.28"
$NodeVersion = "22.23.1"
$UvSha256 = "0a23463216d09c6a72ff80ef5dc5a795f07dc1575cb84d24596c2f124a441b7b"
$NodeSha256 = "7df0bc9375723f4a86b3aa1b7cc73342423d9677a8df4538aca31a049e309c29"
$AssetBase = "https://github.com/JAGGER-L/awesome_agent/releases/download/v$Version"

if ($args.Count -ne 0) {
    throw "This installer accepts no options."
}
$CandidateMode = if ([string]::IsNullOrWhiteSpace(
    $env:AWESOME_INSTALL_CANDIDATE
)) {
    "0"
}
else {
    $env:AWESOME_INSTALL_CANDIDATE
}
if ($CandidateMode -eq "0") {
    if (-not [string]::IsNullOrWhiteSpace(
        $env:AWESOME_INSTALL_CANDIDATE_ASSET_BASE
    )) {
        throw "Candidate asset base requires candidate mode."
    }
}
elseif ($CandidateMode -eq "1") {
    $CandidateBase = $env:AWESOME_INSTALL_CANDIDATE_ASSET_BASE
    [Uri]$CandidateUri = $null
    $CandidatePattern = '^http://127\.0\.0\.1:[0-9]+/?$'
    if ($CandidateBase -notmatch $CandidatePattern -or
        -not [Uri]::TryCreate($CandidateBase, [UriKind]::Absolute, [ref]$CandidateUri) -or
        $CandidateUri.Scheme -ne "http" -or
        $CandidateUri.Host -ne "127.0.0.1" -or
        $CandidateUri.Port -lt 1 -or
        $CandidateUri.Port -gt 65535 -or
        $CandidateUri.AbsolutePath -ne "/" -or
        -not [string]::IsNullOrEmpty($CandidateUri.Query) -or
        -not [string]::IsNullOrEmpty($CandidateUri.Fragment) -or
        -not [string]::IsNullOrEmpty($CandidateUri.UserInfo)) {
        throw "Candidate asset base must be loopback HTTP."
    }
    $AssetBase = "http://127.0.0.1:$($CandidateUri.Port)"
}
else {
    throw "Candidate mode must be 0 or 1."
}

$OperatingSystem = Get-CimInstance -ClassName Win32_OperatingSystem
$Processors = @(Get-CimInstance -ClassName Win32_Processor)
$BuildNumber = 0
$BuildNumberValid = [int]::TryParse(
    [string]$OperatingSystem.BuildNumber,
    [ref]$BuildNumber
)
$NativeX64 = $Processors.Count -gt 0 -and @(
    $Processors | Where-Object { [int]$_.Architecture -ne 9 }
).Count -eq 0
if ($OperatingSystem.ProductType -ne 1 -or
    -not $BuildNumberValid -or
    $BuildNumber -lt 22000 -or
    -not $NativeX64 -or
    -not [Environment]::Is64BitOperatingSystem) {
    throw "Awesome supports Windows 11 x64 only."
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "LOCALAPPDATA is unavailable."
}

$InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\Awesome"
$LauncherDir = Join-Path $InstallRoot "bin"

function Assert-NativeSuccess([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Assert-FileSha256(
    [string]$Path,
    [string]$Expected,
    [string]$Description
) {
    $Actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if ($Actual.ToLowerInvariant() -ne $Expected.ToLowerInvariant()) {
        throw "$Description checksum does not match."
    }
}

function Enter-InstallerLock([string]$InstallRoot) {
    if (Test-Path -LiteralPath $InstallRoot) {
        $RootItem = Get-Item -LiteralPath $InstallRoot -Force
        if (-not $RootItem.PSIsContainer -or
            ($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Install root must be an ordinary directory."
        }
    }
    else {
        New-Item -ItemType Directory -Path $InstallRoot | Out-Null
    }

    $LockPath = Join-Path $InstallRoot ".install.lock"
    if (Test-Path -LiteralPath $LockPath) {
        $LockItem = Get-Item -LiteralPath $LockPath -Force
        if ($LockItem.PSIsContainer -or
            ($LockItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Install lock must be an ordinary file."
        }
    }
    try {
        $LockStream = [IO.File]::Open(
            $LockPath,
            [IO.FileMode]::OpenOrCreate,
            [IO.FileAccess]::ReadWrite,
            [IO.FileShare]::None
        )
        $LockItem = Get-Item -LiteralPath $LockPath -Force
        if ($LockItem.PSIsContainer -or
            ($LockItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            $LockStream.Dispose()
            throw "Install lock must be an ordinary file."
        }
        return $LockStream
    }
    catch [IO.IOException] {
        throw [InvalidOperationException]::new(
            "Another Awesome installer is already running.",
            $_.Exception
        )
    }
}

function Assert-InstallDirectorySlot([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $Item = Get-Item -LiteralPath $Path -Force
    if (-not $Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "Install transaction path is not an ordinary directory."
    }
}

function Remove-InstallDirectory([string]$Path) {
    Assert-InstallDirectorySlot -Path $Path
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
}

function Remove-StaleInstallStages([string]$InstallRoot) {
    Get-ChildItem -LiteralPath $InstallRoot -Force | Where-Object {
        $_.Name -like ".install-stage-*"
    } | ForEach-Object {
        Assert-InstallDirectorySlot -Path $_.FullName
        Remove-InstallDirectory -Path $_.FullName
    }
}

function Undo-InstallTransaction([string]$InstallRoot) {
    $InstalledApp = Join-Path $InstallRoot "app"
    $RollbackApp = Join-Path $InstallRoot "app.rollback"
    $TransactionMarker = Join-Path $InstallRoot ".install-transaction"
    Assert-InstallDirectorySlot -Path $InstalledApp
    Assert-InstallDirectorySlot -Path $RollbackApp
    Assert-InstallDirectorySlot -Path $TransactionMarker

    $HasRollback = Test-Path -LiteralPath $RollbackApp
    Remove-InstallDirectory -Path $InstalledApp
    if (Test-Path -LiteralPath $TransactionMarker) {
        Remove-Item -LiteralPath $TransactionMarker -Force
    }
    if ($HasRollback) {
        Move-Item -LiteralPath $RollbackApp -Destination $InstalledApp
    }
}

function Reconcile-InstallTransaction([string]$InstallRoot) {
    $InstalledApp = Join-Path $InstallRoot "app"
    $RollbackApp = Join-Path $InstallRoot "app.rollback"
    $TransactionMarker = Join-Path $InstallRoot ".install-transaction"
    Assert-InstallDirectorySlot -Path $InstalledApp
    Assert-InstallDirectorySlot -Path $RollbackApp
    Assert-InstallDirectorySlot -Path $TransactionMarker

    if (Test-Path -LiteralPath $TransactionMarker) {
        Undo-InstallTransaction -InstallRoot $InstallRoot
        return
    }
    if (Test-Path -LiteralPath $RollbackApp) {
        if (Test-Path -LiteralPath $InstalledApp) {
            Remove-InstallDirectory -Path $RollbackApp
        }
        else {
            Move-Item -LiteralPath $RollbackApp -Destination $InstalledApp
        }
    }
}

function Install-LauncherAtomically(
    [string]$InstallRoot,
    [string]$LauncherDirectory
) {
    if (Test-Path -LiteralPath $LauncherDirectory) {
        Assert-InstallDirectorySlot -Path $LauncherDirectory
    }
    else {
        New-Item -ItemType Directory -Path $LauncherDirectory | Out-Null
    }
    $Launcher = Join-Path $LauncherDirectory "awesome.cmd"
    $TemporaryLauncher = Join-Path $LauncherDirectory (
        ".awesome." + [Guid]::NewGuid().ToString("N") + ".tmp"
    )
    $LauncherBackup = Join-Path $LauncherDirectory ".awesome.rollback"
    $LauncherBody = @"
@echo off
set "APP_ROOT=%~dp0..\app"
set "PATH=%APP_ROOT%\core\.venv\Scripts;%PATH%"
"%APP_ROOT%\runtimes\node\node.exe" "%APP_ROOT%\tui\dist\cli\index.js" %*
"@
    try {
        Set-Content -LiteralPath $TemporaryLauncher -Value $LauncherBody -Encoding Ascii
        if (Test-Path -LiteralPath $Launcher) {
            $LauncherItem = Get-Item -LiteralPath $Launcher -Force
            if ($LauncherItem.PSIsContainer -or
                ($LauncherItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Launcher path is not an ordinary file."
            }
            if (Test-Path -LiteralPath $LauncherBackup) {
                $BackupItem = Get-Item -LiteralPath $LauncherBackup -Force
                if ($BackupItem.PSIsContainer -or
                    ($BackupItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "Launcher rollback path is not an ordinary file."
                }
                Remove-Item -LiteralPath $LauncherBackup -Force
            }
            [IO.File]::Replace($TemporaryLauncher, $Launcher, $LauncherBackup)
            Remove-Item -LiteralPath $LauncherBackup -Force
        }
        else {
            [IO.File]::Move($TemporaryLauncher, $Launcher)
        }
    }
    finally {
        if (Test-Path -LiteralPath $TemporaryLauncher) {
            Remove-Item -LiteralPath $TemporaryLauncher -Force
        }
    }
}

function Invoke-InstallTransaction(
    [string]$InstallRoot,
    [string]$StagedApp,
    [string]$LauncherDirectory
) {
    Assert-InstallDirectorySlot -Path $StagedApp
    if (-not (Test-Path -LiteralPath $StagedApp -PathType Container)) {
        throw "Staged application is missing."
    }

    Reconcile-InstallTransaction -InstallRoot $InstallRoot
    $InstalledApp = Join-Path $InstallRoot "app"
    $RollbackApp = Join-Path $InstallRoot "app.rollback"
    $TransactionMarker = Join-Path $InstallRoot ".install-transaction"
    try {
        if (Test-Path -LiteralPath $InstalledApp) {
            Move-Item -LiteralPath $InstalledApp -Destination $RollbackApp
        }
        New-Item -ItemType Directory -Path $TransactionMarker | Out-Null
        Move-Item -LiteralPath $StagedApp -Destination $InstalledApp
        Install-LauncherAtomically `
            -InstallRoot $InstallRoot `
            -LauncherDirectory $LauncherDirectory
        Remove-Item -LiteralPath $TransactionMarker -Force
    }
    catch {
        $PrimaryFailure = $_.Exception
        try {
            Undo-InstallTransaction -InstallRoot $InstallRoot
        }
        catch {
            throw [InvalidOperationException]::new(
                "Installation failed and the previous application could not be restored.",
                $PrimaryFailure
            )
        }
        throw $PrimaryFailure
    }
    try {
        Remove-InstallDirectory -Path $RollbackApp
    }
    catch {
        Write-Warning "Committed rollback cleanup was deferred."
    }
}

function Exit-InstallerScope(
    [AllowNull()][string]$Stage,
    [AllowNull()][IO.FileStream]$InstallLock,
    [bool]$HadUvPythonInstallDir,
    [AllowNull()][string]$PreviousUvPythonInstallDir
) {
    try {
        if ($null -ne $Stage -and (Test-Path -LiteralPath $Stage)) {
            Remove-InstallDirectory -Path $Stage
        }
    }
    finally {
        try {
            if ($HadUvPythonInstallDir) {
                $env:UV_PYTHON_INSTALL_DIR = $PreviousUvPythonInstallDir
            }
            else {
                Remove-Item Env:UV_PYTHON_INSTALL_DIR -ErrorAction SilentlyContinue
            }
        }
        finally {
            if ($null -ne $InstallLock) {
                $InstallLock.Dispose()
            }
        }
    }
}

$InstallLock = Enter-InstallerLock -InstallRoot $InstallRoot
$HadUvPythonInstallDir = Test-Path Env:UV_PYTHON_INSTALL_DIR
$PreviousUvPythonInstallDir = $env:UV_PYTHON_INSTALL_DIR
$Stage = $null
try {
    Reconcile-InstallTransaction -InstallRoot $InstallRoot
    Remove-StaleInstallStages -InstallRoot $InstallRoot
    $Stage = Join-Path $InstallRoot (
        ".install-stage-" + [Guid]::NewGuid().ToString("N")
    )
    New-Item -ItemType Directory -Path $Stage | Out-Null
    $UvDir = Join-Path $Stage "uv"
    $Downloads = Join-Path $Stage "downloads"
    $StagedApp = Join-Path $Stage "app"
    $PythonRuntime = Join-Path $StagedApp "runtimes\python"
    New-Item -ItemType Directory -Force -Path $UvDir, $Downloads, $PythonRuntime |
        Out-Null

    $UvArchiveName = "uv-x86_64-pc-windows-msvc.zip"
    $UvArchive = Join-Path $Downloads $UvArchiveName
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://releases.astral.sh/github/uv/releases/download/$UvVersion/$UvArchiveName" `
        -OutFile $UvArchive
    Assert-FileSha256 $UvArchive $UvSha256 "uv"
    Expand-Archive -LiteralPath $UvArchive -DestinationPath $UvDir
    $Uv = Join-Path $UvDir "uv.exe"
    if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
        throw "uv bootstrap did not produce uv.exe."
    }

    $env:UV_PYTHON_INSTALL_DIR = $PythonRuntime
    & $Uv python install 3.12 --no-registry --no-bin
    Assert-NativeSuccess "private Python install"
    $Python = (& $Uv python find --managed-python 3.12 | Select-Object -Last 1).Trim()
    Assert-NativeSuccess "private Python lookup"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Private Python 3.12 was not installed."
    }
    $Python = (& $Python -c `
        "import os, sys; print(os.path.realpath(sys.executable))").Trim()
    Assert-NativeSuccess "private Python path resolution"
    $ResolvedPython = [IO.Path]::GetFullPath($Python)
    $ResolvedPythonRoot = [IO.Path]::GetFullPath($PythonRuntime).TrimEnd("\") + "\"
    if (-not $ResolvedPython.StartsWith(
        $ResolvedPythonRoot,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Private Python escaped the staged runtime."
    }

    $BundleName = "awesome-$Version.zip"
    $Bundle = Join-Path $Downloads $BundleName
    $Checksums = Join-Path $Downloads "SHA256SUMS"
    Invoke-WebRequest -UseBasicParsing -Uri "$AssetBase/$BundleName" -OutFile $Bundle
    Invoke-WebRequest -UseBasicParsing -Uri "$AssetBase/SHA256SUMS" `
        -OutFile $Checksums
    $ChecksumLine = Get-Content -LiteralPath $Checksums | Where-Object {
        $_ -match "\s+$([Regex]::Escape($BundleName))$"
    } | Select-Object -First 1
    if ($null -eq $ChecksumLine) {
        throw "Release checksum is missing."
    }
    $Expected = ($ChecksumLine -split "\s+")[0].ToLowerInvariant()
    $Actual = (Get-FileHash -LiteralPath $Bundle -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Actual -ne $Expected) {
        throw "Release checksum does not match."
    }

    $Extracted = Join-Path $Stage "extracted"
    Expand-Archive -LiteralPath $Bundle -DestinationPath $Extracted
    $BundleRoot = Join-Path $Extracted "awesome-$Version"
    if (-not (Test-Path -LiteralPath $BundleRoot -PathType Container)) {
        throw "Release bundle root is invalid."
    }
    Get-ChildItem -LiteralPath $BundleRoot -Force |
        Copy-Item -Destination $StagedApp -Recurse -Force

    $NodeArchiveName = "node-v$NodeVersion-win-x64.zip"
    $NodeArchive = Join-Path $Downloads $NodeArchiveName
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://nodejs.org/dist/v$NodeVersion/$NodeArchiveName" `
        -OutFile $NodeArchive
    Assert-FileSha256 $NodeArchive $NodeSha256 "Node"
    $NodeExtracted = Join-Path $Stage "node"
    Expand-Archive -LiteralPath $NodeArchive -DestinationPath $NodeExtracted
    $NodeSource = Join-Path $NodeExtracted "node-v$NodeVersion-win-x64"
    $NodeRuntime = Join-Path $StagedApp "runtimes\node"
    New-Item -ItemType Directory -Force -Path $NodeRuntime | Out-Null
    Get-ChildItem -LiteralPath $NodeSource -Force |
        Copy-Item -Destination $NodeRuntime -Recurse -Force
    $Node = Join-Path $NodeRuntime "node.exe"
    $NpmCli = Join-Path $NodeRuntime "node_modules\npm\bin\npm-cli.js"
    if (-not (Test-Path -LiteralPath $Node -PathType Leaf) -or
        -not (Test-Path -LiteralPath $NpmCli -PathType Leaf)) {
        throw "Private Node runtime is incomplete."
    }

    $CoreEnvironment = Join-Path $StagedApp "core\.venv"
    $SitePackages = Join-Path $CoreEnvironment "site-packages"
    $CoreBin = Join-Path $CoreEnvironment "Scripts"
    New-Item -ItemType Directory -Force -Path $SitePackages, $CoreBin | Out-Null
    $Wheel = Join-Path $StagedApp "core\awesome_agent-$Version-py3-none-any.whl"
    $Requirements = Join-Path $StagedApp "core\requirements.lock"
    if (-not (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
        throw "Locked Core requirements are missing."
    }
    & $Uv pip install --python $Python --target $SitePackages `
        --require-hashes --requirement $Requirements
    Assert-NativeSuccess "private Core dependency install"
    & $Uv pip install --python $Python --target $SitePackages `
        --no-deps "$Wheel[memory]"
    Assert-NativeSuccess "private Core install"
    $ResolvedApp = [IO.Path]::GetFullPath($StagedApp).TrimEnd("\") + "\"
    $PythonRelative = $ResolvedPython.Substring($ResolvedApp.Length)
    $CoreWrapper = Join-Path $CoreBin "awesome-core.cmd"
    $CoreWrapperBody = @"
@echo off
set "APP_ROOT=%~dp0..\..\.."
set "PYTHONPATH=%APP_ROOT%\core\.venv\site-packages"
"%APP_ROOT%\$PythonRelative" -c "import site,sys; site.addsitedir(sys.argv.pop(1)); from awesome_agent.protocol.stdio import main; main()" "%PYTHONPATH%" %*
"@
    Set-Content -LiteralPath $CoreWrapper -Value $CoreWrapperBody -Encoding Ascii
    & $Node $NpmCli ci --omit=dev --ignore-scripts --prefix (Join-Path $StagedApp "tui")
    Assert-NativeSuccess "private TUI install"

    $OldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $SitePackages
        $PythonVersion = (& $Python -c (
            "from awesome_agent.version import PRODUCT_VERSION; print(PRODUCT_VERSION)"
        )).Trim()
        Assert-NativeSuccess "private Core version check"
    }
    finally {
        $env:PYTHONPATH = $OldPythonPath
    }
    $NodeMajor = (& $Node -p `
        'process.versions.node.split(String.fromCharCode(46))[0]').Trim()
    Assert-NativeSuccess "private Node version check"
    $OldPath = $env:PATH
    try {
        $env:PATH = "$CoreBin;$OldPath"
        $CliVersion = (& $Node (Join-Path $StagedApp "tui\dist\cli\index.js") `
            --version).Trim()
        Assert-NativeSuccess "public CLI version check"
    }
    finally {
        $env:PATH = $OldPath
    }
    if ($PythonVersion -ne $Version -or $NodeMajor -ne "22" -or
        $CliVersion -ne $Version) {
        throw "Staged application version validation failed."
    }
    Write-Output "validated"

    Invoke-InstallTransaction `
        -InstallRoot $InstallRoot `
        -StagedApp $StagedApp `
        -LauncherDirectory $LauncherDir

    try {
        $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
        $PathParts = @($UserPath -split ";" | Where-Object {
            -not [string]::IsNullOrWhiteSpace($_)
        })
        if (-not ($PathParts | Where-Object { $_.TrimEnd("\") -ieq $LauncherDir })) {
            $UpdatedPath = if ([string]::IsNullOrWhiteSpace($UserPath)) {
                $LauncherDir
            }
            else {
                "$LauncherDir;$($UserPath.TrimStart(';'))"
            }
            [Environment]::SetEnvironmentVariable("Path", $UpdatedPath, "User")
        }
    }
    catch {
        Write-Warning "Update the user PATH manually to include $LauncherDir."
    }

    if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Warning "Git is not installed. Install it from https://git-scm.com/downloads"
    }
    Write-Output "Awesome $Version installed. Open a new terminal and run: awesome"
    Write-Output "Close every existing AWESOME session before rerunning this installer."
}
finally {
    Exit-InstallerScope `
        -Stage $Stage `
        -InstallLock $InstallLock `
        -HadUvPythonInstallDir $HadUvPythonInstallDir `
        -PreviousUvPythonInstallDir $PreviousUvPythonInstallDir
}
