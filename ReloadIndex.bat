@echo off
setlocal

cd /d C:\osrs-flip-assistant\backend

echo Installing missing basics if needed...
py -m pip install openai uvicorn fastapi pygetwindow pyautogui pillow mss pytesseract opencv-python

echo.
echo Starting backend...
py -m uvicorn app.main:app --reload

pause