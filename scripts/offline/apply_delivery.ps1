<#
.SYNOPSIS
    一键把成员 B 的离线改动接到仓库里（收件人跑这一个就行）。

.DESCRIPTION
    做三件事：
      1) 前置检查：确认当前目录是仓库根、Python 可用
      2) 应用两个补丁（0001 R2b 修复 → 0002 离线接线），顺序不能反
      3) 报告下一步

    不下载模型——那是 2GB，用 -Fetch 显式触发，或单独跑 fetch_model.ps1。

    补丁用自带的 apply_patch.py 应用（纯 Python，不依赖 git / patch 命令）。
    脚本是幂等的：已经应用过的补丁会被识别并跳过。

.PARAMETER Check
    只检查补丁能否干净应用，不写任何文件。

.PARAMETER Fetch
    顺带跑一次 fetch_model.ps1 下载推理引擎与模型（约 2GB，需联网）。

.PARAMETER Python
    Python 解释器路径。默认自动找（PATH 上的 python、或仓库 .venv）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\offline\apply_delivery.ps1
    powershell -ExecutionPolicy Bypass -File scripts\offline\apply_delivery.ps1 -Check
    powershell -ExecutionPolicy Bypass -File scripts\offline\apply_delivery.ps1 -Fetch
#>
[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$Fetch,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
try { chcp 65001 | Out-Null } catch {}
$env:PYTHONUTF8 = "1"

$OfflineDir = $PSScriptRoot
$RepoRoot   = (Resolve-Path (Join-Path $OfflineDir "..\..")).Path

function Write-Step($m) { Write-Host "`n[步骤] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [完成] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  [注意] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [失败] $m" -ForegroundColor Red }

Write-Host "=" * 72
Write-Host "  成员 B 离线改动 · 一键接入"
Write-Host "=" * 72
Write-Host "  仓库根 $RepoRoot"

# ── 1. 前置检查 ───────────────────────────────────────────────────────
Write-Step "前置检查"

$mustHave = @(
    "backend\app\dialogue\orchestrator.py",
    "backend\app\dialogue\llm_client.py",
    "backend\app\main.py",
    "backend\app\providers\__init__.py",
    "tools\offline_check.py"
)
$missing = $mustHave | Where-Object { -not (Test-Path (Join-Path $RepoRoot $_)) }
if ($missing) {
    Write-Err "仓库里缺少这些文件："
    $missing | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    Write-Host "`n  两种可能：" -ForegroundColor Yellow
    Write-Host "      · 本包还没解压覆盖到仓库根 → 先解压" -ForegroundColor Yellow
    Write-Host "      · 或者当前目录不是仓库根 → 先 cd 过去" -ForegroundColor Yellow
    exit 1
}
Write-Ok "目录结构正确"

# providers/__init__.py 必须是「已扩 get_llm(settings=None)」的那版，
# 否则补丁里 get_llm(self.settings) 会 TypeError
$provInit = Get-Content (Join-Path $RepoRoot "backend\app\providers\__init__.py") -Raw
if ($provInit -notmatch "def get_llm\(settings") {
    Write-Err "backend/app/providers/__init__.py 不是本包的版本。"
    Write-Host "      补丁会把调用点改成 get_llm(self.settings)，而当前实现不接受参数。" -ForegroundColor Yellow
    Write-Host "      请先解压覆盖包内的 providers/__init__.py 再重跑。" -ForegroundColor Yellow
    exit 1
}
Write-Ok "providers/__init__.py 版本正确"

# ── 2. 找 Python ──────────────────────────────────────────────────────
# 两种用途要求不同，分开找：
#   · 应用补丁  → 任意 Python 3.8+ 即可（apply_patch.py 只用标准库）
#   · 跑测试    → 必须装了 pytest；没有就跳过，不能崩
function Test-PyVer([string]$exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $exe -c "import sys; assert sys.version_info >= (3, 8)" 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally { $ErrorActionPreference = $prev }
}

function Test-PyModule([string]$exe, [string]$mod) {
    if (-not (Test-PyVer $exe)) { return $false }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $exe -c "import $mod" 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally { $ErrorActionPreference = $prev }
}

$cands = @()
if ($Python) { $cands += $Python }
$savedFile = Join-Path $OfflineDir ".python_path"
if (Test-Path $savedFile) { $cands += (Get-Content $savedFile -EA SilentlyContinue | Select-Object -First 1) }
$cands += (Join-Path $RepoRoot ".venv\Scripts\python.exe")
$cands += (Join-Path (Split-Path $RepoRoot -Parent) ".venv\Scripts\python.exe")
$p = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($p) { $cands += $p }

$py = $null
foreach ($c in $cands) { if (Test-PyVer $c) { $py = $c; break } }
if (-not $py) {
    Write-Err "找不到可用的 Python 3.8+。补丁应用器需要它。"
    Write-Host "      用 -Python <path> 指定即可。" -ForegroundColor Yellow
    exit 1
}
Write-Ok "应用补丁用 Python：$py（只需标准库）"

# config.py 必须有 offline_mode —— 这是 Day0 契约 stub 带来的字段。
# 没有的话 getattr(settings, "offline_mode", False) 恒为假，
# OFFLINE_MODE=1 会被**静默忽略**，离线根本不生效，而且不会有任何报错。
#
# 用**行为检查**而不是字符串匹配：实例化一次 Settings 看字段在不在。
# 字符串匹配会漏（比如有人写了个 offline_mode_disabled 也含子串）。
# 注意：这段必须在选定 $py 之后跑。
$probe = @"
import sys
sys.path.insert(0, r'$RepoRoot')
try:
    from backend.app.config import Settings
    s = Settings()
except Exception as exc:
    print('IMPORT_FAIL:' + type(exc).__name__ + ':' + str(exc)[:80]); raise SystemExit(0)
print('HAS_FIELD' if hasattr(s, 'offline_mode') else 'NO_FIELD')
"@
$probeOut = ""
$prev = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
try {
    $probeOut = (& $py -c $probe 2>&1 | Select-Object -Last 1)
} finally { $ErrorActionPreference = $prev }

if ($probeOut -notmatch "HAS_FIELD") {
    Write-Err "backend/app/config.py 的 Settings 里没有 offline_mode 字段。"
    if ($probeOut -match "IMPORT_FAIL") {
        Write-Host "      （探测时连 Settings 都导入不了：$probeOut）" -ForegroundColor DarkGray
    }
    Write-Host "`n      这说明仓库还没应用 Day0 契约 stub（组长那一版 config.py）。" -ForegroundColor Yellow
    Write-Host "      不加这个字段，OFFLINE_MODE=1 会被静默忽略，离线模式不会生效，" -ForegroundColor Yellow
    Write-Host "      而且不会有任何报错——所以这里必须拦住。" -ForegroundColor Yellow
    Write-Host "`n      怎么办：先 git pull 拿到组长的 契约stub/，按里面的说明覆盖，" -ForegroundColor White
    Write-Host "      或直接找组长要那一版 config.py。" -ForegroundColor White
    exit 1
}
Write-Ok "config.py 有 offline_mode（Day0 stub 已应用）"

# 跑测试单独找：优先用上面那个，不行再挨个试谁能 import pytest
$pyTest = $null
foreach ($c in (@($py) + $cands)) {
    if (Test-PyModule $c "pytest") { $pyTest = $c; break }
}

# ── 3. 应用补丁 ───────────────────────────────────────────────────────
$patches = @(
    @{ File = "0001-fix-r2b-case-mismatch.patch";        Desc = "R2b 大小写失配（应用 Day0 stub 后必现）" },
    @{ File = "0002-wire-get-llm-into-call-sites.patch"; Desc = "离线接线（不打这个离线用不了）" }
)

$fail = $false
foreach ($p in $patches) {
    $path = Join-Path $OfflineDir "patches\$($p.File)"
    if (-not (Test-Path $path)) { Write-Err "缺补丁文件 $($p.File)"; $fail = $true; continue }

    Write-Step "$($p.Desc)"
    $args = @((Join-Path $OfflineDir "apply_patch.py"), $path, "--root", $RepoRoot)
    if ($Check) { $args += "--check" }
    & $py @args
    if ($LASTEXITCODE -ne 0) {
        Write-Err "补丁 $($p.File) 应用失败。"
        Write-Host "      若目标文件已被别人改过，建议 git stash 后重试。" -ForegroundColor Yellow
        $fail = $true
        break
    }
}

if ($fail) { exit 1 }

if ($Check) {
    Write-Host "`n  检查模式：没有写任何文件。" -ForegroundColor Cyan
    Write-Host "  去掉 -Check 即可真正应用。`n"
    exit 0
}

# ── 4. 验证 ───────────────────────────────────────────────────────────
Write-Step "验证（跑测试）"
if (-not $pyTest) {
    Write-Warn2 "没找到装了 pytest 的解释器，跳过测试。"
    Write-Host "      这不是失败——补丁已经应用好了。想跑测试的话：" -ForegroundColor Gray
    Write-Host "        · 先建环境：powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -SetupOnly" -ForegroundColor Gray
    Write-Host "        · 或用 -Python <venv>\Scripts\python.exe 指定已建好的虚拟环境" -ForegroundColor Gray
} else {
    Push-Location $RepoRoot
    try {
        & $pyTest -m pytest backend/tests -q 2>&1 | Select-Object -Last 3
        $ok = ($LASTEXITCODE -eq 0)
    } finally { Pop-Location }

    if ($ok) {
        Write-Ok "测试全绿"
    } else {
        Write-Warn2 "测试未全绿。补丁已应用，但请确认这是不是环境问题（缺依赖）。"
        Write-Host "      提示：Day0 契约 stub 本身会让 test_alerts.py::test_should_alert_by_risk 失败，" -ForegroundColor Gray
        Write-Host "      那正是 0001 补丁要修的——如果它还在失败，说明补丁没生效。" -ForegroundColor Gray
    }
}

# ── 5. 下一步 ─────────────────────────────────────────────────────────
Write-Step "下一步"
if ($Fetch) {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $OfflineDir "fetch_model.ps1")
} else {
    Write-Host "  1) 下载推理引擎与模型（约 2GB，需联网）：" -ForegroundColor White
    Write-Host "       powershell -ExecutionPolicy Bypass -File scripts\offline\fetch_model.ps1" -ForegroundColor Gray
}
Write-Host "  2) 冷启动：" -ForegroundColor White
Write-Host "       powershell -ExecutionPolicy Bypass -File scripts\offline\start.ps1" -ForegroundColor Gray
Write-Host "  3) 自检：" -ForegroundColor White
Write-Host "       python tools\offline_check.py" -ForegroundColor Gray
Write-Host "`n  关掉时用 Ctrl+C，或 stop.ps1。`n" -ForegroundColor DarkGray
