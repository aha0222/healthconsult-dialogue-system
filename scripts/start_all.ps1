# Xiao Nuan one-click setup and launch (Windows).
# Installs deps -> downloads models -> creates .env -> waits for API key -> starts backend+voice -> opens frontend.
[CmdletBinding()]
param(
    [switch]$SkipVoice,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"

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
    Write-Host "[ERROR] Python 3.10+ not found. Install it from https://www.python.org/downloads/ and check 'Add Python to PATH'." -ForegroundColor Red
    exit 1
}

# ── 1. 安装依赖 ─────────────────────────────────────────────
Write-Host ""
Write-Host "==> Installing backend dependencies (first time takes a few minutes)"
& $py -m pip install -r backend/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Backend dependencies install failed. Check network and retry." -ForegroundColor Red
    exit 1
}

if (-not $SkipVoice) {
    Write-Host "==> Installing voice dependencies (torch is large, first time is slow)"
    & $py -m pip install -r voice_service/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Voice dependencies install failed. Check network and retry." -ForegroundColor Red
        exit 1
    }
}

# ── 2. 下载语音模型 ─────────────────────────────────────────
if (-not $SkipVoice) {
    $asrModel = "voice_service/models/asr/model.int8.onnx"
    $cosyModel = "voice_service/models/cosyvoice2/llm.pt"
    if (-not (Test-Path $asrModel)) {
        Write-Host "==> Downloading ASR model (~300MB)"
        & $py voice_service/download_models.py
    }
    if (-not (Test-Path $cosyModel)) {
        Write-Host "==> Downloading CosyVoice2 model (~4GB, slowest the first time)"
        & $py voice_service/download_cosyvoice2.py
    }
}

# ── 3. 生成 .env 并检查密钥 ─────────────────────────────────
$envPath = Join-Path $Root ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $Root ".env.example") -Destination $envPath
    Write-Host "==> .env generated (copied from .env.example)"
}

function Get-ApiKey {
    $candidates = @()
    if ($env:DEEPSEEK_API_KEY) { $candidates += $env:DEEPSEEK_API_KEY }
    if (Test-Path -LiteralPath $envPath) {
        Get-Content -LiteralPath $envPath -Encoding UTF8 | ForEach-Object {
            if ($_ -match '^\s*(?:export\s+)?DEEPSEEK_API_KEY\s*=\s*(.*)$') {
                $candidates += $Matches[1]
            }
        }
    }
    foreach ($raw in $candidates) {
        $v = ([string]$raw).Trim().Trim([char]34).Trim([char]39).Trim()
        if (-not [string]::IsNullOrWhiteSpace($v) -and $v -ne "sk-xxx") { return $v }
    }
    return ""
}

if (-not $NoWait) {
    $key = Get-ApiKey
    while ([string]::IsNullOrWhiteSpace($key) -or $key -eq "sk-xxx") {
        Write-Host ""
        Write-Host "[WARN] DEEPSEEK_API_KEY is not set yet." -ForegroundColor Yellow
        Write-Host "       Notepad will open .env. Change sk-xxx to your real key." -ForegroundColor Yellow
        Write-Host "       Then press Ctrl+S to SAVE, and CLOSE Notepad." -ForegroundColor Yellow
        Start-Process notepad $envPath -Wait
        $key = Get-ApiKey
        if ([string]::IsNullOrWhiteSpace($key) -or $key -eq "sk-xxx") {
            $ans = Read-Host "Key still not detected. Press Enter to retry, or type 'skip' and Enter to continue without a key"
            if ($ans -eq "skip") { break }
        }
    }
    if ([string]::IsNullOrWhiteSpace($key) -or $key -eq "sk-xxx") {
        Write-Host "[WARN] Skipped API key. Backend will start but AI chat will not work." -ForegroundColor Yellow
    } else {
        Write-Host "[OK] API key detected." -ForegroundColor Green
    }
}

# ── 4. 启动后端 + 语音服务（两个窗口） ─────────────────────
function Start-PwshWindow([string]$command) {
    $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
    Start-Process -WindowStyle Normal -WorkingDirectory $Root powershell -ArgumentList "-NoExit", "-EncodedCommand", $encoded
}

$backendCmd = "$py -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000"
Write-Host "==> Starting backend (port 8000)"
Start-PwshWindow $backendCmd

if (-not $SkipVoice) {
    $voiceCmd = "$py -m voice_service"
    Write-Host "==> Starting voice service (port 8100)"
    Start-PwshWindow $voiceCmd
}

# ── 5. 打开前台 ─────────────────────────────────────────────
Start-Sleep -Seconds 3
$chatPath = Join-Path $Root "frontend\chat.html"
Write-Host "==> Opening frontend"
Start-Process $chatPath

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host " Started. Keep the two black windows (backend, voice) open." -ForegroundColor Green
Write-Host " Browser opened frontend\chat.html. In Settings, confirm:" -ForegroundColor Green
Write-Host "   Backend  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "   Voice    http://127.0.0.1:8100" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
