# SHOBS-P: finds a Python that can run SHOBS-P, sets up SHOBS-P's own private environment, then does one job.
# Called by "Create shortcuts.bat" (-Action shortcuts), "run_windows.bat" (-Action run) and
# "Run self-test.bat" (-Action selftest). It never changes the Python it finds.
#
# 2.2.18.1: the .bat files used to trust the Windows "py" launcher whenever it existed. On a PC where the launcher
# is installed but knows no Python (for example with Anaconda), that stopped everything. Now each candidate is run
# and tested (3.11 or newer, SSL, Tkinter), and the first one that works is used.
#
# For testing: -UsePython <path to python.exe> tries only that Python, and -ProfileName keeps the test apart from the
# real setup (its own folder in %LOCALAPPDATA%), e.g.
#   powershell -ExecutionPolicy Bypass -File setup_python.ps1 -Action selftest -UsePython C:\...\python.exe -ProfileName SHOBS-P-test
param(
    [string]$AppDir = $PSScriptRoot,
    [ValidateSet('shortcuts', 'run', 'selftest')][string]$Action = 'shortcuts',
    [string]$UsePython = '',
    [string]$ProfileName = 'SHOBS-P'
)
$ErrorActionPreference = 'Continue'
$AppDir = (Resolve-Path $AppDir).Path
$Check = Join-Path $AppDir 'shobs_check_python.py'
$Requirements = Join-Path $AppDir 'requirements.txt'

# Python writes UTF-8 and PowerShell reads UTF-8, so paths with accented letters survive.
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}

# SHOBS-P's own place in the user's folder: shared by every SHOBS-P version, so an update needs no new downloads.
$Home2 = Join-Path $env:LOCALAPPDATA $ProfileName
New-Item -ItemType Directory -Force -Path $Home2 | Out-Null
$EnvDir = Join-Path $Home2 'python-env'
$EnvPy = Join-Path $EnvDir 'Scripts\python.exe'
$Record = Join-Path $Home2 'python.txt'

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

# Runs the checker with one candidate. Returns the full path of that python.exe, or $null (and says why).
function Test-Python([string]$Exe, [string[]]$Pre = @(), [switch]$Packages, [switch]$Quiet) {
    $argv = @() + $Pre + @($Check)
    if ($Packages) { $argv += '--packages' }
    try {
        $out = & $Exe @argv 2>&1 | Out-String
        $code = $LASTEXITCODE
    } catch {
        if (-not $Quiet) { Write-Host "    not usable: $($_.Exception.Message)" }
        return $null
    }
    $path = $null
    foreach ($line in ($out -split "`r?`n")) {
        if ($line.StartsWith('SHOBSPY=')) { $path = $line.Substring(8).Trim() }
    }
    if ($code -eq 0 -and $path -and (Test-Path -LiteralPath $path)) { return $path }
    if (-not $Quiet) {
        $last = ($out -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
        if (-not $last) { $last = "exit code $code" }
        Write-Host "    not usable: $last"
    }
    return $null
}

function Find-BasePython {
    if ($UsePython) {
        Write-Host "  Trying only $UsePython (-UsePython)..."
        return (Test-Python -Exe $UsePython)
    }
    $candidates = New-Object System.Collections.ArrayList
    # Names on the PATH. The py launcher first (python.org's usual way), then plain python and python3.
    if (Get-Command py -ErrorAction SilentlyContinue) {
        [void]$candidates.Add(@{ Exe = 'py'; Pre = @('-3'); Label = 'py -3 (Windows Python launcher)' })
    }
    foreach ($name in 'python', 'python3') {
        # Every match on the PATH, skipping the Microsoft Store placeholder (WindowsApps\python.exe), which only
        # offers to install Python and would otherwise hide a real Python further down the PATH.
        $cmds = Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue |
                Where-Object { $_.Source -notmatch '\\WindowsApps\\' }
        foreach ($cmd in $cmds) {
            [void]$candidates.Add(@{ Exe = $cmd.Source; Pre = @(); Label = "$name ($($cmd.Source))" })
        }
    }
    # Install folders, found even when they are not on the PATH (python.org, Anaconda, Miniconda), on any drive.
    $folders = @()
    if ($env:CONDA_PREFIX) { $folders += $env:CONDA_PREFIX }
    # conda on the PATH: its base folder is two levels up from condabin\conda.bat or Scripts\conda.exe.
    foreach ($cmd in @(Get-Command conda -All -ErrorAction SilentlyContinue)) {
        if ($cmd.Source) { $folders += (Split-Path -Parent (Split-Path -Parent $cmd.Source)) }
    }
    # Pythons registered with Windows (PEP 514: python.org, Anaconda and others write these, wherever installed).
    foreach ($hive in 'HKCU:\Software\Python', 'HKLM:\Software\Python', 'HKLM:\Software\WOW6432Node\Python') {
        foreach ($company in @(Get-ChildItem -Path $hive -ErrorAction SilentlyContinue)) {
            foreach ($tag in @(Get-ChildItem -Path $company.PSPath -ErrorAction SilentlyContinue)) {
                $ip = Get-ItemProperty -Path (Join-Path $tag.PSPath 'InstallPath') -ErrorAction SilentlyContinue
                if ($ip -and $ip.'(default)') { $folders += $ip.'(default)' }
            }
        }
    }
    # Every conda environment conda has made (base included) is listed in .conda\environments.txt.
    $envList = Join-Path $env:USERPROFILE '.conda\environments.txt'
    if (Test-Path -LiteralPath $envList) {
        foreach ($line in [System.IO.File]::ReadAllLines($envList)) { if ($line.Trim()) { $folders += $line.Trim() } }
    }
    foreach ($root in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:ProgramData, 'C:\')) {
        if (-not $root) { continue }
        foreach ($name in 'anaconda3', 'miniconda3', 'Anaconda3', 'Miniconda3', 'miniforge3', 'mambaforge') {
            $folders += (Join-Path $root $name)
        }
    }
    foreach ($pattern in @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*'),
                           (Join-Path $env:ProgramFiles 'Python3*'))) {
        $found = Get-ChildItem -Path $pattern -Directory -ErrorAction SilentlyContinue |
                 Sort-Object Name -Descending
        foreach ($dir in $found) { $folders += $dir.FullName }
    }
    $seen = @{}
    foreach ($dir in $folders) {
        $exe = Join-Path $dir 'python.exe'
        if ((Test-Path -LiteralPath $exe) -and -not $seen.ContainsKey($exe.ToLower())) {
            $seen[$exe.ToLower()] = $true
            [void]$candidates.Add(@{ Exe = $exe; Pre = @(); Label = $exe })
        }
    }
    foreach ($c in $candidates) {
        Write-Host "  Trying $($c.Label)..."
        $path = Test-Python -Exe $c.Exe -Pre $c.Pre
        if ($path) {
            if ($path.StartsWith($EnvDir, [System.StringComparison]::OrdinalIgnoreCase)) { continue }
            Write-Host "    usable: $path"
            return $path
        }
    }
    return $null
}

