<#
.SYNOPSIS
    一键离线冷启动：拉起本地 llama-server + 后端 + 前端（成员 B）。

.DESCRIPTION
    拔网线也能跑完整。默认行为：
      · 自动挑一个空闲端口给 llama-server（8080 常被别的服务占用）
      · 起 llama-server，日志写 logs\（stdout / stderr 分开）
      · 轮询 /health 等模型就绪，期间盯着子进程有没有已经挂掉
      · 前台起后端；Ctrl+C 退出时自动收掉 llama-server
      · 只有 VOICE_ENABLED=1 才拉 voice_service

.PARAMETER Port
    llama-server 端口。不给就自动挑空闲的。

.PARAMETER BackendPort
    后端 uvicorn 端口，默认 8000。

.PARAMETER NoBrowser
    不自动打开浏览器。

.PARAMETER Check
    起来之前先跑一遍 tools\offline_check.py 自检。

.PARAMETER Gpu
    用 GPU 档引擎（bin-cuda\llama-server.exe）与 7B 模型（models-7b\），
    全部层上显存、KV 缓存量化 q8_0，单轮问答从 CPU 档的数分钟降到约 8 秒。
    不给则沿用 CPU 档（bin\ + models\ 里最大的 GGUF）。

    **缺 GPU 资产时直接报错退出**，不会静默退回 CPU 档——静默降级会让人以为
    "GPU 档跑起来了"，实际一轮要数分钟。确实要用 CPU 档请显式加
    -AllowCpuFallback。

.PARAMETER AllowCpuFallback
    允许 -Gpu 在缺 bin-cuda\ / models-7b\ 资产时退回 CPU 档。默认不允许。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\offline\start.ps1
    powershell -ExecutionPolicy Bypass -File scripts\offline\start.ps1 -Gpu
    powershell -ExecutionPolicy Bypass -File scripts\offline\start.ps1 -Check -Gpu
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [int]$BackendPort = 8000,
    [switch]$NoBrowser,
    [switch]$Check,
    [switch]$Gpu,
    [switch]$AllowCpuFallback,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

# 中文日志别乱码
try { chcp 65001 | Out-Null } catch {}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$OfflineDir = $PSScriptRoot
$RepoRoot   = (Resolve-Path (Join-Path $OfflineDir "..\..")).Path
$LogDir     = Join-Path $OfflineDir "logs"
$PidFile    = Join-Path $LogDir "llama.pid"
New-Item -ItemType Directory -Force $LogDir | Out-Null

function Write-Step($m) { Write-Host "`n[启动] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [就绪] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  [注意] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [失败] $m" -ForegroundColor Red }

# ── 0. 读离线档配置 ───────────────────────────────────────────────────
function Read-EnvFile([string]$path) {
    $cfg = @{}
    if (-not (Test-Path $path)) { return $cfg }
    foreach ($line in Get-Content $path -Encoding UTF8) {
        $t = $line.Trim()
        if (-not $t -or $t.StartsWith("#") -or -not $t.Contains("=")) { continue }
        $i = $t.IndexOf("=")
        $k = $t.Substring(0, $i).Trim()
        $v = $t.Substring($i + 1).Trim()
        if ($k) { $cfg[$k] = $v }
    }
    return $cfg
}

$envFile = Join-Path $OfflineDir ".env.offline"
if (-not (Test-Path $envFile)) {
    $example = Join-Path $OfflineDir ".env.offline.example"
    if (Test-Path $example) {
        Copy-Item $example $envFile
        Write-Warn2 "未找到 .env.offline，已从 .example 复制一份。"
    }
}
$cfg = Read-EnvFile $envFile

# ── 1. 找能用的 Python ────────────────────────────────────────────────
# 注意：venv 不一定在仓库目录下（本机就在 F 盘而仓库在 E 盘），
# 所以按优先级找多个位置，并**在启动前验证依赖**——否则会一路跑到
# uvicorn 才报 "No module named uvicorn"，很难定位。
function Test-HasDeps([string]$exe) {
    if (-not $exe -or -not (Test-Path $exe)) { return $false }
    # PowerShell 5.1 在 $ErrorActionPreference="Stop" 下会把原生命令的 stderr
    # 当成终止错误，于是 "No module named uvicorn" 会先弹一段 traceback。
    # 这里临时降级，只看退出码。
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        & $exe -c "import uvicorn, fastapi" 2>&1 | Out-Null
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $prev
    }
}

# 上次成功用过的解释器会被记住，之后不用再带 -Python
$savedFile = Join-Path $OfflineDir ".python_path"
$saved = if (Test-Path $savedFile) { (Get-Content $savedFile -EA SilentlyContinue | Select-Object -First 1) } else { "" }

