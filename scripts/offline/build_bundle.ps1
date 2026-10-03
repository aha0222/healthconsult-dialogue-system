<#
.SYNOPSIS
    打离线部署包（成员 B）：把代码、模型、推理引擎、Python 依赖收进一个目录。

.DESCRIPTION
    产物在 scripts\offline\dist\ （已被本目录 .gitignore 忽略，不入库）。
    目标机冷启动只需一条命令：

        powershell -ExecutionPolicy Bypass -File RUN.ps1

    前置条件：本机已跑过 fetch_model.ps1（模型与 llama-server 已就位）。
    目标机需要装 Python 3.12（wheels 只免去联网装依赖，不能替代解释器）。

.PARAMETER OutDir
    输出目录，默认 scripts\offline\dist。

.PARAMETER SkipWheels
    跳过 pip download（依赖已在目标机上装好时用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\offline\build_bundle.ps1
#>
[CmdletBinding()]
param(
    [string]$OutDir = "",
    [switch]$SkipWheels
)

$ErrorActionPreference = "Stop"
$OfflineDir = $PSScriptRoot
$RepoRoot   = (Resolve-Path (Join-Path $OfflineDir "..\..")).Path
if (-not $OutDir) { $OutDir = Join-Path $OfflineDir "dist" }

function Write-Step($m) { Write-Host "`n[打包] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [完成] $m" -ForegroundColor Green }

# 路径不含非 ASCII：llama-server / GGUF 在中文路径下个别版本会出问题
if ($OutDir -match "[^\x00-\x7F]") {
    Write-Host "  [警告] 输出路径含非 ASCII 字符，llama.cpp 在部分版本下会因此启动失败：" -ForegroundColor Yellow
    Write-Host "         $OutDir" -ForegroundColor Yellow
    Write-Host "         建议用 -OutDir 指定纯英文路径。" -ForegroundColor Yellow
}

# ── 1. 复制代码 ───────────────────────────────────────────────────────
Write-Step "复制仓库代码 → $OutDir\app"
$appDir = Join-Path $OutDir "app"
New-Item -ItemType Directory -Force $appDir | Out-Null

# 只带运行必需的目录；测试、结果、虚拟环境、缓存一律不带
$include = @("backend", "frontend", "skills", "tools", "voice_service", "scripts", "docs")
$excludeDirs = @(".git", ".venv", "venv", "__pycache__", ".pytest_cache",
                 ".cache", "results", "dist", "models", "bin", "wheels", "logs")

foreach ($d in $include) {
    $src = Join-Path $RepoRoot $d
    if (-not (Test-Path $src)) {
        Write-Host "  [跳过] 不存在：$d" -ForegroundColor DarkGray
        continue
    }
    $dst = Join-Path $appDir $d
    New-Item -ItemType Directory -Force $dst | Out-Null

    # robocopy 退出码 0/1 都算成功
    $null = robocopy $src $dst /E /XD $excludeDirs /XF *.pyc *.log *.gguf /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { throw "robocopy 复制 $d 失败，退出码 $LASTEXITCODE" }
}
foreach ($f in @("requirements.txt", ".env.example", "README.md")) {
    $src = Join-Path $RepoRoot $f
    if (Test-Path $src) { Copy-Item $src (Join-Path $appDir $f) -Force }
}
Write-Ok "代码复制完成"

# ── 2. 收入模型与推理引擎 ─────────────────────────────────────────────
Write-Step "收入模型与 llama-server"
$modelsSrc = Join-Path $OfflineDir "models"
$binSrc    = Join-Path $OfflineDir "bin"

if (Test-Path $modelsSrc) {
    Copy-Item $modelsSrc (Join-Path $OutDir "models") -Recurse -Force
    $gguf = Get-ChildItem (Join-Path $OutDir "models") -Filter *.gguf -ErrorAction SilentlyContinue
    if ($gguf) {
        foreach ($g in $gguf) { Write-Ok ("模型 {0} ({1} GB)" -f $g.Name, [math]::Round($g.Length / 1GB, 2)) }
    } else {
        Write-Host "  [警告] models\ 下没有 .gguf，先跑 fetch_model.ps1" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [警告] 没有 models\ 目录，先跑 fetch_model.ps1" -ForegroundColor Yellow
}

if (Test-Path $binSrc) {
    Copy-Item $binSrc (Join-Path $OutDir "bin") -Recurse -Force
    Write-Ok "llama-server 及同版本 DLL 已收入"
} else {
    Write-Host "  [警告] 没有 bin\ 目录，先跑 fetch_model.ps1" -ForegroundColor Yellow
}

# ── 3. 预下载 Python 依赖 ─────────────────────────────────────────────
if (-not $SkipWheels) {
    Write-Step "预下载 Python 依赖（目标机免联网安装）"
    $wheelsDir = Join-Path $OutDir "wheels"
    New-Item -ItemType Directory -Force $wheelsDir | Out-Null

    $py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $py)) { $py = (Get-Command python -ErrorAction SilentlyContinue).Source }
    $req = Join-Path $RepoRoot "backend\requirements.txt"
    $voiceReq = Join-Path $RepoRoot "voice_service\requirements.txt"

    foreach ($r in @($req, $voiceReq)) {
        if (-not (Test-Path $r)) { continue }
        Write-Host "  $r"
        & $py -m pip download --only-binary=:all: --dest $wheelsDir -r $r
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [警告] 部分依赖下载失败，目标机可能需要联网补装。" -ForegroundColor Yellow
        }
    }
    $n = (Get-ChildItem $wheelsDir -File -ErrorAction SilentlyContinue | Measure-Object).Count
    Write-Ok "已预下载 $n 个 wheel"
} else {
    Write-Host "  [跳过] pip download" -ForegroundColor DarkGray
}