function Stop-NoPython {
    Write-Host ''
    Write-Host 'SHOBS-P could not find a Python it can use.'
    Write-Host 'It needs Python 3.11 or newer with Tkinter (the window toolkit).'
    Write-Host 'The simplest fix: install Python from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),'
    Write-Host 'then double-click "Create shortcuts.bat" again.'
    Write-Host 'If you use Anaconda, you can instead run this file from the Anaconda Prompt.'
    exit 2
}

# 1. The Python SHOBS-P used last time, if it still works.
$py = $null
if ((-not $UsePython) -and (Test-Path -LiteralPath $Record)) {
    $last = ([System.IO.File]::ReadAllText($Record)).Trim()
    if ($last -and (Test-Path -LiteralPath $last)) { $py = Test-Python -Exe $last -Quiet }
}
$isNew = -not $py

# 2. Otherwise find one, and give SHOBS-P its own private environment made from it.
if (-not $py) {
    Write-Host 'Looking for a Python that can run SHOBS-P...'
    $base = Find-BasePython
    if (-not $base) { Stop-NoPython }
    Write-Host ''
    Write-Host "Setting up SHOBS-P's own Python environment in $EnvDir"
    Write-Host '(your own Python installation is left untouched)...'
    if (Test-Path -LiteralPath $EnvDir) { Remove-Item -LiteralPath $EnvDir -Recurse -Force -ErrorAction SilentlyContinue }
    & $base -m venv $EnvDir 2>&1 | Out-Host
    if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $EnvPy)) { $py = Test-Python -Exe $EnvPy }
    if (-not $py) {
        Write-Host "  A separate environment does not work with this Python; SHOBS-P will use it directly:"
        Write-Host "  $base"
        if (Test-Path -LiteralPath $EnvDir) { Remove-Item -LiteralPath $EnvDir -Recurse -Force -ErrorAction SilentlyContinue }
        $py = $base
    }
}
Write-Host "Using Python: $py"

# 3. The packages (skipped for the self-test when everything was already in place).
if ($Action -ne 'selftest' -or $isNew -or -not (Test-Python -Exe $py -Packages -Quiet)) {
    Write-Host 'Checking the Python packages SHOBS-P needs (the first time this downloads them: a few minutes)...'
    & $py -m pip install --disable-pip-version-check -r $Requirements 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Installing the packages failed (see the lines above). Check the internet connection and try again.'
        exit 3
    }
    if (-not (Test-Python -Exe $py -Packages)) { exit 3 }
}
Write-Utf8 $Record $py

# 4. The job.
Set-Location -LiteralPath $AppDir
switch ($Action) {
    'shortcuts' {
        $pyw = Join-Path (Split-Path -Parent $py) 'pythonw.exe'
        if (-not (Test-Path -LiteralPath $pyw)) {
            Write-Host "No pythonw.exe next to $py, so the shortcuts cannot start SHOBS-P without a console window."
            exit 4
        }
        & (Join-Path $AppDir 'make_shortcuts.ps1') -AppDir $AppDir -PythonW $pyw
        exit 0
    }
    'run' {
        & $py (Join-Path $AppDir 'clearv_app.py')
        exit $LASTEXITCODE
    }
    'selftest' {
        Write-Host 'Running the SHOBS-P self-test. It takes a minute or two (the transit fits are the slow part)...'
        & $py (Join-Path $AppDir 'selftest.py')
        exit $LASTEXITCODE
    }
}
