@echo off
setlocal
title 实体商家 AI 内容工作台
cd /d "%~dp0"

if not exist config.json (
  echo [首次运行] 已从 config.example.json 生成 config.json，请填入你的 Kimi key 后重新启动。
  copy config.example.json config.json >nul
  notepad config.json
  exit /b
)

echo ============================================
echo   实体商家 AI 内容工作台  http://localhost:8787
echo   关闭本窗口即停止服务
echo ============================================
start "" http://localhost:8787
python server.py
pause
