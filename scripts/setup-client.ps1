<#
.SYNOPSIS
    影书 ShadowScribe —— 电脑端一键安装与配置。

.DESCRIPTION
    把「本地电脑通过内网穿透地址跟服务器沟通」这件事压缩成一条命令：
    装客户端 → 写配置 → 连通性自检。

    设计原则：每一步都打印它在做什么，任何一步失败都给出可直接复制的修复命令，
    而不是丢一个 traceback。

.PARAMETER Endpoint
    服务端地址。内网穿透场景下就是外网地址，例如 http://47.102.212.49:18080

.PARAMETER Token
    服务端 .env 里的 SS_TOKEN。取法：
    ssh <服务器> 'grep ^SS_TOKEN= /opt/shadowscribe/app/.env | cut -d= -f2-'

.PARAMETER Mcp
    顺便把 MCP 服务注册到 Cursor 的 ~/.cursor/mcp.json（会先备份原文件）。

.PARAMETER Inject
    顺便往当前目录注入一次上下文（等价于 cd 到项目后执行 ss inject --auto）。

.PARAMETER NoInstall
    跳过 pip 安装，只写配置并自检（客户端已经装过时用）。

.EXAMPLE
    .\setup-client.ps1 -Endpoint http://47.102.212.49:18080 -Token 你的token

.EXAMPLE
    .\setup-client.ps1 -Endpoint http://47.102.212.49:18080 -Token 你的token -Mcp -Inject
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Endpoint,
    [Parameter(Mandatory = $true)][string]$Token,
    [switch]$Mcp,
    [switch]$Inject,
    [switch]$NoInstall
)

$ErrorActionPreference = 'Stop'
function Say  { param([string]$m) Write-Host $m }
function Step { param([string]$m) Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Good { param([string]$m) Write-Host "    OK   $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "    !!   $m" -ForegroundColor Yellow }
function Die  { param([string]$m, [string]$fix) 
    Write-Host "    FAIL $m" -ForegroundColor Red
    if ($fix) { Write-Host ""; Write-Host "    试试这个：" -ForegroundColor Yellow; Write-Host "      $fix" }
    exit 1
}

$Endpoint = $Endpoint.TrimEnd('/')

Say ""
Say "  影书 ShadowScribe · 电脑端安装" -ForegroundColor White
Say "  服务端：$Endpoint"

# ---------------------------------------------------------------- 1. Python --
Step "1/5 检查 Python"
$python = $null
foreach ($cand in @('python', 'python3', 'py')) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    try {
        $ver = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and [version]$ver -ge [version]'3.10') {
            $python = $cmd.Source
            Good "找到 $($cmd.Source)  (Python $ver)"
            break
        }
    } catch { }
}
if (-not $python) {
    Die "没找到 Python 3.10+" "winget install Python.Python.3.12`n      或到 https://www.python.org/downloads/ 下载安装（安装时勾选 Add to PATH）"
}

# -------------------------------------------------------------- 2. install --
if ($NoInstall) {
    Step "2/5 跳过安装（-NoInstall）"
} else {
    Step "2/5 安装客户端（shadowscribe-client[mcp]）"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'   # pip 的进度会写到 stderr
    & $python -m pip install --user --upgrade "shadowscribe-client[mcp]"
    $pipExit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($pipExit -ne 0) {
        Die "pip 安装失败" "$python -m pip install --user --upgrade `"shadowscribe-client[mcp]`"`n      国内网络可加镜像：-i https://pypi.tuna.tsinghua.edu.cn/simple"
    }
    Good "安装完成"
}

# ---------------------------------------------------------------- 3. find ss --
Step "3/5 定位 ss 命令"
$ss = $null
$onPath = Get-Command ss -ErrorAction SilentlyContinue
if ($onPath) { $ss = $onPath.Source; Good "ss 已在 PATH：$ss" }
else {
    $candidates = @()
    if ($env:APPDATA) { $candidates += (Get-ChildItem "$env:APPDATA\Python\Python*\Scripts\ss.exe" -ErrorAction SilentlyContinue) }
    if ($env:USERPROFILE) { $candidates += (Get-ChildItem "$env:USERPROFILE\AppData\Roaming\Python\Python*\Scripts\ss.exe" -ErrorAction SilentlyContinue) }
    if ($candidates.Count -gt 0) {
        $ss = $candidates[0].FullName
        Warn "ss 不在 PATH，实际路径：$ss"
        $scriptDir = Split-Path $ss -Parent
        if ($env:Path -notlike "*$scriptDir*") {
            $env:Path = "$scriptDir;$env:Path"
            Warn "已临时加入本次会话的 PATH"
            Say  "         想永久生效，把下面这行贴进 PowerShell 再重开窗口："
            Say  "         [Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';$scriptDir', 'User')" -ForegroundColor DarkGray
        }
    }
}
if (-not $ss) {
    Die "装完了但找不到 ss 命令" "手动找：Get-ChildItem `"$env:APPDATA\Python\Python*\Scripts\ss.exe`""
}
function SS { param([Parameter(ValueFromRemainingArguments = $true)]$a) & $ss @a }

# ------------------------------------------------------------- 4. configure --
Step "4/5 写入连接配置"
SS login --endpoint $Endpoint --token $Token
if ($LASTEXITCODE -ne 0) {
    Die "配置写入或连通性检查失败" "确认服务端在跑、端口已开：curl.exe $Endpoint/healthz"
}
Good "配置已保存到 $env:USERPROFILE\.shadowscribe\config.json"

# ----------------------------------------------------------------- 5. doctor --
Step "5/5 自检"
SS doctor
$doctorExit = $LASTEXITCODE

# ----------------------------------------------------------------- optional --
if ($Mcp) {
    Step "附加：注册 MCP 到 Cursor"
    $cursorDir = Join-Path $env:USERPROFILE ".cursor"
    $mcpFile = Join-Path $cursorDir "mcp.json"
    if (-not (Test-Path $cursorDir)) { New-Item -ItemType Directory -Force -Path $cursorDir | Out-Null }
    $config = [ordered]@{ mcpServers = [ordered]@{} }
    if (Test-Path $mcpFile) {
        Copy-Item $mcpFile "$mcpFile.bak" -Force
        try {
            $existing = Get-Content $mcpFile -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($existing.mcpServers) { $config.mcpServers = $existing.mcpServers }
        } catch {
            Warn "原有 mcp.json 无法解析，已备份为 mcp.json.bak，将新建"
        }
    }
    $config.mcpServers['shadowscribe'] = [ordered]@{ command = $ss; args = @('mcp') }
    ($config | ConvertTo-Json -Depth 10) | Set-Content -Path $mcpFile -Encoding UTF8
    Good "写入 $mcpFile（原文件备份为 mcp.json.bak）"
    Say  "         重启 Cursor 后，会话里问「我今天答应了谁什么」试试" -ForegroundColor DarkGray
}

if ($Inject) {
    Step "附加：往当前目录注入上下文"
    SS inject --auto
}

# ------------------------------------------------------------------- 收尾 --
Say ""
if ($doctorExit -eq 0) {
    Say "  装好了。" -ForegroundColor Green
} else {
    Say "  装好了，但自检有项目没通过 —— 看上面的 [MISS] 和建议。" -ForegroundColor Yellow
}
Say ""
Say "  日常两条命令："
Say "    ss brief --copy     看最近发生了什么，并复制到剪贴板"
Say "    ss inject --auto    把上下文写进 Cursor / CLAUDE.md，之后新会话自动带上"
Say ""
Say "  更多：ss --help    ·    文档：https://github.com/yw1103/ShadowScribe"
Say ""
