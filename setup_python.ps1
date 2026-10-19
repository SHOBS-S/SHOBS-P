# SHOBS-P: finds a Python that can run SHOBS-P, sets up SHOBS-P's own private environment, then does one job.
# Called by "Create shortcuts.bat" (-Action shortcuts), "run_windows.bat" (-Action run) and
# "Run self-test.bat" (-Action selftest).
#
# 2.2.18.1: the .bat files used to trust the Windows "py" launcher whenever it existed. On a PC where the launcher
# is installed but knows no Python (for example with Anaconda), that stopped everything. Now each candidate is run
# and tested (3.11 or newer, SSL, Tkinter), and the first one that works is used.
#
# For testing: -UsePython <path to python.exe> tries only that Python, and -ProfileName keeps the test apart from the
# real setup (its own folder in %LOCALAPPDATA%; shortcuts are not made), e.g.
#   powershell -ExecutionPolicy Bypass -File setup_python.ps1 -Action selftest -UsePython C:\...\python.exe -ProfileName SHOBS-P-test
param(
    [string]$AppDir = $PSScriptRoot,
    [ValidateSet('shortcuts', 'run', 'selftest')][string]$Action = 'shortcuts',
    [string]$UsePython = '',
    [string]$ProfileName = 'SHOBS-P'
)
$ErrorActionPreference = 'Continue'
$AppDir = (Resolve-Path -LiteralPath $AppDir).Path
$Check = Join-Path $AppDir 'shobs_check_python.py'
$Requirements = Join-Path $AppDir 'requirements.txt'

if ($Action -eq 'shortcuts' -and $ProfileName -ne 'SHOBS-P') {
    Write-Host 'Shortcuts are only made for the normal setup (without -ProfileName), so a test cannot replace them.'
    exit 6
}

# Python writes UTF-8 and PowerShell reads UTF-8, so paths with accented letters survive.
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
# A leftover PYTHONHOME/PYTHONPATH from an old install stops every Python from starting.
Remove-Item Env:PYTHONHOME, Env:PYTHONPATH -ErrorAction SilentlyContinue
# The newer Windows Python install manager may otherwise download a Python when "py -3" finds none.
$env:PYTHON_MANAGER_AUTOMATIC_INSTALL = 'false'
# Started from an Anaconda Prompt, the PATH carries conda's activation folders, which the Desktop shortcut will not
# have. Drop them, so a Python only passes the test if it also works when started from the shortcut.
if ($env:CONDA_PREFIX) {
    $env:PATH = ($env:PATH -split ';' | Where-Object {
        $_ -and -not $_.StartsWith($env:CONDA_PREFIX, [System.StringComparison]::OrdinalIgnoreCase) }) -join ';'
}

# SHOBS-P's own place in the user's folder, shared by every SHOBS-P version.
$Home2 = Join-Path $env:LOCALAPPDATA $ProfileName
New-Item -ItemType Directory -Force -Path $Home2 | Out-Null
$EnvDir = Join-Path $Home2 'python-env'
$EnvPy = Join-Path $EnvDir 'Scripts\python.exe'
$Record = Join-Path $Home2 'python.txt'

function Write-Utf8([string]$Path, [string]$Text) {
    [System.IO.File]::WriteAllText($Path, $Text, (New-Object System.Text.UTF8Encoding $false))
}

# Runs a program and shows its output as plain lines (Windows PowerShell 5.1 would print stderr lines as red errors).
function Invoke-Shown([string]$Exe, [string[]]$ArgList) {
    & $Exe @ArgList 2>&1 | ForEach-Object { "$_" } | Out-Host
    return $LASTEXITCODE
}

# Runs the checker with one candidate. Returns the full path of that python.exe, or $null (and says why).
function Test-Python([string]$Exe, [string[]]$Pre = @(), [switch]$Packages, [switch]$Quiet) {
    $argv = @() + $Pre + @($Check)
    if ($Packages) { $argv += '--packages' }
    try {
        $out = (& $Exe @argv 2>&1 | ForEach-Object { "$_" }) -join "`n"
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
        if (-not $last) { $last = "it did not start (exit code $code)" }
        Write-Host "    not usable: $last"
    }
    return $null
}

