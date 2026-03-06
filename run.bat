@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    set "PY=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PY=py -3"
    ) else (
        echo Python was not found in PATH. Install Python 3.10+ and retry.
        exit /b 1
    )
)

echo Starting API and Streamlit in separate windows...
start "Marketplace API" /D "%~dp0" cmd /k %PY% app.py
timeout /t 1 >nul
start "Marketplace UI" /D "%~dp0" cmd /k %PY% -m streamlit run gui.py --server.port 8501

echo Started.
echo API:       http://127.0.0.1:8000
echo Streamlit: http://localhost:8501
endlocal
