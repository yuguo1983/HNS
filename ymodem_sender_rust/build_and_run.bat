@echo off
cd /d "%~dp0"
echo 正在编译...
cargo build
if %errorlevel% neq 0 (
    echo 编译失败！请检查错误信息。
    pause
    exit /b %errorlevel%
)
echo 编译成功！启动新版本...
cargo run
pause
