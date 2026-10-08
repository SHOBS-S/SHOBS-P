@echo off
rem Puts a SHOBS-P icon on the Desktop and in the Start menu. Run it once (again after moving the folder).
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set PY=py
) else (
  set PY=python
)
echo Checking the Python packages SHOBS-P needs...
%PY% -m pip install --quiet -r requirements.txt
if errorlevel 1 goto fail
%PY% -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))" > "%TEMP%\shobs_pythonw.txt"
set /p PYW=<"%TEMP%\shobs_pythonw.txt"
del "%TEMP%\shobs_pythonw.txt" >nul 2>&1
if not exist "%PYW%" goto fail
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcuts.ps1" -AppDir "%~dp0." -PythonW "%PYW%"
if errorlevel 1 goto fail
echo.
echo Done. Start SHOBS-P from the Desktop icon or the Start menu.
echo To pin it to the taskbar: Start menu, right-click SHOBS-P, Pin to taskbar.
pause
exit /b 0
:fail
echo.
echo The shortcuts could not be made. Leave this window open and copy the message above.
pause
exit /b 1
