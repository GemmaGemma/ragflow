@echo off
setlocal

for %%I in ("%~dp0.") do set "SCRIPT_DIR=%%~fI"
set "PY_SCRIPT=%SCRIPT_DIR%\parse_ragflow_log.py"

if not exist "%PY_SCRIPT%" (
  echo [ERROR] Missing script: "%PY_SCRIPT%"
  exit /b 1
)

set "PYTHON_CMD=python"
where python >nul 2>nul
if errorlevel 1 (
  set "PYTHON_CMD=py -3"
  where py >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Neither python nor py was found in PATH.
    exit /b 1
  )
)

if "%~1"=="" (
  echo [INFO] Processing all ragflow_server*.log files under:
  echo        "%SCRIPT_DIR%"
  call %PYTHON_CMD% "%PY_SCRIPT%" --log-dir "%SCRIPT_DIR%"
  exit /b %errorlevel%
)

if exist "%~1\" (
  echo [INFO] Processing all ragflow_server*.log files under:
  echo        "%~1"
  call %PYTHON_CMD% "%PY_SCRIPT%" --log-dir "%~1" %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

if exist "%~1" (
  echo [INFO] Processing single log:
  echo        "%~1"
  call %PYTHON_CMD% "%PY_SCRIPT%" --log "%~1" %2 %3 %4 %5 %6 %7 %8 %9
  exit /b %errorlevel%
)

echo [ERROR] Target not found: "%~1"
echo Usage:
echo   %~nx0
echo   %~nx0 ^<log_dir^>
echo   %~nx0 ^<log_file^>
exit /b 1
