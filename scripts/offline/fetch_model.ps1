<#
.SYNOPSIS
    一次性下载离线包所需的模型与推理引擎（需要联网，只在打包时跑一次）。

.DESCRIPTION
    1) llama.cpp 的 Windows CPU 版二进制（含 llama-server.exe）—— 走 GitHub
    2) GGUF 量化模型（默认 Qwen2.5-3B-Instruct Q4_K_M，约 2GB）

    模型按下面的顺序自动挑可达的源：
        ModelScope（Qwen 官方，国内最快）
        hf-mirror.com
        huggingface.co（原站）
    实测国内网络下 huggingface.co 的 DNS 直接解析不了，所以不能只写原站。

    2GB 文件用的是**支持断点续传**的分段下载：中断后重跑会接着下，
    不会从头再来。落盘先用 .part 后缀，完整下载并校验后才改名。

    下载完会打印 SHA256。**校验需要你自己提供期望值**——脚本不硬编码
    一个来路不明的哈希假装校验过。HuggingFace / ModelScope 的文件页面
    能看到官方 sha256。

.PARAMETER ModelUrl
    自定义模型下载地址（内网镜像 / 已备好的离线副本）。给了就只用它。

.PARAMETER ExpectedModelSha256
    可选的模型 SHA256。给了就强校验，不匹配直接判失败。

.PARAMETER LlamaCppUrl
    自定义 llama.cpp Windows CPU 包地址。默认从 GitHub releases API 取最新版。

.PARAMETER Force
    已存在也重新下载。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\offline\fetch_model.ps1
    powershell -ExecutionPolicy Bypass -File scripts\offline\fetch_model.ps1 -ExpectedModelSha256 <sha256>
