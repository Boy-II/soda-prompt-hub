@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Soda Prompt Hub Windows Worker - First Setup

if not exist "RELEASE.json" (
  echo [ERROR] RELEASE.json not found. Download the complete Worker release again.
  pause
  exit /b 2
)

if exist "worker-config.json" (
  echo Existing worker-config.json was kept unchanged.
) else (
  if not exist "worker-config.example.json" (
    echo [ERROR] worker-config.example.json not found.
    pause
    exit /b 2
  )
  copy /y "worker-config.example.json" "worker-config.json" >nul
  echo Created worker-config.json from the example.
)

echo.
echo Edit bridge_root, lora_roots and model_roots for this Windows PC.
echo Save the file, start ComfyUI, then run 1-先自检.bat.
start "" notepad.exe "worker-config.json"
pause
