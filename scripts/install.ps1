# AtomCode Agent 一键安装脚本
# 用法: irm https://raw.atomgit.com/atomgit_atomcode/atomcode/raw/main/scripts/install.ps1 | iex
# 或本地: iwr -useb install.ps1 | iex

# ─────────────────────────────────────────────────────────
# 1. 配置区
# ─────────────────────────────────────────────────────────
$ErrorActionPreference = "Stop"

$REPO_URL      = "https://github.com/yuguo1983/HNS.git"
$REPO_MIRROR   = "https://gh-proxy.com/https://github.com/yuguo1983/HNS.git"
$INSTALL_DIR   = "$env:USERPROFILE\denny-agent"
$PY_MIN_VERSION = "3.10"

# ANSI 颜色（PowerShell 5+ 支持）
function C($code) { return "`e[$code m" }
$BLUE   = C("34")
$GREEN  = C("32")
$YELLOW = C("33")
$RED    = C("31")
$CYAN   = C("36")
$RESET  = C("0")

function Info($msg)  { Write-Host "  $BLUE[INFO]$RESET  $msg" }
function Ok($msg)    { Write-Host "  $GREEN[OK]$RESET    $msg" }
function Warn($msg)  { Write-Host "  $YELLOW[WARN]$RESET  $msg" }
function Err($msg)   { Write-Host "  $RED[ERR]$RESET    $msg" }
function Step($n, $msg) { Write-Host "`n  $CYAN[$n]$RESET $msg" }

# ─────────────────────────────────────────────────────────
# 2. 环境检查
# ─────────────────────────────────────────────────────────
Step "1/5" "检查运行环境..."

# 检查 Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Err "未找到 Python，请先安装 Python $PY_MIN_VERSION+: https://python.org"
    exit 1
}
$pyVer = (python --version 2>&1) -replace "Python ", ""
$pyMajor, $pyMinor = $pyVer.Split(".")[0..1] | ForEach-Object { [int]$_ }
$needMajor, $needMinor = $PY_MIN_VERSION.Split(".") | ForEach-Object { [int]$_ }
if ($pyMajor -lt $needMajor -or ($pyMajor -eq $needMajor -and $pyMinor -lt $needMinor)) {
    Err "Python 版本过低: $pyVer，需要 $PY_MIN_VERSION+"
    exit 1
}
Ok "Python $pyVer"

# 检查 git
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) {
    Warn "未找到 git，将使用 pip 直接从 GitHub 安装（可能较慢）"
    $useGit = $false
} else {
    Ok "Git 已安装"
    $useGit = $true
}

# 检查 pip
$pip = Get-Command pip -ErrorAction SilentlyContinue
if (-not $pip) {
    Err "未找到 pip，请运行: python -m ensurepip --upgrade"
    exit 1
}
Ok "pip 已就绪"

# ─────────────────────────────────────────────────────────
# 3. 安装方式选择
# ─────────────────────────────────────────────────────────
Step "2/5" "获取项目代码..."

if ($useGit) {
    # 已存在则更新，否则 clone
    if (Test-Path "$INSTALL_DIR\.git") {
        Info "更新已有安装: $INSTALL_DIR"
        Push-Location $INSTALL_DIR
        git pull --quiet 2>&1 | Out-Null
        Pop-Location
        Ok "已更新到最新版本"
    } else {
        Info "克隆仓库到: $INSTALL_DIR"
        # 优先用直连，失败换镜像
        try {
            git clone --depth 1 --quiet $REPO_URL $INSTALL_DIR 2>&1 | Out-Null
            if (-not (Test-Path "$INSTALL_DIR\agent.py")) { throw "clone 失败" }
            Ok "克隆成功（直连 GitHub）"
        } catch {
            Warn "直连 GitHub 失败，尝试镜像源..."
            git clone --depth 1 --quiet $REPO_MIRROR $INSTALL_DIR 2>&1 | Out-Null
            if (-not (Test-Path "$INSTALL_DIR\agent.py")) {
                Err "克隆失败，请检查网络或手动下载"
                exit 1
            }
            Ok "克隆成功（镜像源）"
        }
    }
} else {
    # 无 git：用 pip 直接从 GitHub 安装（不落地源码）
    Info "使用 pip 直接安装（无源码落地）"
    try {
        pip install "denny-agent @ git+$REPO_URL" --quiet 2>&1 | Out-Null
        Ok "pip 安装成功"
    } catch {
        Warn "直连失败，尝试镜像源..."
        pip install "denny-agent @ git+$REPO_MIRROR" --quiet 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Err "安装失败，请检查网络"
            exit 1
        }
        Ok "pip 安装成功（镜像源）"
    }
    # 跳到配置步骤
    Step "5/5" "安装完成！"
    Write-Host "`n  $GREEN━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$RESET"
    Write-Host "  $GREEN  Denny Agent 安装成功！$RESET"
    Write-Host "  $GREEN━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$RESET"
    Write-Host "`n  $CYAN下一步:$RESET"
    Write-Host "    1. 创建配置文件: 在任意目录建 .config (JSON)"
    Write-Host "       示例见: https://github.com/yuguo1983/HNS/blob/main/.config.example"
    Write-Host "    2. 启动: $GREEN denny $RESET"
    Write-Host "    3. 单次问答: $GREEN denny -q \"你好\" $RESET"
    Write-Host "    4. 多Agent协作: $GREEN denny swarm \"分析项目\" $RESET"
    Write-Host ""
    exit 0
}

