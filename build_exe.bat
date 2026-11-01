@echo off
REM Builds "Aura.exe" (a single Windows program) with PyInstaller.
REM The .exe is placed in THIS folder, next to .env, outputs\ and logs\,
REM because the app reads its API keys and saves its files there.
REM
REM   --windowed   no black console window
REM   --onefile    everything packed into one .exe
REM   --add-data   bundle the UI files (static\) and the tray icon inside the .exe
REM   --exclude-module  big libraries Aura never uses. PyMuPDF (PDF reading) can
REM                load an optional AI layout add-on, which pulls in torch, scipy,
REM                pandas... and makes the build huge and slow. Aura only needs
REM                plain PDF text, so these are left out.

REM Aura's Google client (phone reminders, notch calendar) goes inside the exe so
REM users don't need their own Google project. It is gitignored: the build stops
REM if it is missing, so a release never ships without it.
if not exist google_client.json (
  echo google_client.json is missing next to build_exe.bat - download it from Google Cloud Console ^(Clients^).
  exit /b 1
)

python -m PyInstaller --noconfirm --clean --windowed --onefile ^
  --name "Aura" ^
  --icon assets\icon.ico ^
  --version-file version_info.txt ^
  --add-data "static;static" ^
  --add-data "assets\icon.ico;assets" ^
  --add-data "google_client.json;." ^
  --hidden-import pystray._win32 ^
  --exclude-module pymupdf.layout --exclude-module pymupdf4llm --exclude-module pymupdf_layout ^
  --exclude-module onnxruntime --exclude-module torch --exclude-module tensorflow --exclude-module transformers ^
  --exclude-module scipy --exclude-module sklearn --exclude-module numba --exclude-module llvmlite ^
  --exclude-module librosa --exclude-module matplotlib --exclude-module pandas --exclude-module pyarrow ^
  --exclude-module sqlalchemy --exclude-module yt_dlp --exclude-module av --exclude-module rdflib ^
  --exclude-module cv2 --exclude-module grpc --exclude-module opentelemetry --exclude-module sentry_sdk ^
  --exclude-module uvicorn --exclude-module pytest --exclude-module IPython ^
  --distpath . --workpath build ^
  desktop.py
if errorlevel 1 (
  echo.
  echo Build FAILED - see the messages above. Aura.exe was not updated.
  exit /b 1
)

echo.
echo Done. Double-click "Aura.exe" to start.
