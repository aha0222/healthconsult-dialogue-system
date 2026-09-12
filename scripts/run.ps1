# 小暖健康陪护 · 一键运行脚本（Windows）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\run.ps1 [-SetupOnly] [-NoTools] [-ListenHost H] [-Port P]
[CmdletBinding()]
param(
    [switch]$SetupOnly,
    [switch]$NoTools,
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 8000
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

Write-Host "==> 安装后端依赖"
& $venvPython -m pip install --upgrade pip | Out-Null
& $venvPython -m pip install -r backend/requirements.txt

if (-not $NoTools -and (Test-Path "tools/requirements.txt")) {
    Write-Host "==> 安装 tools 依赖"
    & $venvPython -m pip install -r tools/requirements.txt
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
& $venvPython -m uvicorn backend.app.main:app --host $ListenHost --port $Port
