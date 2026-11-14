@echo off
rem SHOBS-P self-test: checks this copy of SHOBS-P with the same Python the shortcuts use (Astropy, photutils, Tkinter).
rem It changes no data. The log goes to selftest_log.txt here and to Downloads\SHOBS-P_selftest_log.txt.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_python.ps1" -AppDir "%~dp0." -Action selftest
rem 2.3.5 (#83): setup_python.ps1 says how it went (passed / N checks failed / crashed / could not run).
if errorlevel 1 goto fail
pause
exit /b 0
:fail
echo.
echo Leave this window open and copy the message above.
pause
exit /b 1
