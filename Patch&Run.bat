@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Patch + Run Debug

set "PATCH_DIR=C:\osrs-flip-assistant\incoming_patch"
set "DEST_BACKEND=C:\osrs-flip-assistant\backend"
set "DEST_APP=C:\osrs-flip-assistant\backend\app"
set "TEMP_EXTRACT=%TEMP%\osrs_patch_extract"

echo ==================================================
echo PATCH + RUN DEBUG
echo ==================================================
echo.

echo [1/8] Checking patch directory...
echo PATCH_DIR=%PATCH_DIR%
if not exist "%PATCH_DIR%" (
    echo ERROR: Patch directory does not exist.
    goto :fail
)

echo.
echo [2/8] Looking for exactly one zip...
set "ZIP_COUNT=0"
set "PATCH_ZIP="

for %%F in ("%PATCH_DIR%\*.zip") do (
    set /a ZIP_COUNT+=1
    set "PATCH_ZIP=%%~fF"
)

echo ZIP_COUNT=!ZIP_COUNT!
if "!ZIP_COUNT!"=="0" (
    echo ERROR: No zip file found in %PATCH_DIR%
    echo Put exactly one blessed patch zip in that folder.
    goto :fail
)

if not "!ZIP_COUNT!"=="1" (
    echo ERROR: Found !ZIP_COUNT! zip files in %PATCH_DIR%
    echo Keep only ONE blessed patch zip there.
    goto :fail
)

echo Found patch:
echo !PATCH_ZIP!

echo.
echo [3/8] Preparing temp extract folder...
if exist "%TEMP_EXTRACT%" (
    echo Removing old temp folder...
    rmdir /s /q "%TEMP_EXTRACT%"
    if errorlevel 1 (
        echo ERROR: Failed removing old temp folder.
        goto :fail
    )
)
mkdir "%TEMP_EXTRACT%"
if errorlevel 1 (
    echo ERROR: Failed creating temp extract folder.
    goto :fail
)

echo.
echo [4/8] Extracting patch...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
"try { Expand-Archive -LiteralPath '!PATCH_ZIP!' -DestinationPath '!TEMP_EXTRACT!' -Force -ErrorAction Stop; Write-Host 'Extract OK'; exit 0 } catch { Write-Host 'EXTRACT ERROR:'; Write-Host $_.Exception.Message; exit 1 }"

echo PowerShell extract exit code: !ERRORLEVEL!
if errorlevel 1 (
    echo ERROR: Extraction failed.
    goto :fail
)

echo.
echo [5/8] Detecting patch structure...
set "SOURCE_BACKEND="
set "SOURCE_APP="

for /f "usebackq delims=" %%D in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-ChildItem -Path \"%TEMP_EXTRACT%\" -Directory -Recurse | Where-Object { $_.FullName -match '\\backend$' } | Select-Object -First 1).FullName"`) do (
    set "SOURCE_BACKEND=%%D"
)

if not defined SOURCE_BACKEND (
    for /f "usebackq delims=" %%D in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-ChildItem -Path \"%TEMP_EXTRACT%\" -Directory -Recurse | Where-Object { $_.FullName -match '\\app$' } | Select-Object -First 1).FullName"`) do (
        set "SOURCE_APP=%%D"
    )
)

echo SOURCE_BACKEND=!SOURCE_BACKEND!
echo SOURCE_APP=!SOURCE_APP!

echo.
echo [6/8] Applying patch...
if defined SOURCE_BACKEND (
    echo Detected backend patch structure.
    echo Running:
    echo robocopy "!SOURCE_BACKEND!" "%DEST_BACKEND%" /E /IS /IT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
    robocopy "!SOURCE_BACKEND!" "%DEST_BACKEND%" /E /IS /IT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
    set "RC=!ERRORLEVEL!"
    echo Robocopy exit code: !RC!
    if !RC! GEQ 8 (
        echo ERROR: Robocopy failed.
        goto :fail
    )
    goto :patched
)

if defined SOURCE_APP (
    echo Detected app patch structure.
    echo Running:
    echo robocopy "!SOURCE_APP!" "%DEST_APP%" /E /IS /IT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
    robocopy "!SOURCE_APP!" "%DEST_APP%" /E /IS /IT /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
    set "RC=!ERRORLEVEL!"
    echo Robocopy exit code: !RC!
    if !RC! GEQ 8 (
        echo ERROR: Robocopy failed.
        goto :fail
    )
    goto :patched
)

echo ERROR: Could not find either a backend folder or an app folder inside the extracted patch.
goto :fail

:patched
echo.
echo [7/8] Patch applied successfully.
echo.

echo [8/8] Starting backend...
cd /d "%DEST_BACKEND%"
if errorlevel 1 (
    echo ERROR: Could not change directory to %DEST_BACKEND%
    goto :fail
)

echo Current directory:
cd

echo.
echo Running:
echo py -m uvicorn app.main:app --reload
echo.

py -m uvicorn app.main:app --reload
set "UVICORN_RC=%ERRORLEVEL%"

echo.
echo Uvicorn exit code: !UVICORN_RC!
echo Backend process ended.
goto :done

:fail
echo.
echo ==================================================
echo SCRIPT FAILED
echo ==================================================
echo.
pause
exit /b 1

:done
echo.
echo ==================================================
echo SCRIPT FINISHED
echo ==================================================
echo.
pause
exit /b 0