# ── 4. 生成入口脚本 ───────────────────────────────────────────────────
Write-Step "生成 RUN.ps1"

$runPs1 = @'
<#
    README: 离线包入口。用法：
        powershell -ExecutionPolicy Bypass -File RUN.ps1
        powershell -ExecutionPolicy Bypass -File RUN.ps1 -Check     # 先自检

    首次运行会自动建 .venv 并从 wheels\ 离线装依赖，然后拉起 llama-server 与后端。
    需要目标机已安装 Python 3.12。
#>
[CmdletBinding()]
param([switch]$Check)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$App  = Join-Path $Root "app"

try { chcp 65001 | Out-Null } catch {}
$env:PYTHONUTF8 = "1"

# ── 建虚拟环境 ────────────────────────────────────────────────────────
$venv = Join-Path $Root ".venv"
$py   = Join-Path $venv "Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "`n[首次运行] 建虚拟环境..." -ForegroundColor Cyan
    $sys = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $sys) { throw "找不到 Python。本离线包需要目标机安装 Python 3.12。" }
    & $sys -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "建虚拟环境失败。" }
}

# ── 离线装依赖 ────────────────────────────────────────────────────────
$marker = Join-Path $venv ".deps-installed"
if (-not (Test-Path $marker)) {
    $wheels = Join-Path $Root "wheels"
    Write-Host "`n[首次运行] 离线安装依赖..." -ForegroundColor Cyan
    $reqs = @()
    foreach ($r in @("backend\requirements.txt", "voice_service\requirements.txt")) {
        $p = Join-Path $App $r
        if (Test-Path $p) { $reqs += $r }
    }
    foreach ($r in $reqs) {
        $p = Join-Path $App $r
        if (Test-Path $wheels) {
            & $py -m pip install --no-index --find-links $wheels -r $p
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  [警告] 离线安装失败，试联网安装..." -ForegroundColor Yellow
                & $py -m pip install -r $p
            }
        } else {
            & $py -m pip install -r $p
        }
        if ($LASTEXITCODE -ne 0) { throw "安装 $r 失败。" }
    }
    Set-Content -Path $marker -Value "ok" -Encoding UTF8
}

# ── 环境变量 ──────────────────────────────────────────────────────────
$envFile = Join-Path $App "scripts\offline\.env.offline"
if (-not (Test-Path $envFile)) {
    $example = Join-Path $App "scripts\offline\.env.offline.example"
    if (Test-Path $example) { Copy-Item $example $envFile }
}
$env:OFFLINE_MODE = "1"
if (-not $env:DEEPSEEK_API_KEY) { $env:DEEPSEEK_API_KEY = "sk-local" }
if (-not $env:EMBEDDING_BACKEND) { $env:EMBEDDING_BACKEND = "hash" }

# ── 交给 start.ps1 ────────────────────────────────────────────────────
$start = Join-Path $App "scripts\offline\start.ps1"
if (-not (Test-Path $start)) { throw "包里缺 app\scripts\offline\start.ps1。" }

# start.ps1 按相对仓库根定位，这里把包根当仓库根用
$startArgs = @("-ExecutionPolicy", "Bypass", "-File", $start, "-NoBrowser")
if ($Check) { $startArgs += "-Check" }
& powershell @startArgs
'@

Set-Content -Path (Join-Path $OutDir "RUN.ps1") -Value $runPs1 -Encoding UTF8
Write-Ok "RUN.ps1 已生成"

# ── 5. 汇总 ───────────────────────────────────────────────────────────
Write-Step "汇总"
$totalGB = [math]::Round(((Get-ChildItem $OutDir -Recurse -File |
    Measure-Object -Property Length -Sum).Sum / 1GB), 2)
Write-Host "`n  输出目录 $OutDir"
Write-Host "  总大小   $totalGB GB"
Get-ChildItem $OutDir | ForEach-Object {
    $size = if ($_.PSIsContainer) {
        [math]::Round(((Get-ChildItem $_.FullName -Recurse -File |
            Measure-Object -Property Length -Sum).Sum / 1MB), 1).ToString() + " MB"
    } else { "文件" }
    Write-Host ("    {0,-14} {1}" -f $_.Name, $size)
}
Write-Host "`n[下一步] 把整个 dist 目录拷到目标机，跑：" -ForegroundColor Green
Write-Host "    powershell -ExecutionPolicy Bypass -File RUN.ps1" -ForegroundColor Green
