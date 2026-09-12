<#
.SYNOPSIS
    影书 ShadowScribe —— 电脑端一键安装与配置。

.DESCRIPTION
    把「本地电脑通过内网穿透地址跟服务器沟通」压缩成一条命令：
    装客户端 → 写配置 → 连通性自检。

    两种用法：
      1) 双击同目录下的 setup-client.cmd（会交互式问你地址和 token）
      2) 在 PowerShell 里：
         .\setup-client.ps1 -Endpoint http://host:18080 -Token <SS_TOKEN>

    装完之后**不需要再运行任何影书命令**：最后一步会把 MCP 注册进编辑器，
    并写入一段永不变化的静态指令，让编辑器自己在需要时拉取现实上下文。
    手动跑命令那种「快照」方式保留在 `ss inject`，只给不支持 MCP 的客户端用。

    注意：Windows 默认不允许双击 .ps1（会弹「选择打开方式」），也默认禁止运行
    未签名脚本。这是系统设计，不是脚本问题 —— 双击 .cmd 即可绕开两者。

.NOTES
    本文件必须保存为 **UTF-8 with BOM**。PowerShell 5.1 在没有 BOM 时按系统 ANSI
    代码页（中文系统是 GBK）读取脚本，中文字符串会变成乱码并导致语法错误。

.PARAMETER Endpoint
    服务端地址。内网穿透场景下就是外网地址，例如 http://47.102.212.49:18080

.PARAMETER Token
    服务端 .env 里的 SS_TOKEN。

.PARAMETER NoSetup
    跳过最后一步接线（注册 MCP + 写静态指令）。一般不需要。

.PARAMETER NoInstall
    跳过 pip 安装，只写配置并自检。
#>
[CmdletBinding()]
param(
    [string]$Endpoint,
    [string]$Token,
    [switch]$NoSetup,
    [switch]$NoInstall
)

$ErrorActionPreference = 'Stop'

function Say  { param([string]$m) Write-Host $m }
function Step { param([string]$m) Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Good { param([string]$m) Write-Host "    OK   $m" -ForegroundColor Green }
function Warn { param([string]$m) Write-Host "    !!   $m" -ForegroundColor Yellow }
function Die  {
    param([string]$m, [string]$fix)
    Write-Host "    FAIL $m" -ForegroundColor Red
    if ($fix) {
        Write-Host ""
        Write-Host "    试试这个：" -ForegroundColor Yellow
        Write-Host "      $fix"
    }
    exit 1
}

Say ""
Say "  影书 ShadowScribe · 电脑端安装" -ForegroundColor White

# ------------------------------------------------------- 0. 交互式补齐参数 --
if (-not $Endpoint) {
    Say ""
    $Endpoint = Read-Host "  服务器地址（例如 http://47.102.212.49:18080）"
}
if (-not $Token) {
    $Token = Read-Host "  SS_TOKEN"
}
if (-not $Endpoint -or -not $Token) {
    Die "地址或 token 为空" "重新运行，把两个值都填上"
}
$Endpoint = $Endpoint.Trim().TrimEnd('/')
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
    Die "没找到 Python 3.10+" @"
winget install Python.Python.3.12
      或到 https://www.python.org/downloads/ 下载（安装时勾选 Add python.exe to PATH）
"@
}

# -------------------------------------------------------------- 2. install --
if ($NoInstall) {
    Step "2/5 跳过安装（-NoInstall）"
} else {
    Step "2/5 安装客户端（shadowscribe-client[mcp]）"

    # 依次尝试三个来源。兜底不是可有可无的：这个包还没发布到 PyPI，只写
    # `pip install shadowscribe-client` 的话用户拿到的是 "No matching
    # distribution found" —— 而他往往就站在仓库目录旁边，本可以一行装好。
    $localClient = Join-Path (Split-Path $PSScriptRoot -Parent) 'client'
    $sources = @(
        @{ Label = 'PyPI'; Spec = 'shadowscribe-client[mcp]' }
    )
    if (Test-Path (Join-Path $localClient 'pyproject.toml')) {
        $sources += @{ Label = "本地仓库 $localClient"; Spec = "$localClient[mcp]" }
    }
    $sources += @{
        Label = 'GitHub'
        Spec  = 'shadowscribe-client[mcp] @ git+https://github.com/yw1103/ShadowScribe.git#subdirectory=client'
    }

    $installed = $false
    $locked = $false
    foreach ($src in $sources) {
        Say "    尝试：$($src.Label)"
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        # --force-reinstall --no-cache-dir on purpose. The version number does not
        # change between our commits, and both pip's wheel cache and setuptools'
        # build/ directory are keyed on it — without these flags a reinstall
        # silently keeps the previous code, which cost an hour of confusion once.
        $log = & $python -m pip install --user --upgrade --force-reinstall --no-cache-dir $src.Spec 2>&1
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prev
        $log | Select-String -NotMatch 'already satisfied|RemoteException|^\s*$' |
            Select-Object -Last 3 | ForEach-Object { Say "      $_" }
        if ($code -eq 0) { Good "从 $($src.Label) 安装成功"; $installed = $true; break }
        if ($log -match 'WinError 32|being used by another process|另一个程序正在使用') { $locked = $true }
        Warn "$($src.Label) 不可用，试下一个"
    }
    if ($locked) {
        # Seen in practice: Cursor had the *old* stdio MCP server running and was
        # holding ss.exe open, so pip could not replace it and aborted the whole
        # upgrade while still printing "Successfully built".
        Say ""
        Warn "ss.exe 正被占用 —— 通常是 Cursor 还在跑上一次注册的 MCP 服务"
        Say "         关掉 Cursor，然后重跑本脚本（或先手动结束 ss.exe 进程）" -ForegroundColor DarkGray
    }
    if (-not $installed) {
        Die "三个来源都装不上" @"
$python -m pip install --user --upgrade "<你的仓库路径>\client[mcp]"
      国内网络加镜像：-i https://pypi.tuna.tsinghua.edu.cn/simple
"@
    }
}

