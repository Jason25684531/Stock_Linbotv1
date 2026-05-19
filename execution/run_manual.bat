@echo off
REM Compatibility-only manual wrapper.
REM Official daily scheduler path: jobs\scheduler.py
REM Do not remove until cleanup evidence passes.
chcp 65001 >nul
cd /d D:\01_Project\Stocke\Stock_Linbotv1
set PYTHON=D:\01_Project\Stocke\Stock_Linbotv1\myenv\Scripts\python.exe
set SCHEDULER=jobs\scheduler.py

:MENU
cls
echo ============================================================
echo   Stock Linbot - Manual Run Menu
echo   %date% %time%
echo ============================================================
echo.
echo   [1] Morning Push   - AI News Summary + Stock Pick
echo   [2] Evening Full   - Update DB + Select + Push
echo   [3] Evening Push   - Push Only (skip DB update)
echo   [4] Update DB      - Fetch latest stock data
echo   [5] Run Strategy   - Select stocks (no push)
echo   [6] Start Web      - Launch Flask + Line Bot
echo   [0] Exit
echo.
set /p CHOICE=  Choose (0-6):

if "%CHOICE%"=="1" goto MORNING
if "%CHOICE%"=="2" goto EVENING_FULL
if "%CHOICE%"=="3" goto EVENING_PUSH_ONLY
if "%CHOICE%"=="4" goto UPDATE_DB
if "%CHOICE%"=="5" goto RUN_DAILY
if "%CHOICE%"=="6" goto START_WEB
if "%CHOICE%"=="0" goto END
echo Invalid choice, try again.
pause
goto MENU

:MORNING
echo.
echo [%time%] === Morning Push ===
%PYTHON% -X utf8 %SCHEDULER% morning
if %errorlevel% neq 0 (
    echo [%time%] FAILED, errorlevel: %errorlevel%
) else (
    echo [%time%] DONE
)
goto DONE

:EVENING_FULL
echo.
echo [%time%] === Evening Full Pipeline ===
%PYTHON% -X utf8 %SCHEDULER% evening --stop-on-error
if %errorlevel% neq 0 (
    echo [%time%] FAILED, errorlevel: %errorlevel%
) else (
    echo [%time%] All steps DONE
)
goto DONE

:EVENING_PUSH_ONLY
echo.
echo [%time%] === Evening Push Only ===
%PYTHON% -X utf8 %SCHEDULER% push_evening
if %errorlevel% neq 0 (
    echo [%time%] FAILED, errorlevel: %errorlevel%
) else (
    echo [%time%] DONE
)
goto DONE

:UPDATE_DB
echo.
echo [%time%] === Update Database ===
%PYTHON% -X utf8 %SCHEDULER% update_database
if %errorlevel% neq 0 (
    echo [%time%] FAILED, errorlevel: %errorlevel%
) else (
    echo [%time%] DONE
)
goto DONE

:RUN_DAILY
echo.
echo [%time%] === Run Strategy ===
%PYTHON% -X utf8 %SCHEDULER% run_daily
if %errorlevel% neq 0 (
    echo [%time%] FAILED, errorlevel: %errorlevel%
) else (
    echo [%time%] DONE
)
goto DONE

:START_WEB
echo.
echo [%time%] === Start Web Server (background) ===
call execution\start_web.bat
goto DONE

:DONE
echo.
echo ============================================================
echo  Finished: %date% %time%
echo ============================================================
echo.
set /p BACK=  Press Enter to return to menu, or type q to quit:
if /i "%BACK%"=="q" goto END
goto MENU

:END
echo Bye!