#>
[CmdletBinding()]
param(
    [string]$ModelUrl = "",
    [string]$ModelName = "Qwen2.5-3B-Instruct-Q4_K_M.gguf",
    [string]$ExpectedModelSha256 = "",
    [string]$LlamaCppUrl = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root     = $PSScriptRoot
$ModelDir = Join-Path $Root "models"
$BinDir   = Join-Path $Root "bin"

function Write-Step($m) { Write-Host "`n[步骤] $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [完成] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "  [注意] $m" -ForegroundColor Yellow }

New-Item -ItemType Directory -Force $ModelDir, $BinDir | Out-Null

# 候选源：国内网络下 huggingface.co 常常直接解析不了，必须多源回退
$Sources = @(
    [pscustomobject]@{ Name = "ModelScope"; Url = "https://www.modelscope.cn/models/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/master/qwen2.5-3b-instruct-q4_k_m.gguf" }
    [pscustomobject]@{ Name = "hf-mirror";  Url = "https://hf-mirror.com/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf" }
    [pscustomobject]@{ Name = "huggingface"; Url = "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf" }
)
if ($ModelUrl) {
    $Sources = @([pscustomobject]@{ Name = "自定义"; Url = $ModelUrl })
}

# ── GitHub 代理 ───────────────────────────────────────────────────────
# 实测：api.github.com 与 github.com 的网页能通，但 release 资产实际落在
# objects.githubusercontent.com，国内直连**超时**。走这些加速前缀才下得动。
$GhProxies = @(
    "https://gh-proxy.com/",
    "https://ghproxy.net/"
)

function Resolve-DownloadUrl([string]$url) {
    "`n  试直连..." | Write-Host
    if ((Get-RemoteSize $url) -gt 0) {
        Write-Ok "直连可用"
        return $url
    }
    Write-Warn2 "直连不可用（国内常见：release 资产被阻断），改走加速镜像。"
    foreach ($p in $GhProxies) {
        $cand = $p + $url
        Write-Host "  试 $p"
        if ((Get-RemoteSize $cand) -gt 0) {
            Write-Ok "可用：$p"
            return $cand
        }
        Write-Warn2 "$p 不可用"
    }
    return ""
}

# ── 断点续传下载 ──────────────────────────────────────────────────────
function Get-RemoteSize([string]$url) {
    # 注意用 GET + Range 而不是 HEAD：HF 的 LFS 走重定向，HEAD 经常直接超时
    $req = [System.Net.HttpWebRequest]::Create($url)
    $req.Method = "GET"
    $req.AddRange(0, 0)
    $req.Timeout = 30000
    $req.AllowAutoRedirect = $true
    $req.UserAgent = "xiaonuan-offline"
    try {
        $resp = $req.GetResponse()
        $range = $resp.Headers["Content-Range"]
        $resp.Close()
        if ($range -match "/(\d+)$") { return [long]$Matches[1] }
        return 0
    } catch {
        return -1
    }
}

function Save-FileResumable([string]$url, [string]$dest, [string]$label) {
    $part = "$dest.part"
    $attempt = 0
    $maxAttempts = 40

    while ($attempt -lt $maxAttempts) {
        $attempt++
        $have = if (Test-Path $part) { (Get-Item $part).Length } else { 0 }

        try {
            $req = [System.Net.HttpWebRequest]::Create($url)
            $req.Method = "GET"
            $req.Timeout = 60000
            $req.ReadWriteTimeout = 300000
            $req.AllowAutoRedirect = $true
            $req.UserAgent = "xiaonuan-offline"
            if ($have -gt 0) { $req.AddRange($have) }

            $resp = $req.GetResponse()
            $total = 0
            $range = $resp.Headers["Content-Range"]
            if ($range -match "/(\d+)$") { $total = [long]$Matches[1] }
            elseif ($resp.ContentLength -gt 0) { $total = $have + $resp.ContentLength }

            $mode = if ($have -gt 0) { [System.IO.FileMode]::Append } else { [System.IO.FileMode]::Create }
            $fs = [System.IO.File]::Open($part, $mode, [System.IO.FileAccess]::Write)
            $stream = $resp.GetResponseStream()
            $buf = New-Object byte[] (1MB)
            $lastReport = Get-Date
            try {
                while (($read = $stream.Read($buf, 0, $buf.Length)) -gt 0) {
                    $fs.Write($buf, 0, $read)
                    $have += $read
                    if (((Get-Date) - $lastReport).TotalSeconds -ge 5) {
                        $lastReport = Get-Date
                        if ($total -gt 0) {
                            $pct = [math]::Round($have * 100.0 / $total, 1)
                            Write-Host ("`r      {0}  {1}%  ({2:N2}/{3:N2} GB)" -f `
                                $label, $pct, ($have / 1GB), ($total / 1GB)) -NoNewline
                        } else {
                            Write-Host ("`r      {0}  {1:N2} GB" -f $label, ($have / 1GB)) -NoNewline
                        }
                    }
                }
            } finally {
                $fs.Close(); $stream.Close(); $resp.Close()
            }
            Write-Host ""

            if ($total -gt 0 -and $have -lt $total) {
                throw "连接提前结束：已下 $have / $total 字节"
            }
            Move-Item $part $dest -Force
            return $true
        } catch {
            Write-Host ""
            Write-Warn2 "第 $attempt 次中断：$($_.Exception.Message)"
            if ($attempt -ge $maxAttempts) { throw "重试 $maxAttempts 次仍未完成，请检查网络后重跑（会接着下）。" }
            Start-Sleep -Seconds ([Math]::Min(2 * $attempt, 15))
        }
    }
    return $false
}

# ── 1. llama.cpp 二进制（走 GitHub，实测可达）─────────────────────────
Write-Step "获取 llama.cpp Windows CPU 版"

$serverExe = Join-Path $BinDir "llama-server.exe"
if ((Test-Path $serverExe) -and -not $Force) {
    Write-Ok "已存在，跳过：$serverExe"
} else {
    if (-not $LlamaCppUrl) {
        # 不要用 /releases/latest —— llama.cpp 那个是占位版（v0.5.0，
        # 只有 nightly-tag.txt 一个资产）。真正的二进制发布在 b<编号> 标签下，
        # 必须遍历列表找第一个真带 CPU 包的。
        Write-Host "  查询 GitHub releases（找最近带 win-cpu-x64 的构建）..."
        $api = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=20"
        try {
            $list = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "xiaonuan-offline" }
        } catch {
            throw "取 llama.cpp 发布列表失败：$_`n请用 -LlamaCppUrl 手动指定下载地址。"
        }
        $asset = $null; $tag = ""
        foreach ($r in $list) {
            $a = $r.assets | Where-Object { $_.name -match "bin-win-cpu-x64\.zip$" } | Select-Object -First 1
            if ($a) { $asset = $a; $tag = $r.tag_name; break }
        }
        if (-not $asset) {
            throw "最近 20 个发布里都没找到 win-cpu-x64 包。`n请用 -LlamaCppUrl 手动指定下载地址。"
        }
        $LlamaCppUrl = $asset.browser_download_url
        Write-Host ("  选中 {0} / {1} ({2:N1} MB)" -f $tag, $asset.name, ($asset.size / 1MB))
    }

    $zip = Join-Path $env:TEMP "llamacpp-win-cpu.zip"
    $direct = $LlamaCppUrl
    $LlamaCppUrl = Resolve-DownloadUrl $direct
    if (-not $LlamaCppUrl) {
        throw @"
llama.cpp 二进制下不动：直连与全部加速镜像都失败。
$direct

可选办法：
  1) 用 -LlamaCppUrl 指定已下好的包或内网镜像
  2) 手动下载后解压到 $BinDir（需含 llama-server.exe 与同版本 DLL）
  3) 改用进程内后端：pip install llama-cpp-python，并设 OFFLINE_LLM_MODEL_PATH
     （这样就不需要 llama-server.exe 了，见 README）
"@
    }
    Write-Host "  下载 $LlamaCppUrl"
    $null = Save-FileResumable -url $LlamaCppUrl -dest $zip -label "llama.cpp"
    Write-Host "  解压到 $BinDir"
    Expand-Archive -Path $zip -DestinationPath $BinDir -Force
    Remove-Item $zip -Force

    # 有些包会多套一层目录，把 exe/dll 提到 bin/ 根下
    if (-not (Test-Path $serverExe)) {
        $found = Get-ChildItem $BinDir -Recurse -Filter "llama-server.exe" | Select-Object -First 1
        if ($found) {
            Get-ChildItem $found.Directory -File | Move-Item -Destination $BinDir -Force
            Get-ChildItem $BinDir -Recurse -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    if (-not (Test-Path $serverExe)) { throw "解压后仍未找到 llama-server.exe，请检查 -LlamaCppUrl 指向的包。" }
    Write-Ok "llama-server.exe 就位"
}

# ── 2. GGUF 模型（多源自动选择）──────────────────────────────────────
Write-Step "获取 GGUF 模型（约 2GB，只需一次）"

$modelPath = Join-Path $ModelDir $ModelName
if ((Test-Path $modelPath) -and -not $Force) {
    Write-Ok "已存在，跳过下载：$modelPath"
} else {
    Write-Host "  逐个试源（用 Range 探测可达性与大小）..."
    $chosen = $null
    foreach ($s in $Sources) {
        $size = Get-RemoteSize $s.Url
        if ($size -gt 0) {
            Write-Ok ("{0} 可达，{1:N2} GB" -f $s.Name, ($size / 1GB))
            $chosen = $s
            break
        }
        Write-Warn2 "$($s.Name) 不可达，试下一个。"
    }
    if (-not $chosen) {
        throw @"
所有模型源都不可达。可选办法：
  1) 用 -ModelUrl 指定内网镜像或已备好的副本
  2) 手动下载后放到 $ModelDir\$ModelName
  3) 检查代理设置（HuggingFace 原站在国内常被解析不了）
