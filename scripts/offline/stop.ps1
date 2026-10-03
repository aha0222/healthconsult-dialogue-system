<#
.SYNOPSIS
    停掉 start.ps1 拉起的本地服务（成员 B）。

.DESCRIPTION
    Windows 上脚本的 finally 在 Ctrl+C 时不一定执行得到，
    所以留这个兜底：按 logs\ 下的 pid 文件把 llama-server / voice_service 收掉。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\offline\stop.ps1
#>
[CmdletBinding()]
param()

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

if ($stopped -eq 0) {
    Write-Host "  没有需要停止的进程。" -ForegroundColor DarkGray
} else {
    Write-Host "`n[完成] 停止 $stopped 个进程。" -ForegroundColor Cyan
}
