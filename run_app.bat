@echo off
REM ---------------------------------------------------------------------
REM  Launch the bracket CAD-to-CAE app.
REM
REM  Double-click this file. A console window opens, the app starts, and
REM  your browser opens at http://localhost:8501. Close the console window
REM  (or press Ctrl+C in it) to stop the app.
REM
REM  Conda is located by searching the usual install locations rather than
REM  being hard-coded, so this file carries no personal path.
REM ---------------------------------------------------------------------

setlocal

REM Run from the folder this script lives in, whatever the working directory.
cd /d "%~dp0"

set "PORT=8501"
set "CONDA_BAT="

REM Prefer conda already on PATH.
where conda.bat >nul 2>&1 && for /f "delims=" %%i in ('where conda.bat') do set "CONDA_BAT=%%i"

if not defined CONDA_BAT (
    for %%p in (
        "%USERPROFILE%\miniconda3\condabin\conda.bat"
        "%USERPROFILE%\anaconda3\condabin\conda.bat"
        "%LOCALAPPDATA%\miniconda3\condabin\conda.bat"
        "%ProgramData%\miniconda3\condabin\conda.bat"
        "%ProgramData%\anaconda3\condabin\conda.bat"
    ) do (
        if not defined CONDA_BAT if exist "%%~p" set "CONDA_BAT=%%~p"
    )
)

if not defined CONDA_BAT (
    echo.
    echo   Could not find conda.
    echo.
    echo   Install Miniconda, then create the environment with:
    echo       conda env create -f environment.yml
    echo.
    pause
    exit /b 1
)

echo Using conda at: %CONDA_BAT%
echo Checking the environment...

REM Check the environment before using it, so a missing one gives a readable
REM message instead of a stack trace.
call "%CONDA_BAT%" run -n bracket-cae python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   The 'bracket-cae' environment is missing or incomplete.
    echo.
    echo   Create it with:
    echo       conda env create -f environment.yml
    echo.
    pause
    exit /b 1
)

echo.
echo   Starting the app on http://localhost:%PORT%
echo   Your browser will open in a few seconds.
echo.
echo   Leave this window open while you use the app.
echo   Close it, or press Ctrl+C, to stop.
echo.

REM Open the browser shortly after the server starts. Streamlit runs headless
REM here because its own browser-opening path also triggers a first-run
REM "Welcome / Email:" prompt that waits on input, which would leave a
REM double-clicked window sitting there apparently frozen.
start "" /b cmd /c "timeout /t 8 /nobreak >nul & start "" http://localhost:%PORT%"

call "%CONDA_BAT%" run -n bracket-cae --no-capture-output ^
    streamlit run app.py --server.port %PORT% --server.headless true

REM Keep the window open if it exited with an error, so the message can be
REM read rather than vanishing with the console.
if errorlevel 1 pause

endlocal
