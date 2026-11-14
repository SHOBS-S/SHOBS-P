# SHOBS-P: finds a Python that can run SHOBS-P, sets up SHOBS-P's own private environment, then does one job.
# Called by "Create shortcuts.bat" (-Action shortcuts), "run_windows.bat" (-Action run) and
# "Run self-test.bat" (-Action selftest).
#
# 2.2.18.1: the .bat files used to trust the Windows "py" launcher whenever it existed. On a PC where the launcher
# is installed but knows no Python (for example with Anaconda), that stopped everything. Now each candidate is run
# and tested (3.11 or newer, SSL, Tkinter), and the first one that works is used.
# 2.3.5 (#83): the same logic as shobs_setup.sh on a Mac or Linux: ready-made packages only (pip never compiles),
# every usable Python tried in turn (3.13, 3.12, 3.11 first, then newer, oldest first) until the packages install,
# and the real reason said when they do not (no internet, no ready-made copies for that Python yet, no pip).
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
$PipLog = Join-Path $Home2 'pip_log.txt'
# 2.3.5 (#83): the stop and done marks of the Mac/Linux script. Made from their code points: this file stays plain
# ASCII, because Windows PowerShell 5.1 reads a script without a byte-order mark in the ANSI code page.
$X = [string][char]0x2716
$OK = [string][char]0x2714

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
    $script:TPVer = ''
    foreach ($line in ($out -split "`r?`n")) {
        if ($line.StartsWith('SHOBSVER=')) { $script:TPVer = $line.Substring(9).Trim() }
    }
    if ($code -eq 0 -and $path -and (Test-Path -LiteralPath $path)) { return $path }
    if (-not $Quiet) {
        $last = ($out -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
        if (-not $last) { $last = "it did not start (exit code $code)" }
        Write-Host "    not usable: $last"
    }
    return $null
}

# 2.3.5 (#83): the order the usable Pythons are tried in (as python_rank in shobs_setup.sh): 3.13, 3.12, 3.11
# first (every SHOBS-P package has ready-made copies for them), then newer ones, oldest first. Smaller is sooner.
function Get-PythonRank([string]$Version) {
    $parts = $Version.Split('.')
    if ($parts.Count -lt 2) { return 999 }
    $mm = $parts[0] + '.' + $parts[1]
    if ($mm -eq '3.13') { return 0 }
    if ($mm -eq '3.12') { return 1 }
    if ($mm -eq '3.11') { return 2 }
    $minor = 0
    if ([int]::TryParse($parts[1], [ref]$minor)) { return $minor + 100 }
    return 999
}

# Every Python that can run SHOBS-P, in the order to try them: @(@{ Path; Ver }, ...). Says what it tries and why a
# Python is not usable.
function Find-UsablePythons {
    if ($UsePython) {
        Write-Host "  Trying only $UsePython (-UsePython)..."
        $path = Test-Python -Exe $UsePython
        if ($path) {
            Write-Host "    usable: Python $($script:TPVer)"
            return ,@(@{ Path = $path; Ver = $script:TPVer; Rank = 0; N = 0 })
        }
        return ,@()
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
    $found = New-Object System.Collections.ArrayList
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
        Write-Host "    usable: Python $($script:TPVer) ($path)"
        [void]$found.Add(@{ Path = $path; Ver = $script:TPVer; Rank = (Get-PythonRank $script:TPVer); N = $found.Count })
    }
    # By rank, then in the order found.
    $sorted = @($found | Sort-Object -Property @{ Expression = { $_.Rank } }, @{ Expression = { $_.N } })
    return ,$sorted
}

