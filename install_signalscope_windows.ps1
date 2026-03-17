param(
    [string]$InstallDir = "$env:ProgramData\SignalScope",
    [string]$DataDir = "$env:ProgramData\SignalScope\data",
    [string]$LogDir = "$env:ProgramData\SignalScope\logs",
    [switch]$Force,
    [switch]$Sdr,
    [switch]$CreateStartupTask,
    [switch]$NoStartupTask
)

$ErrorActionPreference = "Stop"

$AppName = "SignalScope"
$AppPyName = "signalscope.py"
$LegacyAppPy = "LivewireAIMonitor.py"
$RepoUrl = "https://github.com/itconor/SignalScope.git"
$RawBaseUrl = "https://raw.githubusercontent.com/itconor/SignalScope/main"
$TempRoot = Join-Path $env:TEMP "signalscope-installer"

function Write-Info($msg) {
    Write-Host "[INFO] $msg" -ForegroundColor Cyan
}

function Write-WarnMsg($msg) {
    Write-Host "[WARN] $msg" -ForegroundColor Yellow
}

function Write-ErrMsg($msg) {
    Write-Host "[ERROR] $msg" -ForegroundColor Red
}

function Test-Admin {
    $currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Ensure-Command($name) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "Missing required command: $name"
    }
}

function Resolve-Python {
    $candidates = @(
        "py",
        "python",
        "$env:LocalAppData\Programs\Python\Python312\python.exe",
        "$env:LocalAppData\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )

    foreach ($candidate in $candidates) {
        if ($candidate -eq "py") {
            $py = Get-Command py -ErrorAction SilentlyContinue
            if ($py) { return @{ Command = "py"; VenvCommand = "py"; UsesLauncher = $true } }
        } elseif ($candidate -eq "python") {
            $py = Get-Command python -ErrorAction SilentlyContinue
            if ($py) { return @{ Command = $py.Source; VenvCommand = $py.Source; UsesLauncher = $false } }
        } elseif (Test-Path $candidate) {
            return @{ Command = $candidate; VenvCommand = $candidate; UsesLauncher = $false }
        }
    }

    throw "Python 3 was not found. Install Python 3.11+ from python.org or the Microsoft Store, then rerun this script."
}

function Invoke-External {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [Parameter(Mandatory=$false)][string[]]$Arguments = @(),
        [string]$WorkingDirectory = $PWD.Path
    )

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    foreach ($arg in $Arguments) {
        [void]$psi.ArgumentList.Add($arg)
    }
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false

    $p = New-Object System.Diagnostics.Process
    $p.StartInfo = $psi
    [void]$p.Start()
    $stdout = $p.StandardOutput.ReadToEnd()
    $stderr = $p.StandardError.ReadToEnd()
    $p.WaitForExit()

    if ($stdout) { Write-Host $stdout.TrimEnd() }
    if ($p.ExitCode -ne 0) {
        if ($stderr) { Write-Host $stderr.TrimEnd() -ForegroundColor Red }
        throw "Command failed: $FilePath $($Arguments -join ' ')"
    } elseif ($stderr) {
        Write-Host $stderr.TrimEnd() -ForegroundColor DarkYellow
    }
}

