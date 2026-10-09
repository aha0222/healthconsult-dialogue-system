<#
.SYNOPSIS
    停掉 start.ps1 拉起的本地服务（成员 B）。

.DESCRIPTION
    Windows 上脚本的 finally 在 Ctrl+C 时不一定执行得到，
    所以留这个兜底：按 logs\ 下的 pid 文件把 llama-server / voice_service 收掉。

    后端也要停。它是 start.ps1 的**前台**进程，没有 pid 文件，只能按端口找；
    漏掉它的话，"关掉再重起"会失败——端口仍被占着，下次 start.ps1 绑不上。
    而"重启后端"是离线端点卡死时唯一的恢复手段（见
    docs/local_llm_endpoint_wedge_report.md）。

.PARAMETER BackendPort
    后端监听端口，默认 8000，与 start.ps1 的 -BackendPort 保持一致。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\offline\stop.ps1
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8000
)

$LogDir  = Join-Path $PSScriptRoot "logs"
$stopped = 0

foreach ($name in @("llama", "voice")) {
    $pidFile = Join-Path $LogDir "$name.pid"
    if (-not (Test-Path $pidFile)) { continue }
    $procId = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($procId -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Write-Host "  已停止 $name (PID $procId)" -ForegroundColor Green
        $stopped++
    } else {
        Write-Host "  $name 的 PID $procId 已不在运行" -ForegroundColor DarkGray
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

Remove-Item (Join-Path $LogDir "llama.port") -Force -ErrorAction SilentlyContinue

# ── 后端：没有 pid 文件，按端口找 ────────────────────────────────────
$backendPids = @()
try {
    $backendPids = Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction Stop |
                   Select-Object -ExpandProperty OwningProcess -Unique
} catch {
    # 没有 Get-NetTCPConnection 的环境，回退解析 netstat
    $backendPids = netstat -ano | Select-String ":$BackendPort\s+.*LISTENING" |
                   ForEach-Object { ($_.ToString() -split '\s+')[-1] } |
                   Sort-Object -Unique
}
foreach ($procId in $backendPids) {
    if (-not $procId) { continue }
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if (-not $proc) { continue }
    if ($proc.ProcessName -ne "python") {
        # 端口被别的程序占了，不是我们的后端——不乱杀
        Write-Host "  端口 $BackendPort 上是 $($proc.ProcessName) (PID $procId)，不是后端，跳过" -ForegroundColor Yellow
        continue
    }
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    Write-Host "  已停止 backend (PID $procId)" -ForegroundColor Green
    $stopped++
}

if ($stopped -eq 0) {
    Write-Host "  没有需要停止的进程。" -ForegroundColor DarkGray
} else {
    Write-Host "`n[完成] 停止 $stopped 个进程。" -ForegroundColor Cyan
}