# ─────────────────────────────────────────────────────────
# 4. pip 安装依赖
# ─────────────────────────────────────────────────────────
Step "3/5" "安装 Python 依赖..."

Push-Location $INSTALL_DIR
try {
    pip install -e . --quiet 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "pip install 失败" }
    Ok "依赖安装完成"
} catch {
    Err "依赖安装失败，请手动运行: pip install -r requirements.txt"
    Pop-Location
    exit 1
}
Pop-Location

# ─────────────────────────────────────────────────────────
# 5. 配置文件
# ─────────────────────────────────────────────────────────
Step "4/5" "检查配置文件..."

$configPath = "$INSTALL_DIR\.config"
$configExample = "$INSTALL_DIR\.config.example"

if (Test-Path $configPath) {
    Ok "配置文件已存在: $configPath"
} elseif (Test-Path $configExample) {
    Copy-Item $configExample $configPath
    Warn "已从模板创建 .config，请编辑填入 API 密钥:"
    Write-Host "    $CYAN notepad $configPath $RESET"
} else {
    # 直接生成最小配置
    $configContent = @'
{
    "ANTHROPIC_API_KEY": "sk-你的密钥",
    "ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic",
    "ANTHROPIC_MODEL": "deepseek-v4-flash"
}
'@
    $configContent | Out-File -FilePath $configPath -Encoding utf8
    Warn "已生成 .config 模板，请编辑填入 API 密钥:"
    Write-Host "    $CYAN notepad $configPath $RESET"
}

# ─────────────────────────────────────────────────────────
# 6. 完成
# ─────────────────────────────────────────────────────────
Step "5/5" "安装完成！"

Write-Host "`n  $GREEN━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$RESET"
Write-Host "  $GREEN  Denny Agent 安装成功！$RESET"
Write-Host "  $GREEN━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$RESET"
Write-Host "`n  $CYAN安装位置:$RESET  $INSTALL_DIR"
Write-Host "  $CYAN启动命令:$RESET    denny"
Write-Host "`n  $CYAN常用命令:$RESET"
Write-Host "    denny                    # 交互式聊天"
Write-Host "    denny -q \"你好\"          # 单次问答"
Write-Host "    denny swarm \"分析项目\"   # 多Agent协作"
Write-Host "    denny webui              # 启动 Web 界面"
Write-Host "`n  $CYAN升级:$RESET irm https://raw.githubusercontent.com/yuguo1983/HNS/main/scripts/install.ps1 | iex"
Write-Host "  $CYAN卸载:$RESET pip uninstall denny-agent; Remove-Item -Recurse $INSTALL_DIR"
Write-Host ""

# 自动加入 PATH（如果 denny 不在 PATH）
$dennyCmd = Get-Command denny -ErrorAction SilentlyContinue
if (-not $dennyCmd) {
    $scriptsDir = (python -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>&1).Trim()
    if ($scriptsDir -and (Test-Path "$scriptsDir\denny.exe")) {
        $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
        if ($userPath -notlike "*$scriptsDir*") {
            [Environment]::SetEnvironmentVariable("PATH", "$userPath;$scriptsDir", "User")
            Warn "已将 $scriptsDir 加入用户 PATH，请重开终端使生效"
        }
    }
}
