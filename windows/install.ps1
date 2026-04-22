# Merlin Windows Installer — PowerShell validation + setup
# Usage: .\install.ps1  (from C:\merlin\windows)
# Creates venv, installs deps, downloads models, verifies camera/audio, checks LM Studio.

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
if (-not $ScriptDir -or $ScriptDir -eq "") { $ScriptDir = Get-Location }

# ─── Color helpers ────────────────────────────────────────────────────────────
function Pass($msg)  { Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Fail($msg)  { Write-Host "  [FAIL] $msg" -ForegroundColor Red }
function Warn($msg)  { Write-Host "  [WARN] $msg" -ForegroundColor Yellow }
function Step($n, $total, $msg) {
    Write-Host ""
    Write-Host "[$n/$total] $msg" -ForegroundColor Cyan
}
function Banner($msg) {
    Write-Host ""
    Write-Host ("=" * 56) -ForegroundColor DarkCyan
    Write-Host "  $msg" -ForegroundColor White
    Write-Host ("=" * 56) -ForegroundColor DarkCyan
}

# ─── Result tracker ───────────────────────────────────────────────────────────
$Results = [ordered]@{}

function Record($key, $passed, $detail = "") {
    $Results[$key] = @{ Passed = $passed; Detail = $detail }
}

# ═══════════════════════════════════════════════════════════════════════════════
Banner "Merlin — Windows Installer"
Write-Host "  Working directory: $ScriptDir" -ForegroundColor DarkGray
# ═══════════════════════════════════════════════════════════════════════════════

# ─── STEP 1: Python 3.10+ ─────────────────────────────────────────────────────
Step 1 9 "Checking Python version..."

$PythonCmd = $null
foreach ($candidate in @("python", "python3", "py")) {
    try {
        $ver = & $candidate --version 2>&1
        if ($ver -match "Python (\d+)\.(\d+)") {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 10) {
                $PythonCmd = $candidate
                Pass "Found $ver (>= 3.10 required)"
                Record "Python 3.10+" $true $ver
                break
            } else {
                Fail "Found $ver — need 3.10 or higher"
                Record "Python 3.10+" $false $ver
            }
        }
    } catch { }
}

if (-not $PythonCmd) {
    Fail "Python not found in PATH."
    Warn "Install Python 3.11+ from https://python.org — check 'Add to PATH' during install."
    Record "Python 3.10+" $false "Not found"
    Write-Host ""
    Write-Host "Cannot continue without Python. Exiting." -ForegroundColor Red
    exit 1
}

# ─── STEP 2: Create venv ─────────────────────────────────────────────────────
Step 2 9 "Creating virtual environment in venv/..."

$VenvDir = Join-Path $ScriptDir "venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"

try {
    if (Test-Path $VenvDir) {
        Warn "venv/ already exists — skipping creation"
        Record "Create venv" $true "Already exists"
    } else {
        & $PythonCmd -m venv $VenvDir 2>&1 | Out-Null
        if (Test-Path $VenvPython) {
            Pass "Virtual environment created at venv/"
            Record "Create venv" $true $VenvDir
        } else {
            throw "venv creation failed — $VenvPython not found"
        }
    }
} catch {
    Fail "Could not create venv: $_"
    Record "Create venv" $false "$_"
}

# ─── STEP 3: Install dependencies ─────────────────────────────────────────────
Step 3 9 "Installing Python packages from requirements.txt..."

$ReqFile = Join-Path $ScriptDir "requirements.txt"

