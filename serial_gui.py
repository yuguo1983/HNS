#!/usr/bin/env python3
"""
Serial Port GUI Application
----------------------------
使用 PyQt5 构建串口调试工具图形界面。
当前阶段使用模拟数据源（定时器产生随机数据）独立测试界面交互。
预留信号/槽机制以支持后续与 task_1 的异步数据接收集成。

Author: Denny Agent SubTask Executor
"""

import sys
import random
import time
import struct
from datetime import datetime

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGroupBox, QLabel, QComboBox, QPushButton, QTextEdit, QPlainTextEdit,
    QStatusBar, QSplitter, QCheckBox, QGridLayout, QFileDialog, QMessageBox,
    QFrame, QSpacerItem, QSizePolicy, QAction, QMenu, QToolBar,
)
from PyQt5.QtCore import (
    Qt, QTimer, QByteArray, pyqtSignal, pyqtSlot, QObject, QSize,
)
from PyQt5.QtGui import (
    QFont, QTextCursor, QColor, QPalette, QIcon, QTextCharFormat,
)

# =============================================================================
# 常量定义
# =============================================================================

BAUD_RATES = [
    "1200", "2400", "4800", "9600", "19200",
    "38400", "57600", "115200", "230400", "460800", "921600",
]

DATA_BITS = ["5", "6", "7", "8"]

PARITY_OPTIONS = ["None", "Even", "Odd", "Mark", "Space"]

STOP_BITS = ["1", "1.5", "2"]

PORT_OPTIONS = [
    "COM1", "COM2", "COM3", "COM4",
    "/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyS0",
    "/dev/cu.usbserial-0001",
]

# 模拟数据模板
MOCK_TEXT_DATA = [
    "Sensor#1: temp=25.3°C, humidity=62%",
    "Sensor#2: pressure=1013.25 hPa",
    "System OK - heartbeat: seq=42",
    "[INFO] Module A initialized successfully",
    "[DATA] 0xA5 0x3C 0x8F 0x12 0x7B 0xE0",
    "GPS: 31.2304°N, 121.4737°E, alt=12.5m",
    "Battery: 3.72V, 85% remaining",
    "WARNING: Signal strength low (-85 dBm)",
    "RPM: 3200, Speed: 45.6 km/h",
    "ACK packet received: id=0x7F",
]

MOCK_HEX_DATA = [
    bytes([0xA5, 0x5A, 0x01, 0x10, 0x00, 0x2F, 0x3C]),
    bytes([0xAA, 0x55, 0x02, 0x20, random.randint(0, 255) for _ in range(4)]),
    bytes([0x7E, 0x00, 0x0A, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x7E]),
]


# =============================================================================
# 串口桩模块 (Stub) — 模拟串口操作
# =============================================================================

