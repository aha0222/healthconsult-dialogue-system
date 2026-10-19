# Xiao Nuan voice-service runner (Windows).
# Installs deps -> downloads models (if missing) -> starts voice_service (default 8100).
[CmdletBinding()]
param(
    [switch]$SetupOnly,
    [switch]$SkipCosyVoice,
    [int]$Port = 8100
)

$ErrorActionPreference = "Stop"

# ── 定位仓库根目录（本脚本位于 scripts\） ────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$Root = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $Root

function Get-PythonCommand {
    foreach ($candidate in @("python", "py")) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) { return $candidate }
    }
    return $null
}

$py = Get-PythonCommand
if (-not $py) {
    Write-Host "[ERROR] Python 3.10+ not found. Install from https://www.python.org/downloads/ and check 'Add Python to PATH'." -ForegroundColor Red
    exit 1
}

# ── GPU / PyTorch check ─────────────────────────────────────
$hasTorch = & $py -c "import torch" 2>$null
if ($LASTEXITCODE -eq 0) {
    $cuda = & $py -c "import torch; print(torch.cuda.is_available())"
    if ($cuda -eq "True") {
        Write-Host "==> GPU PyTorch detected. Voice synthesis will use the GPU."
    } else {
        Write-Host "[WARN] PyTorch installed but no GPU detected. Will use CPU (slower)." -ForegroundColor Yellow
    }
} else {
    Write-Host "[WARN] PyTorch not detected. Voice service needs torch/torchaudio." -ForegroundColor Yellow
    Write-Host "       GPU users: install CUDA PyTorch first. CPU users: just continue." -ForegroundColor Yellow
}

Write-Host "==> Installing voice service dependencies (skips quickly if already installed)"
# Use Tsinghua mirror; torch/torchaudio are skipped if already installed with CUDA.
& $py -m pip install -r voice_service/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Dependencies install failed. Check network or run the pip command manually." -ForegroundColor Red
    exit 1
}

# ── Model check and download ────────────────────────────────
$asrModel = "voice_service/models/asr/model.int8.onnx"
$cosyModel = "voice_service/models/cosyvoice2/llm.pt"

if (-not (Test-Path $asrModel)) {
    Write-Host "==> ASR model not found, downloading (~300MB)"
    & $py voice_service/download_models.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] ASR model download failed. Retry or run voice_service\download_models.py manually." -ForegroundColor Red
        exit 1
    }
}

if (-not $SkipCosyVoice -and -not (Test-Path $cosyModel)) {
    Write-Host "==> CosyVoice2 model not found, downloading (~4GB, slow first time)"
    & $py voice_service/download_cosyvoice2.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[WARN] CosyVoice2 download failed. Retry later, or use -SkipCosyVoice for Piper/Kokoro fallback." -ForegroundColor Yellow
    }
}

if ($SetupOnly) {
    Write-Host "[OK] Voice service environment ready. Start with:" -ForegroundColor Green
    Write-Host "    python -m voice_service"
    Write-Host "    (to change port, set VOICE_PORT=8101 first)"
    exit 0
}

Write-Host "==> Starting voice service: http://127.0.0.1:$Port"
Write-Host "    First model load takes ~20-30s. Success when you see 'Uvicorn running'."
$env:VOICE_PORT = [string]$Port
& $py -m voice_service
