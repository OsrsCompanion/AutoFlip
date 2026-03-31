@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "DOWNLOADS=%USERPROFILE%\Downloads"
set "DEST=C:\osrs-flip-assistant\backend"
set "TEMP_EXTRACT=%TEMP%\osrs_patch_extract"

echo.
echo Looking for newest patch zip in:
echo %DOWNLOADS%
echo.

for /f "usebackq delims=" %%F in (`powershell -NoProfile -Command "(Get-ChildItem -Path \"$env:USERPROFILE\Downloads\" -Filter 'patch_*.zip' | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName"`) do (
    set "LATEST_ZIP=%%F"
)

if not defined LATEST_ZIP (
    echo ERROR: No patch_*.zip file found in Downloads.
    pause
    exit /b 1
)

echo Found latest patch:
echo %LATEST_ZIP%
echo.

if exist "%TEMP_EXTRACT%" rmdir /s /q "%TEMP_EXTRACT%"
mkdir "%TEMP_EXTRACT%"

echo Extracting patch...
powershell -NoProfile -Command "Expand-Archive -LiteralPath \"%LATEST_ZIP%\" -DestinationPath \"%TEMP_EXTRACT%\" -Force"

if errorlevel 1 (
    echo ERROR: Failed to extract zip.
    pause
    exit /b 1
)

for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "(Get-ChildItem -Path \"%TEMP_EXTRACT%\" -Directory -Recurse | Where-Object { $_.Name -eq 'backend' } | Select-Object -First 1).FullName"`) do (
    set "SOURCE_BACKEND=%%D"
)

if not defined SOURCE_BACKEND (
    echo ERROR: Could not find a backend folder inside the extracted patch.
    pause
    exit /b 1
)

echo Source backend:
echo %SOURCE_BACKEND%
echo Destination:
echo %DEST%
echo.

robocopy "%SOURCE_BACKEND%" "%DEST%" /MIR

set "RC=%ERRORLEVEL%"
if %RC% GEQ 8 (
    echo.
    echo ERROR: robocopy failed with code %RC%.
    pause
    exit /b %RC%
)

echo.
echo Patch applied successfully.
pause