$candidates = @()
if ($Python)              { $candidates += $Python }
if ($env:XIAONUAN_VENV)   { $candidates += (Join-Path $env:XIAONUAN_VENV "Scripts\python.exe") }
if ($saved)               { $candidates += $saved }
$candidates += (Join-Path $RepoRoot ".venv\Scripts\python.exe")
$candidates += (Join-Path (Split-Path $RepoRoot -Parent) ".venv\Scripts\python.exe")
$onPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($onPath) { $candidates += $onPath }

$venvPython = $null
foreach ($c in $candidates) {
    if (Test-HasDeps $c) { $venvPython = $c; break }
}

if (-not $venvPython) {
    Write-Err "找不到装了依赖的 Python 解释器。试过这些位置："
    foreach ($c in $candidates) {
        $exists = if (Test-Path $c) { "存在但缺 uvicorn/fastapi" } else { "不存在" }
        Write-Host "      $c  —— $exists" -ForegroundColor DarkGray
    }
    Write-Host @"

  三条出路：
    1) 建好环境：powershell -ExecutionPolicy Bypass -File scripts\run.ps1 -SetupOnly
    2) 指定解释器：-Python <path\to\python.exe>
    3) 设环境变量：`$env:XIAONUAN_VENV = "<venv 目录>"

  本机实测：venv 在 F 盘而仓库在 E 盘，所以默认路径找不到，需要 2) 或 3)。
"@ -ForegroundColor Yellow
    exit 1
}
Write-Host "  解释器 $venvPython" -ForegroundColor DarkGray
# 记住它，下次跑就不用再指定了
if ($saved -ne $venvPython) {
    Set-Content -Path $savedFile -Value $venvPython -Encoding UTF8
    Write-Host "  （已记住，下次直接跑 start.ps1 即可）" -ForegroundColor DarkGray
}

# ── 2. 挑端口 ─────────────────────────────────────────────────────────
function Test-PortFree([int]$p) {
    # 真正去 bind 一次，比查监听列表可靠：能同时排除"被占用但没在 Listen"的情况
    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $p)
        $listener.Start()
        return $true
    } catch {
        return $false
    } finally {
        if ($listener) { $listener.Stop() }
    }
}

if ($Port -eq 0) {
    # GPU 档默认 8090：与前端/文档里记的离线端点一致，也避开常见的 8080 占用
    $Port = if ($Gpu) { 8090 } else { 8080 }
    while (-not (Test-PortFree $Port)) {
        Write-Warn2 "端口 $Port 已被占用，试下一个。"
        $Port++
        if ($Port -gt 8099) { throw "8080-8099 全被占用，请用 -Port 指定其他端口。" }
    }
} elseif (-not (Test-PortFree $Port)) {
    throw "指定端口 $Port 已被占用。"
}
$Endpoint = "http://127.0.0.1:$Port"

# ── 3. 找 llama-server 与模型 ─────────────────────────────────────────
# GPU 档优先用 bin-cuda\ 引擎与 models-7b\ 的 7B 模型；缺失则退回 CPU 档并提示。
$engineDir = if ($Gpu) { "bin-cuda" } else { "bin" }
$modelDir  = if ($Gpu) { "models-7b" } else { "models" }

$serverExe = Join-Path $OfflineDir "$engineDir\llama-server.exe"
if (-not (Test-Path $serverExe)) {
    if ($Gpu) {
        if (-not $AllowCpuFallback) {
            throw ("-Gpu 要求 GPU 档，但找不到 $engineDir\llama-server.exe。`n" +
                   "  缺资产时不会静默退回 CPU 档——那样会让人以为跑的是 GPU 档，" +
                   "实际一轮从约 8 秒变成数分钟。`n" +
                   "  补 GPU 引擎：python scripts\offline\download_cudart.py`n" +
                   "  并确认 bin-cuda\ 与 models-7b\ 都存在。`n" +
                   "  确实要用 CPU 档：显式加 -AllowCpuFallback。")
        }
        Write-Warn2 "找不到 $engineDir\llama-server.exe，按 -AllowCpuFallback 退回 CPU 档引擎（bin\）。"
        $engineDir = "bin"
        $serverExe = Join-Path $OfflineDir "bin\llama-server.exe"
    }
    if (-not (Test-Path $serverExe)) {
        $cmd = Get-Command llama-server -ErrorAction SilentlyContinue
        if ($cmd) { $serverExe = $cmd.Source } else { $serverExe = $null }
    }
}

