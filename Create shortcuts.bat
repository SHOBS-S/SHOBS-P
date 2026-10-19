@echo off
rem Puts a SHOBS-P icon on the Desktop and in the Start menu. Run it once (again after moving the folder).
rem It finds a Python that can run SHOBS-P (any kind: python.org, Anaconda, ...), gives SHOBS-P its own
rem environment in your user folder, installs the packages there, and makes the shortcuts. See setup_python.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_python.ps1" -AppDir "%~dp0." -Action shortcuts
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
