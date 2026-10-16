@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%.." >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Cannot resolve repository root from %SCRIPT_DIR%..
    exit /b 1
)

set "REPO_ROOT=%CD%"
set "DRY_RUN=0"
set "DOCKER=0"
set "HEALTH_RETRIES=20"
set "HEALTH_WAIT=3"

:ARGS
if "%~1"=="" goto ARGS_DONE
if /I "%~1"=="--dry-run" (set "DRY_RUN=1") else if /I "%~1"=="/dry-run" (set "DRY_RUN=1") else if /I "%~1"=="--docker" (set "DOCKER=1") else if /I "%~1"=="--help" (goto HELP) else if /I "%~1"=="/?" (goto HELP) else (
    echo [ERROR] Unknown option: %~1
    echo.
    goto HELP
)
shift
goto ARGS

:ARGS_DONE
rem ponytail: containers re-run pip install on every start, so Docker mode waits up to 20 minutes; prebuild an image to shorten
if "%DOCKER%"=="1" (
    set "HEALTH_RETRIES=120"
    set "HEALTH_WAIT=10"
)

echo ============================================================
echo  Stock Linbot - Local Service Restart
echo  Repo: %REPO_ROOT%
echo  Time: %date% %time%
echo ============================================================
echo.

if "%DRY_RUN%"=="1" (
    echo [DRY-RUN] No processes will be stopped and services will not be started.
    echo.
)

echo [1/3] Stopping matching local Python service processes...
set "RESTART_DRY_RUN=%DRY_RUN%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $root = (Resolve-Path $env:REPO_ROOT).Path.ToLowerInvariant(); $dryRun = $env:RESTART_DRY_RUN -eq '1'; $pattern = 'services\\mcp\\server\.py|(^|[\\\s\""])app\.py([\""\s]|$)'; $targets = Get-CimInstance Win32_Process | Where-Object { $_.Name -in @('python.exe', 'pythonw.exe') -and $_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($root) -and (($_.CommandLine -replace '/', '\') -match $pattern) }; if (-not $targets) { Write-Host 'No matching local Stock Linbot Python service processes found.'; exit 0 }; foreach ($p in $targets) { $line = ($p.CommandLine -replace '\s+', ' '); if ($dryRun) { Write-Host ('DRY-RUN would stop PID {0}: {1}' -f $p.ProcessId, $line) } else { Write-Host ('Stopping PID {0}: {1}' -f $p.ProcessId, $line); try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch { Write-Host ('PID {0} already exited; continuing.' -f $p.ProcessId) } } }"
if errorlevel 1 (
    echo [ERROR] Failed while stopping local service processes.
    popd >nul
    exit /b 1
)

if "%DRY_RUN%"=="1" (
    echo.
    if "%DOCKER%"=="1" (
        echo [2/3] DRY-RUN would run: docker compose restart twse_mcp_server stock_bot
    ) else (
        echo [2/3] DRY-RUN would call execution\start_web.bat
    )
    echo [3/3] DRY-RUN would check http://127.0.0.1:1688/health
    echo.
    echo Dry run complete.
    popd >nul
    exit /b 0
)

echo.
if "%DOCKER%"=="1" (
    echo [2/3] Restarting Docker Compose services: twse_mcp_server stock_bot
    docker compose restart twse_mcp_server stock_bot
    if errorlevel 1 (
        echo [ERROR] docker compose restart failed.
        popd >nul
        exit /b 1
    )
    echo Containers re-run pip install on start; this can take 10+ minutes on a slow network.
) else (
    echo [2/3] Starting local MCP and Web/LINE services...
    call "%SCRIPT_DIR%start_web.bat"
    if errorlevel 1 (
        echo [ERROR] execution\start_web.bat failed.
        popd >nul
        exit /b 1
    )
)

echo.
echo [3/3] Waiting for web health endpoint...
for /L %%I in (1,1,%HEALTH_RETRIES%) do (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:1688/health' -TimeoutSec 3; if ($r.StatusCode -eq 200) { exit 0 } } catch { }; exit 1"
    if not errorlevel 1 (
        echo Health check passed: http://127.0.0.1:1688/health
        echo.
        echo Restart complete.
        popd >nul
        exit /b 0
    )
    echo Health check not ready yet, retry %%I/%HEALTH_RETRIES%...
    timeout /t %HEALTH_WAIT% /nobreak >nul
)

echo [ERROR] Health check did not pass after retries.
if "%DOCKER%"=="1" echo Check container logs: docker logs stock_ai_stock_bot --tail 30
echo Check service windows and run:
echo   powershell -NoProfile -Command "Invoke-WebRequest -UseBasicParsing http://127.0.0.1:1688/health"
popd >nul
exit /b 1

:HELP
echo Usage:
echo   execution\restart_services.bat
echo   execution\restart_services.bat --dry-run
echo   execution\restart_services.bat --docker [--dry-run]
echo.
echo Restarts the local Windows Stock Linbot service processes started by execution\start_web.bat.
echo It only targets python.exe/pythonw.exe command lines in this repository that run:
echo   services\mcp\server.py
echo   app.py
echo.
echo --docker stops those local processes, then restarts only the Docker Compose
echo services twse_mcp_server and stock_bot ^(the db service is not touched^).
echo Health wait is extended to 20 minutes because containers install dependencies on start.
popd >nul 2>&1
exit /b 0