$modelPath = $cfg["OFFLINE_LLM_MODEL_PATH"]
if (-not $modelPath) {
    $found = $null
    if ($Gpu) {
        # 7B 是分片 GGUF：必须传第一片（-00001-of-…），后续分片由 llama.cpp 自动加载
        $found = Get-ChildItem (Join-Path $OfflineDir $modelDir) -Filter "*-00001-of-*.gguf" -ErrorAction SilentlyContinue |
                 Select-Object -First 1
    }
    if (-not $found) {
        $found = Get-ChildItem (Join-Path $OfflineDir $modelDir) -Filter *.gguf -ErrorAction SilentlyContinue |
                 Sort-Object Length -Descending | Select-Object -First 1
    }
    if ($found) { $modelPath = $found.FullName }
    if ($Gpu -and -not $found) {
        if (-not $AllowCpuFallback) {
            throw ("-Gpu 要求 7B 档，但 $modelDir\ 里没有 GGUF 模型。`n" +
                   "  缺资产时不会静默退回 models\ 的 3B——那样会让人以为跑的是" +
                   "7B/GPU 档。`n" +
                   "  补模型：powershell -ExecutionPolicy Bypass -File " +
                   "scripts\offline\fetch_model.ps1`n" +
                   "  确实要用 CPU 档：显式加 -AllowCpuFallback。")
        }
        Write-Warn2 "models-7b\ 里没有 GGUF 模型，按 -AllowCpuFallback 退回 models\。"
        $found = Get-ChildItem (Join-Path $OfflineDir "models") -Filter *.gguf -ErrorAction SilentlyContinue |
                 Sort-Object Length -Descending | Select-Object -First 1
        if ($found) { $modelPath = $found.FullName }
    }
}

$llamaProcess = $null
$llamaStarted = $false

