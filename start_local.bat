@echo off
setlocal
pushd "%~dp0"
if errorlevel 1 goto failed

if not exist ".venv\Scripts\python.exe" goto create_venv
goto verify_install

:create_venv
py -3.11 -m venv ".venv"
if errorlevel 1 goto failed

:verify_install
".venv\Scripts\python.exe" -c "import importlib.metadata as m; d=m.distribution('detect-app'); assert any(e.name == 'detect-app' for e in d.entry_points)" >nul 2>&1
if errorlevel 1 goto install
".venv\Scripts\python.exe" -m pip check >nul 2>&1
if errorlevel 1 goto install
goto launch

:install
echo Installation Python incomplete : reprise de l’installation de detect-app.
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto failed

:launch
".venv\Scripts\python.exe" -m detect_app
set "EXIT_CODE=%ERRORLEVEL%"
goto finish

:failed
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" set "EXIT_CODE=1"
echo Le lancement de detect_app a échoué ^(code %EXIT_CODE%^).

:finish
popd
endlocal & exit /b %EXIT_CODE%