if (-not (Test-Path $ReqFile)) {
    Fail "requirements.txt not found at $ReqFile"
    Record "Install deps" $false "requirements.txt missing"
} elseif (-not (Test-Path $VenvPip)) {
    Fail "pip not found in venv — skipping install"
    Record "Install deps" $false "pip missing"
} else {
    try {
        Write-Host "  Upgrading pip..." -ForegroundColor DarkGray
        & $VenvPip install --upgrade pip --quiet 2>&1 | Out-Null

        Write-Host "  Installing packages (this may take a few minutes)..." -ForegroundColor DarkGray
        $output = & $VenvPip install -r $ReqFile 2>&1
        $exitCode = $LASTEXITCODE

        if ($exitCode -eq 0) {
            Pass "All packages installed"
            Record "Install deps" $true
        } else {
            # Check for partial failure — GPU onnxruntime fallback
            if ($output -match "onnxruntime-gpu") {
                Warn "onnxruntime-gpu failed — trying onnxruntime (CPU) as fallback..."
                & $VenvPip install onnxruntime --quiet 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Warn "Installed onnxruntime (CPU). GPU acceleration unavailable."
                    Record "Install deps" $true "CPU fallback for onnxruntime"
                } else {
                    Fail "Package install failed (exit $exitCode)"
                    Record "Install deps" $false "exit $exitCode"
                }
            } else {
                Fail "Package install failed (exit $exitCode)"
                Record "Install deps" $false "exit $exitCode"
                Write-Host ($output | Select-Object -Last 10 | Out-String) -ForegroundColor DarkRed
            }
        }
    } catch {
        Fail "Exception during install: $_"
        Record "Install deps" $false "$_"
    }
}

# ─── STEP 4: Download YuNet model ─────────────────────────────────────────────
Step 4 9 "Downloading YuNet face detection model..."

$YuNetPath = Join-Path $ScriptDir "face_detection_yunet_2023mar.onnx"
$YuNetUrl  = "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx"

if (Test-Path $YuNetPath) {
    Pass "YuNet model already exists — skipping download"
    Record "YuNet model" $true "Already present"
} else {
    try {
        Write-Host "  Downloading from GitHub (~300 KB)..." -ForegroundColor DarkGray
        Invoke-WebRequest -Uri $YuNetUrl -OutFile $YuNetPath -UseBasicParsing
        if (Test-Path $YuNetPath) {
            $size = [math]::Round((Get-Item $YuNetPath).Length / 1KB, 1)
            Pass "YuNet model downloaded (${size} KB)"
            Record "YuNet model" $true "${size} KB"
        } else {
            throw "File not present after download"
        }
    } catch {
        Fail "YuNet download failed: $_"
        Warn "Manual download: $YuNetUrl"
        Warn "Save to: $YuNetPath"
        Record "YuNet model" $false "$_"
    }
}

# ─── STEP 5: Kokoro TTS models ────────────────────────────────────────────────
Step 5 9 "Checking / downloading Kokoro TTS models..."

$KokoroOnnx  = Join-Path $ScriptDir "kokoro-v1.0.onnx"
$KokoroVoices = Join-Path $ScriptDir "voices-v1.0.bin"
$KokoroBase  = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"

$kokoroPassed = $true
$kokoroDetail = ""

foreach ($item in @(
    @{ Path = $KokoroOnnx;   Name = "kokoro-v1.0.onnx";   Url = "$KokoroBase/kokoro-v1.0.onnx";   SizeMB = "310 MB" },
    @{ Path = $KokoroVoices; Name = "voices-v1.0.bin";    Url = "$KokoroBase/voices-v1.0.bin";    SizeMB = "~20 MB" }
)) {
    if (Test-Path $item.Path) {
        Pass "$($item.Name) already exists — skipping"
    } else {
        Write-Host "  Downloading $($item.Name) ($($item.SizeMB)) — please wait..." -ForegroundColor DarkGray
        try {
            Invoke-WebRequest -Uri $item.Url -OutFile $item.Path -UseBasicParsing
            if (Test-Path $item.Path) {
                $size = [math]::Round((Get-Item $item.Path).Length / 1MB, 1)
                Pass "$($item.Name) downloaded (${size} MB)"
            } else {
                throw "File not present after download"
            }
        } catch {
            Fail "$($item.Name) download failed: $_"
            Warn "Manual download: $($item.Url)"
            Warn "Save to: $($item.Path)"
            $kokoroPassed = $false
            $kokoroDetail += "$($item.Name) failed; "
        }
    }
}

if ($kokoroPassed) {
    Record "Kokoro models" $true
} else {
    Record "Kokoro models" $false $kokoroDetail
}