class SerialPortStub(QObject):
    """
    串口桩类，模拟串口收发行为。
    使用 QTimer 产生随机数据，模拟异步接收。
    预留信号机制以便后续替换为真实串口实现。
    """

    # 接收到数据信号 — 发送原始字节数据
    data_received = pyqtSignal(QByteArray)
    # 连接状态变化信号
    connection_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._connected = False
        self._port = None
        self._baudrate = 9600
        self._running = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._generate_data)
        self._interval = 2000  # 默认2秒产生一次数据
        self._line_count = 0

    @property
    def is_connected(self):
        return self._connected

    def open(self, port, baudrate=9600, data_bits=8,
             parity='N', stop_bits=1):
        """
        模拟打开串口连接。
        对应 task_1 中的串口打开接口。
        """
        if self._connected:
            self.close()

        # 模拟打开延迟
        self._port = port
        self._baudrate = baudrate
        self._connected = True
        self._line_count = 0

        print(f"[Stub] 串口已打开: {port} @ {baudrate} bps")
        self.connection_changed.emit(True)

        # 启动模拟数据生成
        self._running = True
        self._timer.start(self._interval)
        return True

    def close(self):
        """模拟关闭串口连接。"""
        if self._running:
            self._timer.stop()
            self._running = False
        self._connected = False
        self._port = None
        print("[Stub] 串口已关闭")
        self.connection_changed.emit(False)

    def send(self, data: bytes):
        """
        模拟发送数据。
        对应 task_1 中的串口发送接口。
        """
        if not self._connected:
            print("[Stub] 错误: 串口未连接")
            return False

        hex_str = ' '.join(f'{b:02X}' for b in data)
        print(f"[Stub] 发送 {len(data)} 字节: {hex_str}")
        return True

    def set_receive_timeout(self, ms: int):
        """设置数据生成间隔（模拟接收超时）。"""
        self._interval = max(100, ms)
        if self._running:
            self._timer.setInterval(self._interval)

    def _generate_data(self):
        """模拟异步接收数据——由定时器触发。"""
        if not self._connected:
            return

        self._line_count += 1

        # 随机选择文本或二进制数据
        if random.random() < 0.6:
            # 生成文本行
            text = random.choice(MOCK_TEXT_DATA)
            suffix = f" [line:{self._line_count}]\r\n"
            data = (text + suffix).encode('utf-8')
        else:
            # 生成二进制数据
            data = random.choice(MOCK_HEX_DATA)
            data += bytes([self._line_count & 0xFF])
            data += b'\r\n'

        # 通过信号发射数据
        self.data_received.emit(QByteArray(data))

    def get_config(self):
        """返回当前配置字典。"""
        return {
            'port': self._port,
            'baudrate': self._baudrate,
            'connected': self._connected,
        }


# =============================================================================
# 数据缓冲区 — 管理接收到的数据
# =============================================================================

class ReceiveBuffer:
    """接收数据缓冲区，支持文本和十六进制两种视图。"""

    def __init__(self, max_size=1024 * 1024):
        self._buffer = bytearray()
        self._max_size = max_size

    def append(self, data: bytes):
        self._buffer.extend(data)
        # 限制缓冲区大小
        if len(self._buffer) > self._max_size:
            self._buffer = self._buffer[-self._max_size:]

    def clear(self):
        self._buffer.clear()

    def get_text(self, encoding='utf-8', errors='replace'):
        """获取文本表示。"""
        return self._buffer.decode(encoding, errors)

    def get_hex(self):
        """获取十六进制表示。"""
        hex_str = ''
        for i, b in enumerate(self._buffer):
            if i > 0 and i % 16 == 0:
                hex_str += '\n'
            hex_str += f'{b:02X} '
        return hex_str.strip()

    def size(self):
        return len(self._buffer)

    def get_data(self):
        return bytes(self._buffer)


# =============================================================================
# 主界面
# =============================================================================

