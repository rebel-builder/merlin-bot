@echo off
echo ============================================
echo   Merlin Setup — Windows Edition
echo ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.11+ from python.org
    echo Make sure to check "Add to PATH" during install.
    pause
    exit /b 1
)

:: Create venv
echo [1/5] Creating Python virtual environment...
if not exist venv (
    python -m venv venv
)
call venv\Scripts\activate.bat

:: Install dependencies
echo.
echo [2/5] Installing Python packages...
pip install --upgrade pip
pip install -r requirements.txt

:: Download YuNet model
echo.
echo [3/5] Downloading face detection model...
if not exist face_detection_yunet_2023mar.onnx (
    python -c "import urllib.request; urllib.request.urlretrieve('https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx', 'face_detection_yunet_2023mar.onnx'); print('Downloaded.')"
) else (
    echo Already exists.
)

:: Check for Kokoro model files
echo.
echo [4/5] Checking Kokoro TTS models...
if not exist kokoro-v1.0.onnx (
    echo.
    echo !! MANUAL STEP REQUIRED !!
    echo Download these two files into this folder:
    echo   1. kokoro-v1.0.onnx
    echo   2. voices-v1.0.bin
    echo From: https://github.com/thewh1teagle/kokoro-onnx/releases
    echo.
) else (
    echo Kokoro models found.
)

:: Check LM Studio
echo.
echo [5/5] Checking LM Studio...
python -c "import requests; r=requests.get('http://localhost:1234/v1/models',timeout=3); print('LM Studio is running.' if r.ok else 'LM Studio not responding.')" 2>nul
if errorlevel 1 (
    echo LM Studio not detected. Make sure it's running with a model loaded.
)

echo.
echo ============================================
echo   Setup complete!
echo.
echo   To start Merlin:
echo     venv\Scripts\activate
echo     python merlin.py
echo.
echo   Make sure:
echo     - LM Studio is running with a model loaded
echo     - EMEET PIXY is plugged in via USB
echo     - BT speaker is connected
echo ============================================
pause