function Find-BasePython {
    if ($UsePython) {
        Write-Host "  Trying only $UsePython (-UsePython)..."
        $path = Test-Python -Exe $UsePython
        if ($path) { Write-Host "    usable: $path" }
        return $path
    }
    $candidates = New-Object System.Collections.ArrayList
    $seen = @{}
    function Add-Candidate([string]$Exe, [string[]]$Pre, [string]$Label) {
        $key = ($Exe + ' ' + ($Pre -join ' ')).ToLower()
        if ($Exe -and -not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            [void]$candidates.Add(@{ Exe = $Exe; Pre = $Pre; Label = $Label })
        }
    }
    # Names on the PATH. The py launcher first (python.org's usual way), then plain python and python3.
    $pyCmd = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($pyCmd) { Add-Candidate $pyCmd.Path @('-3') 'py -3 (Windows Python launcher)' }
    foreach ($name in 'python', 'python3') {
        # Every match on the PATH, skipping the Microsoft Store placeholder (WindowsApps\python.exe), which only
        # offers to install Python and would otherwise hide a real Python further down the PATH. (A real Store
        # Python is found through the registry below.)
        $cmds = Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue |
                Where-Object { $_.Path -notmatch '\\WindowsApps\\' }
        foreach ($cmd in $cmds) { Add-Candidate $cmd.Path @() "$name ($($cmd.Path))" }
    }
    # Install folders, found even when they are not on the PATH (python.org, Anaconda, Miniconda), on any drive.
    $exes = @()
    if ($env:CONDA_PREFIX) { $exes += (Join-Path $env:CONDA_PREFIX 'python.exe') }
    # conda on the PATH: its base folder is two levels up from condabin\conda.bat or Scripts\conda.exe.
    foreach ($cmd in @(Get-Command conda -All -ErrorAction SilentlyContinue)) {
        if ($cmd.Path) { $exes += (Join-Path (Split-Path -Parent (Split-Path -Parent $cmd.Path)) 'python.exe') }
    }
    # Pythons registered with Windows (PEP 514: python.org, Anaconda and others write these, wherever installed).
    foreach ($hive in 'HKCU:\Software\Python', 'HKLM:\Software\Python', 'HKLM:\Software\WOW6432Node\Python') {
        foreach ($company in @(Get-ChildItem -Path $hive -ErrorAction SilentlyContinue)) {
            foreach ($tag in @(Get-ChildItem -Path $company.PSPath -ErrorAction SilentlyContinue)) {
                $ip = Get-ItemProperty -Path (Join-Path $tag.PSPath 'InstallPath') -ErrorAction SilentlyContinue
                if (-not $ip) { continue }
                if ($ip.ExecutablePath) { $exes += $ip.ExecutablePath }
                elseif ($ip.'(default)') { $exes += (Join-Path $ip.'(default)' 'python.exe') }
            }
        }
    }
    # Every conda environment (base included) is listed in .conda\environments.txt; only those with Tkinter.
    $envList = Join-Path $env:USERPROFILE '.conda\environments.txt'
    if (Test-Path -LiteralPath $envList) {
        foreach ($line in [System.IO.File]::ReadAllLines($envList)) {
            $dir = $line.Trim()
            if ($dir -and (Test-Path -LiteralPath (Join-Path $dir 'Lib\tkinter'))) { $exes += (Join-Path $dir 'python.exe') }
        }
    }
    foreach ($root in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:ProgramData, 'C:\')) {
        if (-not $root) { continue }
        foreach ($name in 'anaconda3', 'miniconda3', 'miniforge3', 'mambaforge') {
            $exes += (Join-Path (Join-Path $root $name) 'python.exe')
        }
    }
    foreach ($pattern in @((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python3*'),
                           (Join-Path $env:ProgramFiles 'Python3*'))) {
        foreach ($dir in @(Get-ChildItem -Path $pattern -Directory -ErrorAction SilentlyContinue)) {
            $exes += (Join-Path $dir.FullName 'python.exe')
        }
    }
    foreach ($exe in $exes) {
        if ($exe -and (Test-Path -LiteralPath $exe)) { Add-Candidate $exe @() $exe }
    }
    $tried = @{}
    foreach ($c in $candidates) {
        Write-Host "  Trying $($c.Label)..."
        $path = Test-Python -Exe $c.Exe -Pre $c.Pre
        if (-not $path) { continue }
        if ($tried.ContainsKey($path.ToLower())) { Write-Host "    (same Python as above)"; continue }
        $tried[$path.ToLower()] = $true
        if ($path.StartsWith($EnvDir, [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-Host "    skipped (SHOBS-P's own environment)"
            continue
        }
        Write-Host "    usable: $path"
        return $path
    }
    return $null
}

function Stop-NoPython {
    Write-Host ''
    Write-Host 'SHOBS-P could not find a Python it can use (each one tried is listed above, with the reason).'
    Write-Host 'It needs Python 3.11 or newer with Tkinter (the window toolkit).'
    Write-Host 'The simplest fix: install Python from https://www.python.org/downloads/ (tick "Add python.exe to PATH"),'
    Write-Host 'then double-click "Create shortcuts.bat" again.'
    exit 2
}

# Removes SHOBS-P's environment folder (rd copes with big folders better than Windows PowerShell 5.1).
function Remove-EnvDir {
    if (-not (Test-Path -LiteralPath $EnvDir)) { return $true }
    cmd /c rd /s /q "`"$EnvDir`"" 2>&1 | Out-Null
    if (Test-Path -LiteralPath $EnvDir) { Remove-Item -LiteralPath $EnvDir -Recurse -Force -ErrorAction SilentlyContinue }
    return -not (Test-Path -LiteralPath $EnvDir)
}

# 1. The Python SHOBS-P used last time, if it still works.
$py = $null
if ((-not $UsePython) -and (Test-Path -LiteralPath $Record)) {
    $last = ([System.IO.File]::ReadAllText($Record)).Trim()
    if ($last -and (Test-Path -LiteralPath $last)) { $py = Test-Python -Exe $last -Quiet }
}
$isNew = -not $py
$pipUser = $false

# 2. Otherwise find one, and give SHOBS-P its own private environment made from it.
if (-not $py) {
    Write-Host 'Looking for a Python that can run SHOBS-P...'
    $base = Find-BasePython
    if (-not $base) { Stop-NoPython }
    Write-Host ''
    Write-Host "Setting up SHOBS-P's own Python environment in $EnvDir"
    Write-Host '(made from that Python, which itself is left untouched). This takes a moment...'
    if (-not (Remove-EnvDir)) {
        Write-Host "The old SHOBS-P environment in $EnvDir could not be removed."
        Write-Host 'Close SHOBS-P if it is running, then try again.'
        exit 5
    }
    $code = Invoke-Shown $base @('-m', 'venv', $EnvDir)
    if ($code -eq 0 -and (Test-Path -LiteralPath $EnvPy)) { $py = Test-Python -Exe $EnvPy }
    if (-not $py) {
        [void](Remove-EnvDir)
        $py = $base
        # Anaconda's own environment is managed by conda: keep pip's packages in the user's folder instead.
        $pipUser = Test-Path -LiteralPath (Join-Path (Split-Path -Parent $base) 'conda-meta')
        Write-Host '  A separate environment does not work with this Python, so SHOBS-P will use it directly:'
        Write-Host "  $base"
        if ($pipUser) { Write-Host "  (its packages go to your user folder, not into Anaconda's own environment)" }
        else { Write-Host '  (its packages are installed into that Python with pip)' }
    }
}
Write-Host "Using Python: $py"

# 3. The packages (skipped for the self-test when everything was already in place).
if ($Action -ne 'selftest' -or $isNew -or -not (Test-Python -Exe $py -Packages -Quiet)) {
    Write-Host 'Checking the Python packages SHOBS-P needs (the first time this downloads them: a few minutes)...'
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check')
    if ($pipUser) { $pipArgs += '--user' }
    $pipArgs += @('-r', $Requirements)
    $code = Invoke-Shown $py $pipArgs
    if ($code -ne 0) {
        Write-Host 'Installing the packages failed (see the lines above). Check the internet connection and try again.'
        exit 3
    }
    if (-not (Test-Python -Exe $py -Packages)) {
        Write-Host 'The packages were installed but do not all load (see the line above).'
        exit 3
    }
}
Write-Utf8 $Record $py

# 4. The job.
Set-Location -LiteralPath $AppDir
switch ($Action) {
    'shortcuts' {
        $pyw = Join-Path (Split-Path -Parent $py) 'pythonw.exe'
        if (-not (Test-Path -LiteralPath $pyw)) {
            Write-Host "No pythonw.exe next to $py, so the shortcuts cannot start SHOBS-P without a console window."
            Write-Host 'Use run_windows.bat to start SHOBS-P instead.'
            exit 4
        }
        & (Join-Path $AppDir 'make_shortcuts.ps1') -AppDir $AppDir -PythonW $pyw
        $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'SHOBS-P.lnk'
        if (-not (Test-Path -LiteralPath $lnk)) {
            Write-Host "The Desktop shortcut $lnk was not made (see the lines above)."
            exit 4
        }
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