if (-not $serverExe -or -not $modelPath -or -not (Test-Path $modelPath)) {
    Write-Warn2 "缺少 llama-server.exe 或 GGUF 模型，跳过本地端点。"
    Write-Warn2 "先跑：powershell -ExecutionPolicy Bypass -File scripts\offline\fetch_model.ps1"
    Write-Warn2 "后端将按 OFFLINE_LLM_BACKEND 尝试进程内后端；也不可用则明确报错（不会回退云端）。"
} else {
    # 上次跑完没收干净时直接复用，避免重复占端口／重复加载 2GB 模型
    $reuse = $false
    $portFile = Join-Path $LogDir "llama.port"
    if ((Test-Path $PidFile) -and (Test-Path $portFile)) {
        $oldPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($oldPid -and (Get-Process -Id $oldPid -ErrorAction SilentlyContinue)) {
            $oldPort = (Get-Content $portFile -ErrorAction SilentlyContinue | Select-Object -First 1)
            if ($oldPort) {
                $reuse = $true
                $Port = [int]$oldPort
                $Endpoint = "http://127.0.0.1:$Port"
                Write-Step "复用已在运行的 llama-server（PID $oldPid，端口 $Port）"
            }
        }
    }

    if (-not $reuse) {
        Write-Step "启动 llama-server（端口 $Port）"
        Write-Host "  模型 $modelPath"
        Write-Host "  引擎 $serverExe"

        # 关键：SKILL.md 全文约 10.5k 字符，光 system prompt 就 7–10k token。
        # -c 给小了会截断甚至报错。
        $ctx     = if ($cfg["OFFLINE_LLM_CTX"])     { $cfg["OFFLINE_LLM_CTX"] }     else { "16384" }
        $threads = if ($cfg["OFFLINE_LLM_THREADS"]) { $cfg["OFFLINE_LLM_THREADS"] } else { "8" }
        $llamaArgs = @(
            "-m", $modelPath,
            "-c", $ctx,
            "-t", $threads, "-tb", $threads,
            "--host", "127.0.0.1", "--port", "$Port",
            "--parallel", "1",
            "--jinja"
        )
        if ($Gpu) {
            # 全部层上显存 + KV 缓存量化到 q8_0，8GB 显存够放 7B + 16k 上下文
            $llamaArgs += @("-ngl", "99", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0")
        }
        # 不加 --no-mmap：mmap 启动更快，内存交给系统缓存

        $outLog = Join-Path $LogDir "llama.out.log"
        $errLog = Join-Path $LogDir "llama.err.log"
        # stdout / stderr 不能指向同一个文件，否则 Start-Process 直接报错
        $llamaProcess = Start-Process -FilePath $serverExe -ArgumentList $llamaArgs `
            -PassThru -NoNewWindow -RedirectStandardOutput $outLog -RedirectStandardError $errLog
        $llamaStarted = $true
        Set-Content -Path $PidFile -Value $llamaProcess.Id -Encoding UTF8
        Set-Content -Path $portFile -Value "$Port" -Encoding UTF8

        Write-Host "  等待模型就绪（首次加载慢，最多 180 秒）..."
        $deadline = (Get-Date).AddSeconds(180)
        $ready = $false
        while ((Get-Date) -lt $deadline) {
            if ($llamaProcess.HasExited) {
                Write-Err "llama-server 已退出（退出码 $($llamaProcess.ExitCode)）。错误日志末尾："
                Get-Content $errLog -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { "      $_" }
                throw "llama-server 启动失败，详见 $errLog"
            }
            try {
                $r = Invoke-WebRequest -Uri "$Endpoint/health" -TimeoutSec 2 -UseBasicParsing
                if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 300) { $ready = $true; break }
            } catch {
                # 加载中会返 503；也可能还没开始监听
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $ready) {
            Write-Err "等待超时。日志末尾："
            Get-Content $errLog -Tail 25 -ErrorAction SilentlyContinue | ForEach-Object { "      $_" }
            throw "llama-server 未在 180 秒内就绪。"
        }
        Write-Ok "llama-server 就绪：$Endpoint"
    }
}

# ── 4. 组装后端环境变量 ───────────────────────────────────────────────
# .env.offline 的值先落进环境；随后用命令行/自动挑选的值覆盖。
foreach ($k in $cfg.Keys) {
    if ($k.StartsWith("_")) { continue }
    if ($cfg[$k]) { Set-Item -Path "env:$k" -Value $cfg[$k] }
}
$env:OFFLINE_MODE = "1"
$env:OFFLINE_LLM_BACKEND = if ($cfg["OFFLINE_LLM_BACKEND"]) { $cfg["OFFLINE_LLM_BACKEND"] } else { "auto" }
# 挑到的端口覆盖配置文件里的固定值——这是端口冲突能自动化解的关键
$env:OFFLINE_LLM_ENDPOINT = $Endpoint
if (-not $env:DEEPSEEK_API_KEY) { $env:DEEPSEEK_API_KEY = "sk-local" }
if (-not $env:EMBEDDING_BACKEND) { $env:EMBEDDING_BACKEND = "hash" }

# ── 5. 可选：语音服务 ─────────────────────────────────────────────────
$voiceProcess = $null
if ($env:VOICE_ENABLED -eq "1") {
    Write-Step "启动 voice_service（VOICE_ENABLED=1）"
    $voiceLog = Join-Path $LogDir "voice.out.log"
    $voiceErr = Join-Path $LogDir "voice.err.log"
    $voiceProcess = Start-Process -FilePath $venvPython -ArgumentList @("-m", "voice_service") `
        -WorkingDirectory $RepoRoot -PassThru -NoNewWindow `
        -RedirectStandardOutput $voiceLog -RedirectStandardError $voiceErr
    Set-Content -Path (Join-Path $LogDir "voice.pid") -Value $voiceProcess.Id -Encoding UTF8
    Write-Ok "voice_service 已拉起（日志 $voiceLog）"
}

# ── 6. 可选：先自检 ───────────────────────────────────────────────────
if ($Check) {
    Write-Step "离线自检"
    & $venvPython (Join-Path $RepoRoot "tools\offline_check.py")
    if ($LASTEXITCODE -ne 0) {
        Write-Warn2 "自检未全过（退出码 $LASTEXITCODE）。仍继续启动，便于你排查。"
    }
}

# ── 7. 前台起后端 ─────────────────────────────────────────────────────
Write-Step "启动后端 http://127.0.0.1:$BackendPort"
$frontend = Join-Path $RepoRoot "frontend\chat.html"
Write-Host "  前端页面 $frontend"
if (-not $NoBrowser) {
    Start-Process $frontend | Out-Null
}

$backendExit = 0
try {
    & $venvPython -m uvicorn backend.app.main:app --host 127.0.0.1 --port $BackendPort
    $backendExit = $LASTEXITCODE
    if ($null -eq $backendExit) { $backendExit = 0 }
    if ($backendExit -ne 0) { Write-Err "后端退出，退出码 $backendExit（错误见上方输出）。" }
} finally {
    Write-Host "`n[收尾] 停止子进程..." -ForegroundColor Cyan
    foreach ($p in @($voiceProcess, $llamaProcess)) {
        if ($p -and -not $p.HasExited) {
            Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            Write-Host "  已停止 PID $($p.Id)"
        }
    }
    if ($llamaStarted -and (Test-Path $PidFile)) { Remove-Item $PidFile -Force -ErrorAction SilentlyContinue }
    Write-Host "  完成"
}

# 后端非正常退出时，脚本也要返回非零——否则 CI / 上层脚本会以为启动成功了
exit $backendExit
