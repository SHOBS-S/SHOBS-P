@echo off
rem SHOBS-P self-test: checks this copy of SHOBS-P with the same Python the shortcuts use (Astropy, photutils, Tkinter).
rem It changes no data. The log goes to selftest_log.txt here and to Downloads\SHOBS-P_selftest_log.txt.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_python.ps1" -AppDir "%~dp0." -Action selftest
if errorlevel 1 goto fail
echo.
echo Done. The log is selftest_log.txt in this folder (a copy is in your Downloads folder).
pause
exit /b 0
:fail
echo.
echo The self-test did not pass or could not run. Leave this window open and copy the message above.
echo If the test ran, its log is selftest_log.txt in this folder.
pause
exit /b 1
