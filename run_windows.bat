@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set PY=py
) else (
  set PY=python
)
echo Using %PY%
%PY% -m pip install -r requirements.txt
if errorlevel 1 goto fail
%PY% clearv_app.py
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
