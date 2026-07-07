@echo off
REM MLB Edge Analyzer - analisis diario del slate (main.py)
REM Pensado para correr desde Windows Task Scheduler, pero funciona igual
REM ejecutado a mano con doble clic o desde una terminal cualquiera.

setlocal

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

if not exist "%PROJECT_DIR%logs" mkdir "%PROJECT_DIR%logs"

for /f %%D in ('powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd')"') do set "TODAY=%%D"
set "LOGFILE=%PROJECT_DIR%logs\scheduled_run_daily_analysis_%TODAY%.log"

echo ==================================================================== >> "%LOGFILE%"
echo Run started: %DATE% %TIME% >> "%LOGFILE%"
echo Working dir: %CD% >> "%LOGFILE%"
echo ==================================================================== >> "%LOGFILE%"

REM ODDS_API_KEY vive en la variable de entorno de usuario de Windows
REM (nunca hardcodeada aqui). La leemos explicitamente por si el Task
REM Scheduler arranca la tarea con un entorno que no la trae heredada.
for /f "delims=" %%K in ('powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('ODDS_API_KEY','User')"') do set "ODDS_API_KEY=%%K"

call "%PROJECT_DIR%venv\Scripts\activate.bat"

python "%PROJECT_DIR%main.py" >> "%LOGFILE%" 2>&1
set "EXITCODE=%ERRORLEVEL%"

echo Run finished with exit code %EXITCODE%: %DATE% %TIME% >> "%LOGFILE%"
echo. >> "%LOGFILE%"

endlocal & exit /b %EXITCODE%