# 2.3.5 (#83): ready-made packages only (--only-binary=:all:), as on a Mac or Linux. pip's own words go to
# pip_log.txt. Returns $true when the packages installed and load; otherwise sets $script:PipWhy to 'network',
# 'nobuild', 'nopip' or 'other' and returns $false.
function Install-Packages([string]$Py, [bool]$AsUser, [bool]$Upgrade = $false) {
    $script:PipWhy = ''
    & $Py -m pip --version *> $null
    if ($LASTEXITCODE -ne 0) { $script:PipWhy = 'nopip'; return $false }
    Add-Content -LiteralPath $PipLog -Value "=== $(Get-Date) $Py" -Encoding UTF8 -ErrorAction SilentlyContinue
    # A newer pip knows more of the ready-made package names; a failure here is fine. Only when a new environment
    # is set up, and never in a Python whose packages belong to Anaconda (review S10).
    if ($Upgrade -and -not $AsUser) {
        & $Py -m pip install --disable-pip-version-check --quiet --upgrade pip 2>&1 | ForEach-Object { "$_" } |
            Add-Content -LiteralPath $PipLog -Encoding UTF8 -ErrorAction SilentlyContinue
    }
    $pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:', '--progress-bar', 'off')
    if ($AsUser) { $pipArgs += '--user' }
    $pipArgs += @('-r', $Requirements)
    # Progress on the screen: one line per package; everything (and any error) goes to the log.
    $lines = New-Object System.Collections.ArrayList
    & $Py @pipArgs 2>&1 | ForEach-Object {
        $line = "$_"
        [void]$lines.Add($line)
        if ($line -match '^Collecting ([A-Za-z0-9_.\-]+)') { Write-Host "    getting $($Matches[1])" }
    }
    $code = $LASTEXITCODE
    Add-Content -LiteralPath $PipLog -Value $lines -Encoding UTF8 -ErrorAction SilentlyContinue
    if ($code -eq 0 -and (Test-Python -Exe $Py -Packages -Quiet)) { return $true }
    $text = ($lines -join "`n")
    if ($code -eq 0) { $script:PipWhy = 'other' }
    elseif ($text -match '(?i)connection|timed out|name resolution|could not fetch|proxyerror|temporary failure|network is unreachable|sslerror') {
        $script:PipWhy = 'network'
    } elseif (-not (Test-PipReachesIndex $Py)) {
        # pip says "no matching distribution" also when it never reached the package index at all.
        $script:PipWhy = 'network'
    } elseif ($text -match '(?i)no matching distribution|could not find a version|resolutionimpossible|conflicting dependencies') {
        $script:PipWhy = 'nobuild'
    } else { $script:PipWhy = 'other' }
    return $false
}

# Can pip download anything at all? pip's own ready-made copy suits every Python, so when even that fails the
# trouble is the connection, not this Python.
function Test-PipReachesIndex([string]$Py) {
    $tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("shobs_pip_" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    & $Py -m pip download --disable-pip-version-check --quiet --no-deps --only-binary=:all: --dest $tmp pip 2>&1 |
        ForEach-Object { "$_" } | Add-Content -LiteralPath $PipLog -Encoding UTF8 -ErrorAction SilentlyContinue
    $ok = ($LASTEXITCODE -eq 0)
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    return $ok
}

# Says in plain words why the packages did not install with Python <version> ($script:PipWhy). Plain lines: the
# script goes on to the next Python itself; the closing cross mark says what to do when none works.
function Write-PipWhy([string]$Version) {
    switch ($script:PipWhy) {
        'nobuild' { Write-Host "    Python ${Version}: some packages SHOBS-P needs have no ready-made copy for it yet"
                    Write-Host '      (they would have to be compiled, and SHOBS-P never compiles).' }
        'network' { Write-Host "    $X The packages could not be downloaded. Check the internet connection and try again." }
        'nopip'   { Write-Host '    This Python has no pip, so SHOBS-P cannot install its packages with it.' }
        default   { Write-Host "    The packages did not install on Python $Version (pip's own words are in $PipLog)." }
    }
}

function Stop-NoPython {
    Write-Host ''
    Write-Host "$X SHOBS-P could not find a Python it can use (each one tried is listed above, with the reason)."
    Write-Host '  It needs Python 3.11 or newer with Tkinter (the window toolkit).'
    Write-Host '  The simplest fix: install Python 3.13 from https://www.python.org/downloads/windows/'
    Write-Host '  (tick "Add python.exe to PATH"), then double-click "Create shortcuts.bat" again.'
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
    if ($last -and (Test-Path -LiteralPath $last)) {
        $py = Test-Python -Exe $last -Quiet
        if (-not $py) { Write-Host "The Python SHOBS-P used before ($last) no longer works; looking for another one." }
    }
}
$isNew = -not $py
# Reusing an Anaconda Python directly (no environment of its own): its pip packages go to the user's folder.
$pipUser = [bool]($py -and -not $py.StartsWith($EnvDir, [System.StringComparison]::OrdinalIgnoreCase) -and
                  (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $py) 'conda-meta')))
if ($py -and ($Action -ne 'selftest' -or -not (Test-Python -Exe $py -Packages -Quiet))) {
    Write-Host 'Checking the Python packages SHOBS-P needs...'
    if (-not (Install-Packages $py $pipUser)) {
        if ($script:PipWhy -eq 'network') { Write-PipWhy ''; exit 3 }
        # e.g. a new SHOBS-P needs a package that has no ready-made copy for this Python: set up afresh, which
        # tries the other Pythons too.
        Write-Host "  The packages could not be updated with $py; setting SHOBS-P up afresh."
        $py = $null
        $isNew = $true
    }
}

