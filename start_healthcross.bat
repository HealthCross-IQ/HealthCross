@echo off
rem Double-click this file to start the HealthCross server - no typing
rem commands, no picking the wrong folder or terminal. It just runs
rem start_healthcross.sh (in this same folder) through Git Bash.
setlocal
set SCRIPT_DIR=%~dp0
set BASH_EXE=

if exist "C:\Program Files\Git\bin\bash.exe" set BASH_EXE=C:\Program Files\Git\bin\bash.exe
if "%BASH_EXE%"=="" if exist "C:\Program Files (x86)\Git\bin\bash.exe" set BASH_EXE=C:\Program Files (x86)\Git\bin\bash.exe

if "%BASH_EXE%"=="" (
    where bash >nul 2>nul
    if errorlevel 1 (
        echo Could not find Git Bash automatically.
        echo Please open Git Bash manually and run: ./start_healthcross.sh
        pause
        exit /b 1
    ) else (
        set BASH_EXE=bash
    )
)

"%BASH_EXE%" "%SCRIPT_DIR%start_healthcross.sh"
pause
