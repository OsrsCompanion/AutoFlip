@echo off
setlocal

echo.
echo ==============================
echo   AutoFlip Git Push + Deploy
echo ==============================
echo.

REM Always run from the folder this batch file is in
cd /d "%~dp0"

echo Current folder:
echo %CD%
echo.

git status
if errorlevel 1 (
    echo.
    echo Git status failed. Make sure Git is installed and this folder is a Git repo.
    pause
    exit /b 1
)

echo.
set /p COMMIT_MSG=Enter commit message (or press Enter for default): 

if "%COMMIT_MSG%"=="" set COMMIT_MSG=update website

echo.
echo Staging files...
git add .
if errorlevel 1 (
    echo.
    echo Failed during git add.
    pause
    exit /b 1
)

echo.
echo Committing changes...
git commit -m "%COMMIT_MSG%"
if errorlevel 1 (
    echo.
    echo Commit step returned no changes or failed.
    echo If there was nothing to commit, your site may already be up to date.
)

echo.
echo Pushing to GitHub...
git push origin main
if errorlevel 1 (
    echo.
    echo Push failed.
    pause
    exit /b 1
)

echo.
echo Success! Changes pushed to GitHub.
echo Vercel should redeploy automatically in about 10-60 seconds.
echo.
pause
endlocal