# ---------------------------------------------------------------- 3. find ss --
Step "3/5 定位 ss 命令"
$ss = $null
$onPath = Get-Command ss -ErrorAction SilentlyContinue
if ($onPath) {
    $ss = $onPath.Source
    Good "ss 已在 PATH：$ss"
} else {
    # 问 Python 自己 user-base 在哪，比猜 APPDATA 路径可靠
    try { $userBase = (& $python -c "import site; print(site.USER_BASE)" 2>$null).Trim() } catch { $userBase = "" }
    $candidates = @()
    if ($userBase) { $candidates += (Join-Path $userBase 'Scripts\ss.exe') }
    if ($env:APPDATA) {
        $candidates += (Get-ChildItem "$env:APPDATA\Python\Python*\Scripts\ss.exe" -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName })
    }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { $ss = $c; break }
    }
    if ($ss) {
        Warn "ss 不在 PATH，实际路径：$ss"
        $scriptDir = Split-Path $ss -Parent
        if ($env:Path -notlike "*$scriptDir*") {
            $env:Path = "$scriptDir;$env:Path"
            Warn "已临时加入本次会话的 PATH（本脚本可用，但新开窗口仍找不到）"
            Say ""
            Say "         想让 ss 永久可用，复制下面这行到 PowerShell 执行一次：" -ForegroundColor DarkGray
            Say "         [Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';$scriptDir', 'User')" -ForegroundColor DarkGray
        }
    }
}
if (-not $ss) {
    Die "装完了但找不到 ss 命令" @"
$python -m site --user-base
      然后看 <那个目录>\Scripts\ss.exe 是否存在
"@
}
function SS { param([Parameter(ValueFromRemainingArguments = $true)]$a) & $ss @a }

# ------------------------------------------------------------- 4. configure --
Step "4/5 写入连接配置"
SS login --endpoint $Endpoint --token $Token
if ($LASTEXITCODE -ne 0) {
    Die "配置写入或连通性检查失败" @"
确认服务端在跑、端口已开：curl.exe $Endpoint/healthz
      token 错了就去服务器上取：docker compose exec -T api printenv SS_TOKEN
"@
}
Good "配置已保存到 $env:USERPROFILE\.shadowscribe\config.json"

# ----------------------------------------------------------------- 5. doctor --
Step "5/5 自检"
SS doctor
$doctorExit = $LASTEXITCODE

# ------------------------------------------------------------------ 6. setup --
# 这一步才是「无感」的关键，而且它只需要跑这一次。
#
# 之前我把 `ss inject`（写快照）当成日常命令，那是错的：快照一过期就要重跑，
# 等于让用户每天手动搬运自己的上下文。`ss setup` 写的是**永不变化的静态指令**，
# 它让编辑器自己去调 MCP 拿实时数据，所以之后什么都不用做。
if ($NoSetup) {
    Step "6/6 跳过接线（-NoSetup）"
} else {
    Step "6/6 接线：注册 MCP + 写静态指令"
    SS setup --root (Get-Location).Path
    if ($LASTEXITCODE -ne 0) {
        Warn "接线有项目没成功，看上面的输出"
    }
}

# ------------------------------------------------------------------- 收尾 --
Say ""
if ($doctorExit -eq 0) {
    Say "  装好了。" -ForegroundColor Green
} else {
    Say "  装好了，但自检有项目没通过 —— 看上面的 [MISS] 和建议。" -ForegroundColor Yellow
}
Say ""
Say "  之后你不需要再运行任何影书命令。" -ForegroundColor Green
Say "  在 Cursor 里直接说事，它会自己调 get_reality_context 拿现实上下文。"
Say ""
Say "  可选：想手动看一眼现实里发生了什么 ——"
Say "    ss brief --copy     打印最近上下文并复制到剪贴板"
Say ""
Say "  更多：ss --help    ·    文档：https://github.com/yw1103/ShadowScribe"
Say ""
