# YMODEM 串口文件发送工具

通过串口使用 YMODEM 协议发送固件/文件到目标设备。

## 功能
- **list_ports** — 列出可用串口及描述
- **send** — 通过 YMODEM 协议发送文件到指定串口

## 参数
| 参数 | 类型 | 说明 |
|------|------|------|
| action | string | 操作: `list_ports` 或 `send` |
| port | string | 串口号 (send 时必填，如 `COM8`) |
| file | string | 文件路径 (send 时必填) |
| baudrate | int | 波特率 (send 时可选，默认 115200) |