"@
    }

    Write-Host "  从 $($chosen.Name) 下载（支持断点续传，中断后重跑会接着下）"
    $null = Save-FileResumable -url $chosen.Url -dest $modelPath -label $chosen.Name
    Write-Ok "模型下载完成"
}

# ── 3. 校验 ───────────────────────────────────────────────────────────
Write-Step "校验"

$sizeGB = [math]::Round((Get-Item $modelPath).Length / 1GB, 2)
Write-Host "  模型大小 $sizeGB GB"
$hash = (Get-FileHash $modelPath -Algorithm SHA256).Hash.ToLower()
Write-Host "  模型 SHA256 $hash"

if ($ExpectedModelSha256) {
    if ($hash -ne $ExpectedModelSha256.ToLower()) {
        throw "模型 SHA256 不匹配！`n  期望 $($ExpectedModelSha256.ToLower())`n  实际 $hash`n文件可能损坏或被篡改，请删除后重下。"
    }
    Write-Ok "SHA256 与期望值一致"
} else {
    Write-Warn2 "未提供 -ExpectedModelSha256，本次**未做完整性校验**。"
    Write-Warn2 "请到模型页面核对上面的 SHA256，或重跑并附 -ExpectedModelSha256 <值>。"
}

$serverHash = (Get-FileHash $serverExe -Algorithm SHA256).Hash.ToLower()
Write-Host "  llama-server.exe SHA256 $serverHash"

Write-Host "`n[下一步] powershell -ExecutionPolicy Bypass -File scripts\offline\start.ps1" -ForegroundColor Green
