@echo off
setlocal
cd /d "%~dp0"

set VERSION=1.2.2
set RELEASE_DIR=release\Pullio-%VERSION%-win64
set RELEASE_ZIP=release\Pullio-%VERSION%-win64.zip

echo ============================================
echo Building Pullio %VERSION%
echo ============================================
echo.

echo Checking required project files...

if not exist "src\pullio.py" (
  echo ERROR: src\pullio.py was not found.
  goto :err
)

if not exist "version_info.txt" (
  echo ERROR: version_info.txt was not found.
  goto :err
)

if not exist "yt-dlp.exe" (
  echo ERROR: yt-dlp.exe was not found in the project root.
  goto :err
)

if not exist "ffmpeg.exe" (
  echo ERROR: ffmpeg.exe was not found in the project root.
  goto :err
)

if not exist "ffprobe.exe" (
  echo ERROR: ffprobe.exe was not found in the project root.
  goto :err
)

echo Required files: OK
echo.

echo Installing/updating build dependencies...
python -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto :err

set ICON_ARG=
if exist "assets\pullio.ico" set ICON_ARG=--icon "assets\pullio.ico"

echo.
echo Building Pullio.exe...
python -m PyInstaller --noconfirm --clean --windowed --onefile ^
  --name Pullio ^
  --version-file version_info.txt ^
  --collect-all customtkinter ^
  %ICON_ARG% ^
  src\pullio.py

if errorlevel 1 goto :err

echo.
echo Preparing clean portable release folder...

if exist "%RELEASE_DIR%" (
  echo Cleaning previous release folder...
  rmdir /S /Q "%RELEASE_DIR%"
)

if exist "%RELEASE_ZIP%" (
  echo Removing previous release ZIP...
  del /Q "%RELEASE_ZIP%"
)

mkdir "%RELEASE_DIR%"
if errorlevel 1 goto :err

copy /Y "dist\Pullio.exe" "%RELEASE_DIR%\Pullio.exe" >nul
copy /Y "LICENSE" "%RELEASE_DIR%\LICENSE.txt" >nul
copy /Y "THIRD_PARTY_NOTICES.md" "%RELEASE_DIR%\THIRD_PARTY_NOTICES.md" >nul
copy /Y "yt-dlp.exe" "%RELEASE_DIR%\yt-dlp.exe" >nul
copy /Y "ffmpeg.exe" "%RELEASE_DIR%\ffmpeg.exe" >nul
copy /Y "ffprobe.exe" "%RELEASE_DIR%\ffprobe.exe" >nul

REM Personal runtime files are intentionally NOT copied.
REM pullio_settings.json and pullio_history.json are created locally by each user.

echo.
echo Verifying release package...

if not exist "%RELEASE_DIR%\Pullio.exe" goto :verifyerr
if not exist "%RELEASE_DIR%\yt-dlp.exe" goto :verifyerr
if not exist "%RELEASE_DIR%\ffmpeg.exe" goto :verifyerr
if not exist "%RELEASE_DIR%\ffprobe.exe" goto :verifyerr
if not exist "%RELEASE_DIR%\LICENSE.txt" goto :verifyerr
if not exist "%RELEASE_DIR%\THIRD_PARTY_NOTICES.md" goto :verifyerr

if exist "%RELEASE_DIR%\ytdownload.exe" goto :legacyerr
if exist "%RELEASE_DIR%\pullio_settings.json" goto :privacyerr
if exist "%RELEASE_DIR%\pullio_history.json" goto :privacyerr

echo Release folder verification: OK

echo.
echo Creating release ZIP...
powershell -NoProfile -Command "Compress-Archive -Path '%RELEASE_DIR%\*' -DestinationPath '%RELEASE_ZIP%' -Force"
if errorlevel 1 goto :ziperr

if not exist "%RELEASE_ZIP%" goto :ziperr

echo.
echo ============================================
echo Pullio %VERSION% build complete.
echo.
echo EXE:
echo dist\Pullio.exe
echo.
echo Clean portable folder:
echo %RELEASE_DIR%
echo.
echo Public release ZIP:
echo %RELEASE_ZIP%
echo.
echo Dependencies:
echo yt-dlp.exe
echo ffmpeg.exe
echo ffprobe.exe
echo ============================================
pause
exit /b 0

:verifyerr
echo.
echo ERROR: Release verification failed.
echo One or more required files are missing.
pause
exit /b 1

:legacyerr
echo.
echo ERROR: Legacy ytdownload.exe was found in the public release.
echo Public builds must expose the dependency as yt-dlp.exe.
pause
exit /b 1

:privacyerr
echo.
echo ERROR: Personal JSON files were found in the release folder.
echo Release was stopped to protect user data.
pause
exit /b 1

:ziperr
echo.
echo ERROR: Could not create release ZIP.
pause
exit /b 1

:err
echo.
echo Build failed.
pause
exit /b 1
