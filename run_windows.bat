@echo off
rem Starts SHOBS-P with this console window beside it (useful when the program will not start from its icon).
rem Uses the same Python as the shortcuts, and checks the packages first. See setup_python.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_python.ps1" -AppDir "%~dp0." -Action run
if errorlevel 1 goto fail
echo.
echo App closed.
pause
exit /b 0
:fail
echo.
echo The app did not start. Leave this window open and copy the message above.
pause
exit /b 1
