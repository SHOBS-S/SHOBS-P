@echo off
rem SHOBS-P self-test: checks this copy of SHOBS-P with your own Python (Astropy, photutils, Tkinter).
rem It changes no data. The log goes to selftest_log.txt here and to Downloads\SHOBS-P_selftest_log.txt.
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set PY=py
) else (
  set PY=python
)
echo Running the SHOBS-P self-test with %PY%. It takes a minute or two (the transit fits are the slow part)...
%PY% selftest.py
echo.
echo Done. The log is selftest_log.txt in this folder (a copy is in your Downloads folder).
pause