# ─── STEP 6: LM Studio ────────────────────────────────────────────────────────
Step 6 9 "Checking LM Studio (localhost:1234)..."

$lmRunning = $false
try {
    $response = Invoke-WebRequest -Uri "http://localhost:1234/v1/models" -UseBasicParsing -TimeoutSec 4 -ErrorAction Stop
    if ($response.StatusCode -eq 200) {
        $body = $response.Content | ConvertFrom-Json -ErrorAction SilentlyContinue
        $modelCount = if ($body.data) { $body.data.Count } else { "?" }
        Pass "LM Studio is running — $modelCount model(s) loaded"
        Record "LM Studio" $true "$modelCount model(s)"
        $lmRunning = $true
    }
} catch {
    Warn "LM Studio not detected on localhost:1234"
    Warn "Start LM Studio, load a model, and enable the local server before running Merlin."
    Record "LM Studio" $false "Not responding"
}

# ─── STEP 7: EMEET PIXY camera ───────────────────────────────────────────────
Step 7 9 "Checking EMEET PIXY camera..."

$cameraFound = $false

if (Test-Path $VenvPython) {
    $camScript = @"
import sys
try:
    import cv2
    found = []
    for i in range(6):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            name = "Unknown"
            try:
                name = cap.getBackendName()
            except:
                pass
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            found.append(f"  index {i}: {w}x{h}")
            cap.release()
    if found:
        print("CAMERAS_FOUND")
        for f in found:
            print(f)
    else:
        print("NO_CAMERAS")
except ImportError:
    print("CV2_NOT_INSTALLED")
except Exception as e:
    print(f"ERROR:{e}")
"@

    try {
        $camResult = & $VenvPython -c $camScript 2>&1
        if ($camResult -match "CAMERAS_FOUND") {
            $lines = ($camResult -split "`n") | Where-Object { $_ -match "index" }
            Pass "Camera(s) detected:"
            foreach ($line in $lines) { Write-Host "    $line" -ForegroundColor Green }

            # PIXY check — look for multiple cameras or USB device list
            $wmiCams = Get-PnpDevice -Class "Camera" -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq "OK" }
            $pixyCam = $wmiCams | Where-Object { $_.FriendlyName -match "EMEET|PIXY" }
            if ($pixyCam) {
                Pass "EMEET PIXY confirmed: $($pixyCam.FriendlyName)"
                Record "EMEET PIXY" $true $pixyCam.FriendlyName
            } else {
                Warn "Camera found but EMEET PIXY not specifically identified."
                Warn "Make sure EMEET PIXY is connected via USB."
                # Still a soft pass — a camera IS present
                Record "EMEET PIXY" $true "Camera present (PIXY not specifically identified)"
            }
            $cameraFound = $true
        } elseif ($camResult -match "NO_CAMERAS") {
            Fail "No cameras detected. Plug in EMEET PIXY via USB."
            Record "EMEET PIXY" $false "No cameras found"
        } elseif ($camResult -match "CV2_NOT_INSTALLED") {
            Warn "opencv not installed yet — camera check skipped"
            Record "EMEET PIXY" $false "opencv not installed"
        } else {
            Fail "Camera check error: $camResult"
            Record "EMEET PIXY" $false "$camResult"
        }
    } catch {
        Fail "Camera probe failed: $_"
        Record "EMEET PIXY" $false "$_"
    }
} else {
    Warn "venv Python not available — camera check skipped"
    Record "EMEET PIXY" $false "Python venv not ready"
}

# ─── STEP 8: Audio test ───────────────────────────────────────────────────────
Step 8 9 "Running audio device test..."

