@echo off
rem Job-search dashboard. Double-click this file, or run start-jobhunt.bat in a terminal.
rem The first run installs the Python packages and Chromium (a few minutes); later runs start in seconds.
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
if exist .venv\jobhunt-ready goto run
echo First run: installing Python packages and Chromium. This takes a few minutes.
where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
if not exist %PY% goto nopython
%PY% -m pip install -r requirements-jobhunt.txt || goto fail
%PY% -m playwright install chromium || goto fail
echo ok> .venv\jobhunt-ready
:run
if not exist .env echo Note: there is no .env file here. Copy .env.example to .env and fill in the jobhunt section.
%PY% -m jobhunt serve %*
goto end
:nopython
echo Python 3.12 or newer is needed. Install it from python.org (tick "Add python.exe to PATH"), then run this again.
goto end
:fail
echo Setup failed: see the error above. Fix it, delete the .venv folder, and run this again.
:end
pause
endlocal
