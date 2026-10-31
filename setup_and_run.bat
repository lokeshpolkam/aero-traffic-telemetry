@echo off
title AERO-TRAFFIC: Edge-AI Multi-Target Telemetry Platform
echo ==========================================================
echo    AERO-TRAFFIC: Autonomous Vision & Trajectory Tracker
echo             30-Second Fast Start Launcher
echo ==========================================================
echo.

cd /d "%~dp0"

echo [1/2] Verifying Python Dependencies...
pip install -r requirements.txt --quiet --disable-pip-version-check

echo [2/2] Launching Smart City Surveillance Operations Center...
python -m streamlit run app.py --server.port 8502
pause