if (Test-Path $VenvPython) {
    $audioScript = @"
import sys
try:
    import sounddevice as sd
    devices = sd.query_devices()
    inputs  = [d for d in devices if d['max_input_channels'] > 0]
    outputs = [d for d in devices if d['max_output_channels'] > 0]
    default_in  = sd.query_devices(kind='input')
    default_out = sd.query_devices(kind='output')
    print(f"INPUT_DEVICES:{len(inputs)}")
    print(f"OUTPUT_DEVICES:{len(outputs)}")
    print(f"DEFAULT_IN:{default_in['name']}")
    print(f"DEFAULT_OUT:{default_out['name']}")
    # Brief playback test (440 Hz sine, 0.3s, low volume)
    import numpy as np
    t = np.linspace(0, 0.3, int(0.3 * 44100), endpoint=False)
    tone = (0.15 * np.sin(2 * np.pi * 440 * t)).astype('float32')
    sd.play(tone, 44100)
    sd.wait()
    print("PLAYBACK_OK")
except ImportError:
    print("SOUNDDEVICE_NOT_INSTALLED")
except Exception as e:
    print(f"AUDIO_ERROR:{e}")
"@

    try {
        $audioResult = & $VenvPython -c $audioScript 2>&1

        if ($audioResult -match "SOUNDDEVICE_NOT_INSTALLED") {
            Warn "sounddevice not installed — audio test skipped"
            Record "Audio test" $false "sounddevice not installed"
        } elseif ($audioResult -match "AUDIO_ERROR:(.+)") {
            Fail "Audio error: $($Matches[1])"
            Record "Audio test" $false $Matches[1]
        } else {
            $inLine  = ($audioResult | Select-String "INPUT_DEVICES:(\d+)").Matches[0].Groups[1].Value
            $outLine = ($audioResult | Select-String "OUTPUT_DEVICES:(\d+)").Matches[0].Groups[1].Value
            $defIn   = ($audioResult | Select-String "DEFAULT_IN:(.+)").Matches[0].Groups[1].Value
            $defOut  = ($audioResult | Select-String "DEFAULT_OUT:(.+)").Matches[0].Groups[1].Value

            Pass "Audio devices: $inLine input(s), $outLine output(s)"
            Pass "Default mic:     $defIn"
            Pass "Default speaker: $defOut"

            if ($audioResult -match "PLAYBACK_OK") {
                Pass "Playback test: tone played successfully"
                Record "Audio test" $true "in=$inLine out=$outLine"
            } else {
                Warn "Playback test did not confirm success (silent failure or no speakers)"
                Record "Audio test" $true "Devices OK; playback unconfirmed"
            }
        }
    } catch {
        Fail "Audio test exception: $_"
        Record "Audio test" $false "$_"
    }
} else {
    Warn "venv Python not available — audio test skipped"
    Record "Audio test" $false "Python venv not ready"
}

# ─── STEP 9: Final validation summary ────────────────────────────────────────
Step 9 9 "Generating summary..."

$passed = 0
$failed = 0

Banner "Setup Summary"
foreach ($key in $Results.Keys) {
    $r = $Results[$key]
    if ($r.Passed) {
        $passed++
        $detail = if ($r.Detail) { " — $($r.Detail)" } else { "" }
        Write-Host ("  [PASS] {0,-20}{1}" -f $key, $detail) -ForegroundColor Green
    } else {
        $failed++
        $detail = if ($r.Detail) { " — $($r.Detail)" } else { "" }
        Write-Host ("  [FAIL] {0,-20}{1}" -f $key, $detail) -ForegroundColor Red
    }
}

Write-Host ""
Write-Host ("  $passed passed, $failed failed") -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Yellow" })

if ($failed -eq 0) {
    Write-Host ""
    Write-Host "  All checks passed! Merlin is ready." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  Fix the FAIL items above, then run install.ps1 again." -ForegroundColor Yellow
}

Write-Host ""
Banner "How to start Merlin"
Write-Host @"
  1. Make sure LM Studio is running with a model loaded
     (local server on port 1234 must be enabled)

  2. Plug in EMEET PIXY via USB

  3. Open PowerShell in this folder and run:

       .\venv\Scripts\Activate.ps1
       python merlin.py

  Note: if Activate.ps1 is blocked, run this first:
       Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
"@ -ForegroundColor White

Write-Host ("=" * 56) -ForegroundColor DarkCyan
Write-Host ""