# 2. Otherwise find one, and give SHOBS-P its own private environment made from it. Every usable Python is tried in
# turn (3.13, 3.12, 3.11, then newer) until the packages install.
if (-not $py) {
    Write-Host 'Looking for a Python that can run SHOBS-P...'
    $usable = Find-UsablePythons
    if ($usable.Count -eq 0) { Stop-NoPython }
    Set-Content -LiteralPath $PipLog -Value '' -Encoding UTF8 -ErrorAction SilentlyContinue
    $noBuild = @()
    $baseVer = ''
    for ($i = 0; $i -lt $usable.Count; $i++) {
        $base = $usable[$i].Path
        $baseVer = $usable[$i].Ver
        $pipUser = $false
        Write-Host ''
        Write-Host "Setting up SHOBS-P's own Python environment with Python $baseVer ($base)"
        Write-Host "in $EnvDir"
        Write-Host '(that Python itself is left untouched). The first time this downloads the packages: a few minutes...'
        if (-not (Remove-EnvDir)) {
            Write-Host "$X The old SHOBS-P environment in $EnvDir could not be removed."
            Write-Host '  Close SHOBS-P if it is running, then try again.'
            exit 5
        }
        $cand = $null
        $code = Invoke-Shown $base @('-m', 'venv', $EnvDir)
        if ($code -eq 0 -and (Test-Path -LiteralPath $EnvPy)) { $cand = Test-Python -Exe $EnvPy }
        if (-not $cand) {
            [void](Remove-EnvDir)
            $cand = $base
            # Anaconda's own environment is managed by conda: keep pip's packages in the user's folder instead.
            $pipUser = Test-Path -LiteralPath (Join-Path (Split-Path -Parent $base) 'conda-meta')
            Write-Host '  A separate environment does not work with this Python, so SHOBS-P will use it directly'
            if ($pipUser) { Write-Host "  (its packages go to your user folder, not into Anaconda's own environment)." }
            else { Write-Host '  (its packages are installed into that Python with pip).' }
        }
        Write-Host 'Installing the Python packages SHOBS-P needs...'
        if (Install-Packages $cand $pipUser $true) { $py = $cand; break }
        Write-PipWhy $baseVer
        if ($script:PipWhy -eq 'network') { [void](Remove-EnvDir); exit 3 }
        if ($script:PipWhy -eq 'nobuild') { $noBuild += $baseVer }
        if ($i -lt $usable.Count - 1) { Write-Host '      Trying the next Python.' }
        if ($cand.StartsWith($EnvDir, [System.StringComparison]::OrdinalIgnoreCase)) { [void](Remove-EnvDir) }
    }
    if (-not $py) {
        Write-Host ''
        Write-Host "$X SHOBS-P could not set up its packages with any Python on this computer (each one is listed above)."
        if ($noBuild.Count) { Write-Host "  Python $($noBuild -join ', ') has no ready-made copies of some packages yet." }
        Write-Host '  Install Python 3.13 from https://www.python.org/downloads/windows/ (tick "Add python.exe to PATH"),'
        Write-Host '  then double-click "Create shortcuts.bat" again. Your other Pythons can stay.'
        Write-Host "  (pip's own words are in $PipLog)"
        exit 3
    }
    if (-not (Test-Python -Exe $py -Packages)) {
        Write-Host "$X The packages were installed but do not all load (see the line above)."
        Write-Host "  Delete the folder $Home2 and double-click ""Create shortcuts.bat"" again."
        exit 3
    }
    Write-Host "$OK SHOBS-P is set up with Python $baseVer."
}
Write-Host "Using Python: $py"

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
        $code = $LASTEXITCODE
        # 2.3.5 (#83): say how it went (selftest.py exits 0 passed, 10 checks failed, 11 crashed).
        $where = 'selftest_log.txt in this folder (a copy is in your Downloads folder)'
        Write-Host ''
        if ($code -eq 0) { Write-Host "$OK Self-test passed. The log is $where." }
        elseif ($code -eq 10) {
            $n = ''
            $logFile = Join-Path $AppDir 'selftest_log.txt'
            if (Test-Path -LiteralPath $logFile) {
                $r = Select-String -LiteralPath $logFile -Pattern '^RESULT: (\d+)' | Select-Object -First 1
                if ($r) { $n = $r.Matches[0].Groups[1].Value }
            }
            if (-not $n) { $n = 'some' }
            $word = 'checks'
            if ($n -eq '1') { $word = 'check' }
            Write-Host "$X Self-test: $n $word failed (the RESULT line above names them)."
            Write-Host "  Send $where."
        } elseif ($code -eq 11) {
            Write-Host "$X The self-test itself crashed (see CRASHED above)."
            Write-Host "  Send $where."
        } else { Write-Host "$X The self-test could not run (exit code $code, see the lines above)." }
        exit $code
    }
}