function Resolve-SourceTree {
    $cwd = (Get-Location).Path
    $appFromCwd = Join-Path $cwd $AppPyName
    $legacyFromCwd = Join-Path $cwd $LegacyAppPy

    if (Test-Path $appFromCwd) {
        Write-Info "Using local source: $appFromCwd"
        return @{
            SourceDir = $cwd
            SourceApp = $appFromCwd
        }
    }

    if (Test-Path $legacyFromCwd) {
        Write-Info "Using local legacy source: $legacyFromCwd"
        return @{
            SourceDir = $cwd
            SourceApp = $legacyFromCwd
        }
    }

    if (Test-Path $TempRoot) {
        Remove-Item -Recurse -Force $TempRoot
    }
    New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null

    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        try {
            Write-Info "Fetching source from GitHub with git..."
            Invoke-External -FilePath $git.Source -Arguments @("clone", "--depth", "1", $RepoUrl, $TempRoot)
        } catch {
            Write-WarnMsg "Git clone failed, falling back to direct download."
        }
    }

    $appPath = Join-Path $TempRoot $AppPyName
    if (-not (Test-Path $appPath)) {
        Write-Info "Downloading $AppPyName directly from GitHub..."
        Invoke-WebRequest -UseBasicParsing -Uri "$RawBaseUrl/$AppPyName" -OutFile $appPath
    }

    $legacyPath = Join-Path $TempRoot $LegacyAppPy
    if (-not (Test-Path $appPath) -and (Test-Path $legacyPath)) {
        Copy-Item $legacyPath $appPath -Force
    }

    if (-not (Test-Path $appPath)) {
        throw "Failed to obtain $AppPyName from GitHub."
    }

    return @{
        SourceDir = $TempRoot
        SourceApp = $appPath
    }
}

function Copy-SourceFiles {
    param(
        [string]$SourceDir,
        [string]$InstallDir
    )

    New-Item -ItemType Directory -Force -Path $InstallDir, $DataDir, $LogDir | Out-Null

    $targetApp = Join-Path $InstallDir $AppPyName
    if ((Test-Path $targetApp) -and (-not $Force)) {
        Write-Info "Existing $targetApp found. Keeping it. Use -Force to overwrite."
    } else {
        Copy-Item (Join-Path $SourceDir $AppPyName) $targetApp -Force
        Write-Info "Installed application file to $targetApp"
    }

    $staticDir = Join-Path $SourceDir "static"
    if (Test-Path $staticDir) {
        $targetStatic = Join-Path $InstallDir "static"
        New-Item -ItemType Directory -Force -Path $targetStatic | Out-Null
        Copy-Item (Join-Path $staticDir "*") $targetStatic -Recurse -Force -ErrorAction SilentlyContinue
        Write-Info "Copied static assets."
    } else {
        Write-WarnMsg "No static directory found in source tree."
    }

    $requirements = Join-Path $SourceDir "requirements.txt"
    if (Test-Path $requirements) {
        Copy-Item $requirements (Join-Path $InstallDir "requirements.txt") -Force
    }
}

function New-VenvAndInstallDeps {
    param(
        [hashtable]$PythonInfo,
        [string]$InstallDir
    )

    $venvDir = Join-Path $InstallDir "venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    $venvPip = Join-Path $venvDir "Scripts\pip.exe"

    if (-not (Test-Path $venvPython)) {
        Write-Info "Creating virtual environment..."
        if ($PythonInfo.UsesLauncher) {
            Invoke-External -FilePath "py" -Arguments @("-3", "-m", "venv", $venvDir)
        } else {
            Invoke-External -FilePath $PythonInfo.VenvCommand -Arguments @("-m", "venv", $venvDir)
        }
    }

    Write-Info "Upgrading pip, wheel and setuptools..."
    Invoke-External -FilePath $venvPython -Arguments @("-m", "pip", "install", "--upgrade", "pip<25", "wheel", "setuptools<81")

    $requirements = Join-Path $InstallDir "requirements.txt"
    if (Test-Path $requirements) {
        Write-Info "Installing Python requirements from requirements.txt..."
        Invoke-External -FilePath $venvPython -Arguments @("-m", "pip", "install", "-r", $requirements)
    } else {
        Write-Info "Installing core Python dependencies..."
        Invoke-External -FilePath $venvPython -Arguments @("-m", "pip", "install", "flask", "waitress", "cheroot", "numpy", "scipy", "requests", "certifi", "cryptography")
    }

    try {
        Invoke-External -FilePath $venvPython -Arguments @("-m", "pip", "install", "onnx", "onnxruntime")
    } catch {
        Write-WarnMsg "ONNX stack install failed. The app can still run, but AI/ONNX features may be unavailable."
    }

    if ($Sdr) {
        try {
            Invoke-External -FilePath $venvPython -Arguments @("-m", "pip", "install", "pyrtlsdr==0.2.93")
            Write-WarnMsg "pyrtlsdr installed. You may still need Zadig/WinUSB drivers for RTL-SDR devices on Windows."
            Write-WarnMsg "redsea and welle.io are not installed automatically by this Windows script."
        } catch {
            Write-WarnMsg "Failed to install pyrtlsdr automatically."
        }
    }

    return @{
        VenvDir = $venvDir
        VenvPython = $venvPython
    }
}

