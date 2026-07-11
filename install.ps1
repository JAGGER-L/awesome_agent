$ErrorActionPreference = "Stop"
$Version = "1.0.0"
$UvVersion = "0.11.28"
$NodeVersion = "22.23.1"
$AssetBase = "https://github.com/JAGGER-L/awesome_agent/releases/latest/download"

if ($args.Count -ne 0) {
    throw "This installer accepts no options."
}
if (-not [Environment]::Is64BitOperatingSystem -or
    $env:PROCESSOR_ARCHITECTURE -ne "AMD64" -or
    [Environment]::OSVersion.Version.Build -lt 22000) {
    throw "Awesome supports Windows 11 x64 only."
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "LOCALAPPDATA is unavailable."
}

$InstallRoot = Join-Path $env:LOCALAPPDATA "Programs\Awesome"
$LauncherDir = Join-Path $InstallRoot "bin"
$Stage = Join-Path ([IO.Path]::GetTempPath()) (
    "awesome-install-" + [Guid]::NewGuid().ToString("N")
)

function Assert-NativeSuccess([string]$Operation) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

New-Item -ItemType Directory -Path $Stage | Out-Null
try {
    $UvDir = Join-Path $Stage "uv"
    $Downloads = Join-Path $Stage "downloads"
    $StagedApp = Join-Path $Stage "app"
    $PythonRuntime = Join-Path $StagedApp "runtimes\python"
    New-Item -ItemType Directory -Force -Path $UvDir, $Downloads, $PythonRuntime |
        Out-Null

    $UvInstaller = Join-Path $Stage "uv-install.ps1"
    Invoke-RestMethod -Uri "https://astral.sh/uv/$UvVersion/install.ps1" `
        -OutFile $UvInstaller
    $env:UV_UNMANAGED_INSTALL = $UvDir
    $env:UV_NO_MODIFY_PATH = "1"
    & $UvInstaller
    Assert-NativeSuccess "uv bootstrap"
    $Uv = Join-Path $UvDir "uv.exe"
    if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) {
        throw "uv bootstrap did not produce uv.exe."
    }

    $env:UV_PYTHON_INSTALL_DIR = $PythonRuntime
    & $Uv python install 3.12 --no-registry
    Assert-NativeSuccess "private Python install"
    $Python = (& $Uv python find 3.12 | Select-Object -Last 1).Trim()
    Assert-NativeSuccess "private Python lookup"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Private Python 3.12 was not installed."
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

    $Venv = Join-Path $StagedApp "core\.venv"
    & $Uv venv --python $Python $Venv
    Assert-NativeSuccess "private Core environment creation"
    $VenvPython = Join-Path $Venv "Scripts\python.exe"
    $Wheel = Join-Path $StagedApp "core\awesome_agent-$Version-py3-none-any.whl"
    & $Uv pip install --python $VenvPython "$Wheel[memory]"
    Assert-NativeSuccess "private Core install"
    & $Node $NpmCli ci --omit=dev --ignore-scripts --prefix (Join-Path $StagedApp "tui")
    Assert-NativeSuccess "private TUI install"

    $PythonVersion = (& $VenvPython -c (
        "from awesome_agent.version import PRODUCT_VERSION; print(PRODUCT_VERSION)"
    )).Trim()
    Assert-NativeSuccess "private Core version check"
    $NodeMajor = (& $Node -p 'process.versions.node.split(".")[0]').Trim()
    Assert-NativeSuccess "private Node version check"
    $OldPath = $env:PATH
    try {
        $env:PATH = "$(Join-Path $Venv 'Scripts');$OldPath"
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

    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    $InstalledApp = Join-Path $InstallRoot "app"
    if (Test-Path -LiteralPath $InstalledApp) {
        Remove-Item -LiteralPath $InstalledApp -Recurse -Force
    }
    Move-Item -LiteralPath $StagedApp -Destination $InstalledApp

    New-Item -ItemType Directory -Force -Path $LauncherDir | Out-Null
    $Launcher = Join-Path $LauncherDir "awesome.cmd"
    $LauncherBody = @"
@echo off
set "APP_ROOT=$InstalledApp"
set "PATH=%APP_ROOT%\core\.venv\Scripts;%PATH%"
"%APP_ROOT%\runtimes\node\node.exe" "%APP_ROOT%\tui\dist\cli\index.js" %*
"@
    Set-Content -LiteralPath $Launcher -Value $LauncherBody -Encoding Ascii

    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $PathParts = @($UserPath -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    if (-not ($PathParts | Where-Object { $_.TrimEnd("\") -ieq $LauncherDir })) {
        $UpdatedPath = if ([string]::IsNullOrWhiteSpace($UserPath)) {
            $LauncherDir
        }
        else {
            "$($UserPath.TrimEnd(';'));$LauncherDir"
        }
        [Environment]::SetEnvironmentVariable("Path", $UpdatedPath, "User")
    }

    if ($null -eq (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Warning "Git is not installed. Install it from https://git-scm.com/downloads"
    }
    Write-Output "Awesome $Version installed. Open a new terminal and run: awesome"
    Write-Output "Close every existing AWESOME session before rerunning this installer."
}
finally {
    if (Test-Path -LiteralPath $Stage) {
        Remove-Item -LiteralPath $Stage -Recurse -Force
    }
}
