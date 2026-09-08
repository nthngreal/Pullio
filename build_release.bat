@echo off
setlocal
cd /d "%~dp0"

echo Installing/updating build dependencies...
python -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto :err

if not exist "yt-dlp.exe" (
  echo.
  echo ERROR: yt-dlp.exe was not found in the project root.
  echo.
  echo If your old file is still named ytdownload.exe,
  echo run migrate_yt_dlp_name.bat first.
  pause
  exit /b 1
)

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

set RELEASE_DIR=release\Pullio-1.0.0-win64

if exist "%RELEASE_DIR%" (
  echo Cleaning previous release folder...
  rmdir /S /Q "%RELEASE_DIR%"
)

mkdir "%RELEASE_DIR%"

copy /Y "dist\Pullio.exe" "%RELEASE_DIR%\Pullio.exe" >nul
copy /Y "LICENSE" "%RELEASE_DIR%\LICENSE.txt" >nul
copy /Y "THIRD_PARTY_NOTICES.md" "%RELEASE_DIR%\THIRD_PARTY_NOTICES.md" >nul

copy /Y "yt-dlp.exe" "%RELEASE_DIR%\yt-dlp.exe" >nul
if exist "ffmpeg.exe" copy /Y "ffmpeg.exe" "%RELEASE_DIR%\ffmpeg.exe" >nul
if exist "ffprobe.exe" copy /Y "ffprobe.exe" "%RELEASE_DIR%\ffprobe.exe" >nul

REM Personal runtime files are intentionally NOT copied.
REM pullio_settings.json and pullio_history.json are created locally by each user.

echo.
echo Verifying release package...

if not exist "%RELEASE_DIR%\Pullio.exe" goto :verifyerr
if not exist "%RELEASE_DIR%\yt-dlp.exe" goto :verifyerr
if not exist "%RELEASE_DIR%\ffmpeg.exe" goto :verifyerr
if not exist "%RELEASE_DIR%\ffprobe.exe" goto :verifyerr

if exist "%RELEASE_DIR%\ytdownload.exe" goto :legacyerr
if exist "%RELEASE_DIR%\pullio_settings.json" goto :privacyerr
if exist "%RELEASE_DIR%\pullio_history.json" goto :privacyerr

echo.
echo Creating release ZIP...
powershell -NoProfile -Command "Compress-Archive -Path '%RELEASE_DIR%\*' -DestinationPath 'release\Pullio-1.0.0-win64.zip' -Force"
if errorlevel 1 goto :ziperr

echo.
echo ============================================
echo Pullio 1.0.0 build complete.
echo.
echo EXE:
echo dist\Pullio.exe
echo.
echo Clean portable folder:
echo %RELEASE_DIR%
echo.
echo Public release ZIP:
echo release\Pullio-1.0.0-win64.zip
echo.
echo Dependency name:
echo yt-dlp.exe
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
