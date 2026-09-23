@echo off
REM Build 先回 (XianHui) into a distributable Windows installer.
REM Requires: the .venv from setup, and Inno Setup 6 (iscc on PATH or default path).

setlocal
cd /d "%~dp0"

echo [1/4] Installing build dependencies...
".venv\Scripts\python.exe" -m pip install pyinstaller -r requirements.txt -q || goto :err

echo [2/4] Building with PyInstaller...
".venv\Scripts\python.exe" -m PyInstaller jev.spec --noconfirm --clean || goto :err

echo [3/4] Compiling installer with Inno Setup...
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist %ISCC% set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
  echo   Inno Setup not found. The app folder is ready at dist\XianHui.
  echo   Install Inno Setup 6, then run:  packaging\build_installer.bat
  goto :done
)
%ISCC% packaging\installer.iss || goto :err

echo [4/4] Done.
echo   App folder : dist\XianHui
echo   Installer  : packaging\output\XianHui-Setup.exe
goto :done

:err
echo BUILD FAILED.
exit /b 1

:done
endlocal
