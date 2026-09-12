@echo off
rem ===========================================================================
rem  ShadowScribe -- double-clickable launcher for setup-client.ps1
rem
rem  Why this exists: Windows does not associate .ps1 with PowerShell (double
rem  clicking one opens the "How do you want to open this file?" dialog) and the
rem  default execution policy blocks unsigned scripts. Both are by design, so a
rem  .ps1 alone can never be the "just double-click it" path.
rem
rem  ASCII only on purpose: cmd.exe reads batch files in the OEM code page, so
rem  inline CJK here would corrupt. All Chinese output comes from the .ps1.
rem
rem  Usage:
rem    setup-client.cmd                                     (interactive)
rem    setup-client.cmd http://host:18080 <SS_TOKEN> [-Mcp]
rem ===========================================================================
chcp 65001 >nul 2>&1
setlocal
title ShadowScribe - client setup

set "PS1=%~dp0setup-client.ps1"
if not exist "%PS1%" (
    echo.
    echo   [FAIL] setup-client.ps1 not found next to this launcher.
    echo          Expected: %PS1%
    echo.
    pause
    exit /b 1
)

set "EP="
set "TK="
set "EXTRA="

if "%~1"=="" goto :ask
set "EP=%~1"
set "TK=%~2"
if not "%~3"=="" set "EXTRA=%~3"
if not "%~4"=="" set "EXTRA=%EXTRA% %~4"
if not "%~5"=="" set "EXTRA=%EXTRA% %~5"
goto :run

:ask
echo.
echo   ShadowScribe client setup
echo   =========================
echo.
echo   Server address is the tunnel URL from your deployment,
echo   e.g. http://your-host:18080
echo.
set /p "EP=  Server address: "
if "%EP%"=="" (
    echo.
    echo   [FAIL] no address entered.
    pause
    exit /b 1
)
echo.
echo   SS_TOKEN is in the server's .env file:
echo     docker compose exec -T api printenv SS_TOKEN
echo.
set /p "TK=  SS_TOKEN: "
if "%TK%"=="" (
    echo.
    echo   [FAIL] no token entered.
    pause
    exit /b 1
)
echo.
set /p "MCP=  Also register MCP for Cursor? [y/N]: "
if /i "%MCP%"=="y" set "EXTRA=-Mcp"
echo.

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" -Endpoint "%EP%" -Token "%TK%" %EXTRA%
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo   Finished with warnings ^(exit %RC%^). Read the messages above.
) else (
    echo   Done.
)
echo.
pause
exit /b %RC%
