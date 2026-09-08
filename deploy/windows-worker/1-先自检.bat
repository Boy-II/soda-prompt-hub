@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Prompt Hub Windows Worker - Self Test

if not exist "worker-config.json" (
  echo [ERROR] worker-config.json not found.
  echo Copy worker-config.example.json to worker-config.json first.
  pause
  exit /b 2
)

if not exist "prompt_hub_worker.py" (
  echo [ERROR] prompt_hub_worker.py not found.
  echo Download the complete deploy\windows-worker folder again.
  pause
  exit /b 2
)

if not exist "RELEASE.json" (
  echo [ERROR] RELEASE.json not found. Download the complete Worker release again.
  pause
  exit /b 2
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$r = Get-Content -LiteralPath 'RELEASE.json' -Raw | ConvertFrom-Json; Write-Host ('Worker version: ' + $r.worker_version + ' / Protocol: ' + $r.protocol_version)"
python --version
python prompt_hub_worker.py --config worker-config.json --self-test
set EXIT_CODE=%ERRORLEVEL%

echo.
if "%EXIT_CODE%"=="0" (
  echo [OK] Worker and local ComfyUI are ready.
) else (
  echo [ERROR] Self test failed. Exit code: %EXIT_CODE%
)
pause
exit /b %EXIT_CODE%