function Write-LauncherFiles {
    param(
        [string]$InstallDir,
        [string]$VenvPython
    )

    $appPath = Join-Path $InstallDir $AppPyName
    $batPath = Join-Path $InstallDir "run_signalscope.bat"
    $ps1Path = Join-Path $InstallDir "run_signalscope.ps1"

    $bat = @"
@echo off
cd /d "$InstallDir"
"$VenvPython" "$appPath"
pause
"@
    Set-Content -Path $batPath -Value $bat -Encoding ASCII

    $ps = @"
Set-Location "$InstallDir"
& "$VenvPython" "$appPath"
"@
    Set-Content -Path $ps1Path -Value $ps -Encoding UTF8

    Write-Info "Created launchers:"
    Write-Host "  $batPath"
    Write-Host "  $ps1Path"
}

function Register-StartupTask {
    param(
        [string]$InstallDir,
        [string]$VenvPython
    )

    $taskName = "SignalScope"
    $appPath = Join-Path $InstallDir $AppPyName
    $action = New-ScheduledTaskAction -Execute $VenvPython -Argument "`"$appPath`"" -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Start SignalScope at user logon" -Force | Out-Null
    Write-Info "Created Scheduled Task '$taskName' to start SignalScope at logon."
}

Write-Host "== $AppName Windows installer ==" -ForegroundColor Green
Write-Host "Install dir: $InstallDir"

if (-not (Test-Admin)) {
    Write-WarnMsg "Not running as Administrator. Installation can still work, but creating a startup task or writing to ProgramData may fail."
}

$pythonInfo = Resolve-Python
Write-Info "Using Python: $($pythonInfo.Command)"

$source = Resolve-SourceTree
Copy-SourceFiles -SourceDir $source.SourceDir -InstallDir $InstallDir
$venv = New-VenvAndInstallDeps -PythonInfo $pythonInfo -InstallDir $InstallDir
Write-LauncherFiles -InstallDir $InstallDir -VenvPython $venv.VenvPython

if ($CreateStartupTask -and -not $NoStartupTask) {
    try {
        Register-StartupTask -InstallDir $InstallDir -VenvPython $venv.VenvPython
    } catch {
        Write-WarnMsg "Failed to create startup task: $($_.Exception.Message)"
    }
}

Write-Host ""
Write-Host "Installation complete." -ForegroundColor Green
Write-Host "Installed app: $(Join-Path $InstallDir $AppPyName)"
Write-Host "Virtualenv: $($venv.VenvDir)"
Write-Host "Data dir: $DataDir"
Write-Host "Log dir: $LogDir"
Write-Host ""
Write-Host "Run SignalScope with:"
Write-Host "  $(Join-Path $InstallDir 'run_signalscope.bat')"
Write-Host ""
Write-Host "Then open:"
Write-Host "  http://localhost:5000"
Write-Host ""

if ($Sdr) {
    Write-Host "SDR notes:" -ForegroundColor Yellow
    Write-Host "  - Install the RTL-SDR WinUSB driver with Zadig if Windows does not expose the dongle correctly."
    Write-Host "  - redsea and welle.io are not automatically installed by this Windows installer."
}
