# 小暖健康陪护 · 一键运行脚本（Windows）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\run.ps1 [-SetupOnly] [-NoTools] [-ListenHost H] [-Port P] [-Voice] [-ForceInstall]
[CmdletBinding()]
param(
    [switch]$SetupOnly,
    [switch]$NoTools,
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 8000,
    [switch]$Voice,
    [switch]$ForceInstall
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
    Write-Host "✗ 找不到 Python，请先安装 Python 3.10+（https://www.python.org/downloads/）"
    exit 1
}

$venvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "==> 创建虚拟环境 .venv"
    if ($py -eq "py") {
        & py -3 -m venv .venv
    } else {
        & python -m venv .venv
    }
    if (-not (Test-Path $venvPython)) {
        Write-Host "✗ 创建虚拟环境失败，请确认已安装 Python 3.10+。"
        exit 1
    }
}

# ── 依赖：要求文件没变且关键包可导入就跳过，避免每次启动都等 pip ──────
# 装过一次后按 requirements 内容 + Python 版本打一个戳；戳对得上就跳过。
# 要强制重装：加 -ForceInstall。
function Get-ReqFiles {
    $files = @("backend/requirements.txt")
    if (-not $NoTools -and (Test-Path "tools/requirements.txt")) {
        $files += "tools/requirements.txt"
    }
    return $files
}

function Get-ReqStamp([string[]]$files) {
    $hash = ($files | ForEach-Object { (Get-FileHash $_ -Algorithm SHA256).Hash }) -join "|"
    $pyVer = (& $venvPython -c "import sys;print(sys.version.split()[0])" 2>$null)
    return "$hash|$pyVer"
}

$reqFiles = Get-ReqFiles
$stampFile = Join-Path $Root ".venv\.install-stamp"
$needInstall = $true

if (-not $ForceInstall -and (Test-Path $stampFile)) {
    $want = Get-ReqStamp $reqFiles
    $have = (Get-Content $stampFile -Raw -ErrorAction SilentlyContinue)
    if ($have -and $have.Trim() -eq $want) {
        # 戳对得上，再确认关键包真的能导入（防止手删过 site-packages）
        & $venvPython -c "import fastapi, uvicorn, pydantic" 2>$null
        if ($LASTEXITCODE -eq 0) { $needInstall = $false }
    }
}

if ($needInstall) {
    Write-Host "==> 安装后端依赖"
    & $venvPython -m pip install --upgrade pip | Out-Null
    & $venvPython -m pip install -r backend/requirements.txt

    if (-not $NoTools -and (Test-Path "tools/requirements.txt")) {
        Write-Host "==> 安装 tools 依赖"
        & $venvPython -m pip install -r tools/requirements.txt
    }

    Set-Content -Path $stampFile -Value (Get-ReqStamp $reqFiles) -Encoding UTF8
} else {
    Write-Host "==> 依赖已满足（requirements.txt 未变），跳过安装；要强制重装加 -ForceInstall"
}

# ── .env ─────────────────────────────────────────────────────
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "==> 已生成 .env（请填写 DEEPSEEK_API_KEY）"
}

$key = ""
Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*DEEPSEEK_API_KEY\s*=\s*(.*)$') { $key = $Matches[1].Trim() }
}
if ([string]::IsNullOrWhiteSpace($key) -or $key -eq "sk-xxx") {
    Write-Host "⚠ .env 中的 DEEPSEEK_API_KEY 还未填写，后端无法调用大模型。" -ForegroundColor Yellow
    Write-Host "  请编辑 .env 后重新运行本脚本。"
}

if ($SetupOnly) {
    Write-Host "✓ 环境准备完成。启动后端："
    Write-Host "    .\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host $ListenHost --port $Port"
    exit 0
}

Write-Host "==> 启动后端：http://$ListenHost`:$Port"
Write-Host "    前端：浏览器打开 frontend/chat.html，在设置里确认后端地址"

# ── 数据留存：演示机同样会落真实对话，这里显式给一个留存期 ──────────
# 默认 180 天（0＝永久）。.env 里已经设了就不覆盖，保留使用者的显式选择。
if (-not $env:AUDIT_RETENTION_DAYS) { $env:AUDIT_RETENTION_DAYS = "180" }

# ── 可选：一并起语音服务（云端模式下的演示用）────────────────────
# 主路径是在线档，但朗读仍走本地 voice_service（省调用、低延迟、断网也能播报）。
$voiceProc = $null
if ($Voice) {
    $env:VOICE_ENABLED = "1"
    $env:SPEAKER_DETECT_ENABLED = "1"
    Write-Host "==> 启动语音服务：http://127.0.0.1:8100（VOICE_ENABLED=1）"
    $voiceLog = Join-Path $Root ".voice.out.log"
    $voiceErr = Join-Path $Root ".voice.err.log"
    $voiceProc = Start-Process -FilePath $venvPython -ArgumentList @("-m", "voice_service") `
        -PassThru -NoNewWindow `
        -RedirectStandardOutput $voiceLog -RedirectStandardError $voiceErr
}

try {
    & $venvPython -m uvicorn backend.app.main:app --host $ListenHost --port $Port
} finally {
    if ($voiceProc -and -not $voiceProc.HasExited) {
        Stop-Process -Id $voiceProc.Id -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止语音服务（PID $($voiceProc.Id)）"
    }
}