class SerialPortGUI(QMainWindow):
    """
    串口调试工具主窗口。
    界面布局使用 QSplitter + QGroupBox，支持窗口缩放响应。
    """

    STYLESHEET = """
    QMainWindow {
        background-color: #f5f5f5;
    }
    QGroupBox {
        font-weight: bold;
        border: 1px solid #cccccc;
        border-radius: 6px;
        margin-top: 10px;
        padding-top: 16px;
        background-color: #ffffff;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 6px;
        color: #2c3e50;
    }
    QPushButton {
        background-color: #3498db;
        color: white;
        border: none;
        border-radius: 4px;
        padding: 6px 14px;
        font-size: 13px;
        min-height: 24px;
    }
    QPushButton:hover {
        background-color: #2980b9;
    }
    QPushButton:pressed {
        background-color: #21618c;
    }
    QPushButton:disabled {
        background-color: #bdc3c7;
        color: #7f8c8d;
    }
    QPushButton#btnSend {
        background-color: #27ae60;
    }
    QPushButton#btnSend:hover {
        background-color: #229954;
    }
    QPushButton#btnSend:disabled {
        background-color: #bdc3c7;
    }
    QPushButton#btnConnect {
        background-color: #e74c3c;
    }
    QPushButton#btnConnect:hover {
        background-color: #c0392b;
    }
    QPushButton#btnConnect:checked {
        background-color: #2ecc71;
    }
    QPushButton#btnConnect:checked:hover {
        background-color: #27ae60;
    }
    QPushButton#btnClear {
        background-color: #e67e22;
    }
    QPushButton#btnClear:hover {
        background-color: #d35400;
    }
    QPushButton#btnSave {
        background-color: #8e44ad;
    }
    QPushButton#btnSave:hover {
        background-color: #7d3c98;
    }
    QComboBox {
        padding: 4px 8px;
        border: 1px solid #cccccc;
        border-radius: 4px;
        min-height: 20px;
        background-color: white;
    }
    QComboBox:focus {
        border-color: #3498db;
    }
    QPlainTextEdit, QTextEdit {
        border: 1px solid #cccccc;
        border-radius: 4px;
        background-color: #1e1e1e;
        color: #d4d4d4;
        font-family: 'Consolas', 'Courier New', monospace;
        font-size: 13px;
    }
    QPlainTextEdit:focus, QTextEdit:focus {
        border-color: #3498db;
    }
    QStatusBar {
        background-color: #ecf0f1;
        border-top: 1px solid #bdc3c7;
        font-size: 12px;
    }
    QCheckBox {
        spacing: 6px;
    }
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
    }
    """

    def __init__(self):
        super().__init__()
        self._serial = SerialPortStub(self)
        self._buffer = ReceiveBuffer()
        self._hex_mode = False
        self._auto_scroll = True
        self._data_count = 0

        self._init_ui()
        self._connect_signals()
        self._apply_styles()

    def _init_ui(self):
        """初始化用户界面。"""
        self.setWindowTitle("串口调试工具 v1.0")
        self.setMinimumSize(900, 650)
        self.resize(1100, 750)

        # 中央部件
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # ========== 顶部：串口参数设置区域 ==========
        settings_group = QGroupBox("串口设置")
        settings_layout = QGridLayout(settings_group)
        settings_layout.setSpacing(8)

        # 端口选择
        settings_layout.addWidget(QLabel("端口:"), 0, 0)
        self.cbPort = QComboBox()
        self.cbPort.setEditable(True)
        self.cbPort.addItems(PORT_OPTIONS)
        self.cbPort.setCurrentText(PORT_OPTIONS[0])
        self.cbPort.setMinimumWidth(140)
        settings_layout.addWidget(self.cbPort, 0, 1)

        # 波特率
        settings_layout.addWidget(QLabel("波特率:"), 0, 2)
        self.cbBaud = QComboBox()
        self.cbBaud.addItems(BAUD_RATES)
        self.cbBaud.setCurrentText("115200")
        self.cbBaud.setMinimumWidth(100)
        settings_layout.addWidget(self.cbBaud, 0, 3)

        # 数据位
        settings_layout.addWidget(QLabel("数据位:"), 0, 4)
        self.cbDataBits = QComboBox()
        self.cbDataBits.addItems(DATA_BITS)
        self.cbDataBits.setCurrentText("8")
        settings_layout.addWidget(self.cbDataBits, 0, 5)

        # 校验位
        settings_layout.addWidget(QLabel("校验位:"), 0, 6)
        self.cbParity = QComboBox()
        self.cbParity.addItems(PARITY_OPTIONS)
        self.cbParity.setCurrentText("None")
        settings_layout.addWidget(self.cbParity, 0, 7)

        # 停止位
        settings_layout.addWidget(QLabel("停止位:"), 0, 8)
        self.cbStopBits = QComboBox()
        self.cbStopBits.addItems(STOP_BITS)
        self.cbStopBits.setCurrentText("1")
        settings_layout.addWidget(self.cbStopBits, 0, 9)

        # 连接/断开按钮
        self.btnConnect = QPushButton("打开串口")
        self.btnConnect.setObjectName("btnConnect")
        self.btnConnect.setCheckable(True)
        self.btnConnect.setMinimumWidth(100)
        settings_layout.addWidget(self.btnConnect, 0, 10)

        # 刷新端口按钮
        self.btnRefresh = QPushButton("刷新")
        self.btnRefresh.setMinimumWidth(60)
        settings_layout.addWidget(self.btnRefresh, 0, 11)

        # 设置列拉伸
        settings_layout.setColumnStretch(12, 1)

        main_layout.addWidget(settings_group)

        # ========== 中间：数据接收与发送区域（可拆分） ==========
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(4)

        # ---- 接收区域 ----
        recv_group = QGroupBox("数据接收")
        recv_layout = QVBoxLayout(recv_group)
        recv_layout.setContentsMargins(4, 16, 4, 4)

        # 工具栏：显示模式 + 控制按钮
        recv_toolbar = QHBoxLayout()
        recv_toolbar.setSpacing(8)

        self.chkHex = QCheckBox("十六进制显示")
        recv_toolbar.addWidget(self.chkHex)

        self.chkAutoScroll = QCheckBox("自动滚动")
        self.chkAutoScroll.setChecked(True)
        recv_toolbar.addWidget(self.chkAutoScroll)

        recv_toolbar.addStretch()

        self.lblDataCount = QLabel("接收: 0 字节")
        recv_toolbar.addWidget(self.lblDataCount)

        self.btnClearRecv = QPushButton("清空接收")
        self.btnClearRecv.setObjectName("btnClear")
        recv_toolbar.addWidget(self.btnClearRecv)

        self.btnSaveLog = QPushButton("保存日志")
        self.btnSaveLog.setObjectName("btnSave")
        recv_toolbar.addWidget(self.btnSaveLog)

        recv_layout.addLayout(recv_toolbar)

        # 接收显示区域
        self.txtRecv = QPlainTextEdit()
        self.txtRecv.setReadOnly(True)
        self.txtRecv.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.txtRecv.setMinimumHeight(200)
        recv_layout.addWidget(self.txtRecv)

        splitter.addWidget(recv_group)

        # ---- 发送区域 ----
        send_group = QGroupBox("数据发送")
        send_layout = QVBoxLayout(send_group)
        send_layout.setContentsMargins(4, 16, 4, 4)

        # 发送工具栏
        send_toolbar = QHBoxLayout()
        self.chkHexSend = QCheckBox("十六进制发送")
        send_toolbar.addWidget(self.chkHexSend)

        self.chkAppendCRLF = QCheckBox("追加 \\r\\n")
        self.chkAppendCRLF.setChecked(True)
        send_toolbar.addWidget(self.chkAppendCRLF)

        send_toolbar.addStretch()

        self.btnClearSend = QPushButton("清空输入")
        self.btnClearSend.setObjectName("btnClear")
        send_toolbar.addWidget(self.btnClearSend)

        send_layout.addLayout(send_toolbar)

        # 发送输入与按钮
        send_row = QHBoxLayout()
        self.txtSend = QTextEdit()
        self.txtSend.setMaximumHeight(80)
        self.txtSend.setPlaceholderText("在此输入要发送的数据...")
        send_row.addWidget(self.txtSend)

        self.btnSend = QPushButton("发送")
        self.btnSend.setObjectName("btnSend")
        self.btnSend.setMinimumWidth(80)
        self.btnSend.setMinimumHeight(60)
        self.btnSend.setEnabled(False)
        send_row.addWidget(self.btnSend)

        send_layout.addLayout(send_row)

        splitter.addWidget(send_group)

        # 设置初始比例（接收区域占 65%，发送区域占 35%）
        splitter.setStretchFactor(0, 65)
        splitter.setStretchFactor(1, 35)

        main_layout.addWidget(splitter, 1)  # stretch=1 让 splitter 扩展

        # ========== 状态栏 ==========
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("就绪 — 请配置串口参数后点击"打开串口"", 5000)

        # 在状态栏添加持久部件
        self.lblStatusPort = QLabel("端口: --")
        self.status.addPermanentWidget(self.lblStatusPort)

        self.lblStatusBaud = QLabel("波特率: --")
        self.status.addPermanentWidget(self.lblStatusBaud)

        self.lblStatusConn = QLabel("● 未连接")
        self.lblStatusConn.setStyleSheet(
            "color: #e74c3c; font-weight: bold; padding-left: 8px;"
        )
        self.status.addPermanentWidget(self.lblStatusConn)

        # 快捷键
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        """设置键盘快捷键。"""
        # Ctrl+Enter 发送
        send_shortcut = QAction("发送", self)
        send_shortcut.setShortcut("Ctrl+Return")
        send_shortcut.triggered.connect(self._send_data)
        self.addAction(send_shortcut)

        # Ctrl+L 清空接收
        clear_shortcut = QAction("清空", self)
        clear_shortcut.setShortcut("Ctrl+L")
        clear_shortcut.triggered.connect(self._clear_receive)
        self.addAction(clear_shortcut)

    def _connect_signals(self):
        """连接信号与槽。"""
        # 串口数据接收信号
        self._serial.data_received.connect(self._on_data_received)

        # 串口连接状态信号
        self._serial.connection_changed.connect(self._on_connection_changed)

        # 按钮信号
        self.btnConnect.clicked.connect(self._toggle_connection)
        self.btnRefresh.clicked.connect(self._refresh_ports)
        self.btnSend.clicked.connect(self._send_data)
        self.btnClearRecv.clicked.connect(self._clear_receive)
        self.btnClearSend.clicked.connect(self._clear_send_input)
        self.btnSaveLog.clicked.connect(self._save_log)

        # 显示模式切换
        self.chkHex.toggled.connect(self._on_display_mode_changed)
        self.chkAutoScroll.toggled.connect(self._on_auto_scroll_changed)

        # 回车键发送 (在发送框中按 Ctrl+Enter 已通过快捷键处理)
        # 直接按 Enter 在 QTextEdit 中默认换行，保留此行为

    def _apply_styles(self):
        """应用样式表。"""
        self.setStyleSheet(self.STYLESHEET)

        # 接收区域字体
        font = QFont("Consolas", 11)
        font.setStyleHint(QFont.Monospace)
        self.txtRecv.setFont(font)

        # 发送区域字体
        send_font = QFont("Microsoft YaHei", 11)
        self.txtSend.setFont(send_font)

    # ------------------------------------------------------------------
    # 信号槽 — 串口操作
    # ------------------------------------------------------------------

    def _toggle_connection(self):
        """切换串口连接/断开。"""
        if self._serial.is_connected:
            self._serial.close()
        else:
            port = self.cbPort.currentText().strip()
            if not port:
                QMessageBox.warning(self, "提示", "请选择或输入串口端口号")
                self.btnConnect.setChecked(False)
                return

            try:
                baudrate = int(self.cbBaud.currentText())
            except ValueError:
                QMessageBox.warning(self, "提示", "波特率格式错误")
                self.btnConnect.setChecked(False)
                return

            data_bits = int(self.cbDataBits.currentText())
            parity_map = {'None': 'N', 'Even': 'E', 'Odd': 'O',
                          'Mark': 'M', 'Space': 'S'}
            parity = parity_map[self.cbParity.currentText()]
            stop_bits = float(self.cbStopBits.currentText())

            success = self._serial.open(port, baudrate, data_bits,
                                        parity, stop_bits)
            if success:
                self.status.showMessage(f"已连接到 {port} @ {baudrate} bps")
            else:
                self.btnConnect.setChecked(False)

    def _refresh_ports(self):
        """
        刷新可用端口列表。
        真实环境下应枚举系统串口，当前为桩函数。
        """
        self.status.showMessage("正在扫描可用串口...", 2000)
        # 模拟延迟后更新
        QTimer.singleShot(500, self._do_refresh_ports)

    def _do_refresh_ports(self):
        """执行端口刷新（桩）。"""
        current = self.cbPort.currentText()
        self.cbPort.clear()
        # 真实环境：使用 serial.tools.list_ports.comports()
        self.cbPort.addItems(PORT_OPTIONS)
        if current in PORT_OPTIONS:
            self.cbPort.setCurrentText(current)
        self.status.showMessage("端口列表已刷新", 3000)

    def _send_data(self):
        """发送数据。"""
        if not self._serial.is_connected:
            QMessageBox.warning(self, "提示", "串口未连接，请先打开串口")
            return

        text = self.txtSend.toPlainText()
        if not text:
            self.status.showMessage("发送内容为空", 2000)
            return

        if self.chkHexSend.isChecked():
            # 十六进制发送：将空格分隔的十六进制字符串转为字节
            try:
                hex_str = text.strip().replace(' ', '')
                if len(hex_str) % 2 != 0:
                    raise ValueError("十六进制字符串长度必须为偶数")
                data = bytes.fromhex(hex_str)
            except Exception as e:
                QMessageBox.warning(self, "格式错误",
                                    f"十六进制数据格式错误:\n{e}")
                return
        else:
            data = text.encode('utf-8')
            if self.chkAppendCRLF.isChecked():
                data += b'\r\n'

        success = self._serial.send(data)
        if success:
            hex_preview = ' '.join(f'{b:02X}' for b in data[:16])
            if len(data) > 16:
                hex_preview += '...'
            self.status.showMessage(
                f"已发送 {len(data)} 字节: {hex_preview}", 3000
            )
            # 在接收区回显发送数据
            timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            echo_text = (
                f"\n--- [{timestamp}] [发送] {len(data)} 字节 ---\n"
            )
            if self._hex_mode:
                echo_text += self._bytes_to_hex_display(data) + '\n'
            else:
                try:
                    echo_text += data.decode('utf-8', errors='replace') + '\n'
                except:
                    echo_text += self._bytes_to_hex_display(data) + '\n'

            self.txtRecv.appendPlainText(echo_text)
            if self._auto_scroll:
                self._scroll_to_bottom()

    # ------------------------------------------------------------------
    # 信号槽 — 数据接收
    # ------------------------------------------------------------------

    @pyqtSlot(QByteArray)
    def _on_data_received(self, data: QByteArray):
        """处理接收到的数据（异步回调/信号槽）。"""
        raw = bytes(data)
        self._buffer.append(raw)
        self._data_count += len(raw)

        # 更新字节计数
        self.lblDataCount.setText(f"接收: {self._data_count} 字节")

        # 追加显示
        timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
        if self._hex_mode:
            display_text = f"[{timestamp}] {self._bytes_to_hex_display(raw)}\n"
        else:
            try:
                text = raw.decode('utf-8', errors='replace')
                # 替换控制字符为可见形式
                text = self._sanitize_text(text)
                display_text = f"[{timestamp}] {text}"
            except:
                display_text = (
                    f"[{timestamp}] {self._bytes_to_hex_display(raw)}\n"
                )

        self.txtRecv.appendPlainText(display_text)

        if self._auto_scroll:
            self._scroll_to_bottom()

    def _on_connection_changed(self, connected: bool):
        """连接状态变化处理。"""
        if connected:
            self.btnConnect.setText("断开串口")
            self.btnConnect.setChecked(True)
            self.btnSend.setEnabled(True)
            self.lblStatusConn.setText("● 已连接")
            self.lblStatusConn.setStyleSheet(
                "color: #2ecc71; font-weight: bold; padding-left: 8px;"
            )
            self.lblStatusPort.setText(f"端口: {self._serial.get_config()['port']}")
            self.lblStatusBaud.setText(
                f"波特率: {self._serial.get_config()['baudrate']}"
            )
        else:
            self.btnConnect.setText("打开串口")
            self.btnConnect.setChecked(False)
            self.btnSend.setEnabled(False)
            self.lblStatusConn.setText("● 未连接")
            self.lblStatusConn.setStyleSheet(
                "color: #e74c3c; font-weight: bold; padding-left: 8px;"
            )
            if not self._serial.get_config().get('port'):
                self.lblStatusPort.setText("端口: --")
                self.lblStatusBaud.setText("波特率: --")

    # ------------------------------------------------------------------
    # 信号槽 — 界面控制
    # ------------------------------------------------------------------

    def _on_display_mode_changed(self, hex_mode: bool):
        """切换十六进制/文本显示模式。"""
        self._hex_mode = hex_mode
        # 重新显示缓冲区内容
        self._refresh_display()
        self.status.showMessage(
            f"已切换为{'十六进制' if hex_mode else '文本'}显示模式", 2000
        )

    def _on_auto_scroll_changed(self, auto: bool):
        """切换自动滚动。"""
        self._auto_scroll = auto
        if auto:
            self._scroll_to_bottom()

    def _clear_receive(self):
        """清空接收区。"""
        self._buffer.clear()
        self.txtRecv.clear()
        self._data_count = 0
        self.lblDataCount.setText("接收: 0 字节")
        self.status.showMessage("接收区已清空", 2000)

    def _clear_send_input(self):
        """清空发送输入框。"""
        self.txtSend.clear()
        self.txtSend.setFocus()

    def _save_log(self):
        """保存接收日志到文件。"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        default_name = f"serial_log_{timestamp}.txt"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存日志文件", default_name,
            "文本文件 (*.txt);;所有文件 (*)"
        )
        if not file_path:
            return

        try:
            content = self.txtRecv.toPlainText()
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            self.status.showMessage(f"日志已保存至: {file_path}", 3000)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"无法保存文件:\n{e}")

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _refresh_display(self):
        """根据当前模式刷新显示内容。"""
        self.txtRecv.clear()
        if self._buffer.size() > 0:
            if self._hex_mode:
                display = self._buffer.get_hex()
            else:
                display = self._buffer.get_text()
            self.txtRecv.setPlainText(display)
            if self._auto_scroll:
                self._scroll_to_bottom()

    def _scroll_to_bottom(self):
        """滚动到文本末尾。"""
        cursor = self.txtRecv.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.txtRecv.setTextCursor(cursor)

    @staticmethod
    def _bytes_to_hex_display(data: bytes) -> str:
        """将字节数据格式化为十六进制显示字符串。"""
        return ' '.join(f'{b:02X}' for b in data)

    @staticmethod
    def _sanitize_text(text: str) -> str:
        """替换控制字符为可视化表示，保留 \\n \\r \\t。"""
        result = []
        for ch in text:
            code = ord(ch)
            if code >= 0x20 or code in (0x0A, 0x0D, 0x09):
                result.append(ch)
            else:
                result.append(f'[{code:02X}]')
        return ''.join(result)

    # ------------------------------------------------------------------
    # 窗口事件
    # ------------------------------------------------------------------

    def closeEvent(self, event):
        """关闭窗口时断开串口。"""
        if self._serial.is_connected:
            self._serial.close()
        event.accept()


# =============================================================================
# 程序入口
# =============================================================================

def main():
    """主函数 — 启动 GUI 应用。"""
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("串口调试工具")
    app.setApplicationVersion("1.0.0")

    # 设置全局字体
    font = QFont("Microsoft YaHei", 10)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)

    window = SerialPortGUI()
    window.show()

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
