# -*- coding: utf-8 -*-
"""
YMODEM 串口文件发送 GUI 工具
基于 tkinter + pyserial
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import serial
import serial.tools.list_ports
import os
import time
import struct
import logging
from datetime import datetime

# ========== YMODEM 协议实现 ==========

class YModem:
    """YMODEM 协议实现"""
    
    # 协议常量
    SOH = 0x01    # 128字节数据块
    STX = 0x02    # 1024字节数据块
    EOT = 0x04    # 结束传输
    ACK = 0x06    # 确认
    NAK = 0x15    # 否定确认
    CAN = 0x18    # 取消
    CRC = b'C'    # CRC 模式请求 (0x43)
    
    MAX_RETRIES = 10
    MAX_ERRORS = 5
    TIMEOUT = 1.0
    
    def __init__(self, port, baudrate=115200, log_callback=None):
        self.port = port
        self.baudrate = baudrate
        self.log_callback = log_callback
        self.serial = None
        self._cancel = False
        
    def log(self, msg, level="INFO"):
        if self.log_callback:
            self.log_callback(msg, level)
        else:
            print(f"[{level}] {msg}")
    
    def open(self):
        """打开串口"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.TIMEOUT,
                xonxoff=False,
                rtscts=False
            )
            self.log(f"串口 {self.port} 已打开 (波特率: {self.baudrate})")
            return True
        except Exception as e:
            self.log(f"打开串口失败: {e}", "ERROR")
            return False
    
    def close(self):
        """关闭串口"""
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.log("串口已关闭")
    
    def cancel(self):
        """取消传输"""
        self._cancel = True
        
    def _calc_crc16(self, data):
        """计算 CRC-16 (CCITT)"""
        crc = 0
        for byte in data:
            crc ^= (byte << 8)
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc = (crc << 1)
            crc &= 0xFFFF
        return crc
    
    def _wait_for_crc(self):
        """等待接收端发送 CRC 请求字符 'C'"""
        for i in range(self.MAX_RETRIES * 10):
            if self._cancel:
                return False
            byte = self.serial.read(1)
            if byte:
                if byte[0] == self.CRC:
                    return True
                elif byte[0] == self.NAK:
                    self.log("接收端请求 NAK (非CRC模式)", "WARNING")
                    # 仍返回 True，尝试继续
                    return True
                elif byte[0] == self.CAN:
                    self.log("接收端取消传输", "ERROR")
                    return False
            time.sleep(0.1)
        self.log("等待接收端超时 (未收到 'C')", "ERROR")
        return False
    
    def _send_packet(self, seq, data, block_size=128):
        """发送一个数据包"""
        if block_size == 1024:
            header = bytes([self.STX, seq & 0xFF, (~seq) & 0xFF])
        else:
            header = bytes([self.SOH, seq & 0xFF, (~seq) & 0xFF])
        
        # 填充数据到指定大小
        if len(data) < block_size:
            data = data + bytes([0x1A] * (block_size - len(data)))
        elif len(data) > block_size:
            data = data[:block_size]
        
        # 计算 CRC
        crc = self._calc_crc16(data)
        packet = header + data + bytes([(crc >> 8) & 0xFF, crc & 0xFF])
        
        self.serial.write(packet)
        return True
    
    def _read_response(self):
        """读取接收端响应"""
        byte = self.serial.read(1)
        if byte:
            return byte[0]
        return None
    
    def send_file(self, filepath):
        """发送文件 (YMODEM 协议)"""
        if not self.serial or not self.serial.is_open:
            self.log("串口未打开", "ERROR")
            return False
        
        self._cancel = False
        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)
        
        self.log(f"准备发送文件: {filename} ({filesize} 字节)")
        
        # --- 第一步：发送文件头（文件名+大小）---
        self.log("等待接收端进入接收模式...")
        if not self._wait_for_crc():
            # 发送取消
            self.serial.write(bytes([self.CAN, self.CAN]))
            return False
        
        # 构建文件头数据: 文件名 + '\0' + 文件大小字符串 + '\0'
        header_data = filename.encode('utf-8') + b'\x00'
        header_data += str(filesize).encode('utf-8') + b'\x00'
        
        seq = 0
        self._send_packet(seq, header_data, 128)
        self.log(f"发送文件头: {filename}")
        
        # 等待 ACK + CRC ('C')
        response = self._read_response()
        if response == self.ACK:
            if self._wait_for_crc():
                self.log("文件头已确认")
            else:
                self.log("文件头确认后未收到 CRC 请求", "ERROR")
                return False
        elif response == self.NAK:
            self.log("文件头被 NAK，重试", "WARNING")
            # 简单重试一次
            self._send_packet(seq, header_data, 128)
            response = self._read_response()
            if response != self.ACK:
                self.log("文件头发送失败", "ERROR")
                return False
            if not self._wait_for_crc():
                return False
        elif response == self.CAN:
            self.log("传输被接收端取消", "ERROR")
            return False
        else:
            self.log(f"文件头响应异常: {response}", "ERROR")
            return False
        
        # --- 第二步：发送文件数据 ---
        seq = 1
        errors = 0
        retries = 0
        sent_bytes = 0
        
        with open(filepath, 'rb') as f:
            while True:
                if self._cancel:
                    self.log("传输已取消", "WARNING")
                    self.serial.write(bytes([self.CAN, self.CAN]))
                    return False
                
                chunk = f.read(1024)
                if not chunk:
                    break
                
                # 选择块大小
                if len(chunk) == 1024:
                    block_size = 1024
                else:
                    block_size = 128
                
                # 发送数据包
                self._send_packet(seq, chunk, block_size)
                
                # 等待响应
                response = self._read_response()
                if response == self.ACK:
                    # 成功
                    sent_bytes += len(chunk)
                    progress = (sent_bytes / filesize) * 100 if filesize > 0 else 0
                    self.log(f"进度: {sent_bytes}/{filesize} ({progress:.1f}%) - 包 {seq}")
                    seq += 1
                    retries = 0
                    errors = 0
                elif response == self.NAK:
                    retries += 1
                    if retries > self.MAX_RETRIES:
                        self.log(f"包 {seq} 重试次数过多，放弃", "ERROR")
                        return False
                    self.log(f"包 {seq} 收到 NAK，重试 ({retries})", "WARNING")
                    # 回退文件指针，重发
                    f.seek(-len(chunk), os.SEEK_CUR)
                elif response == self.CAN:
                    self.log("传输被接收端取消", "ERROR")
                    return False
                else:
                    errors += 1
                    if errors > self.MAX_ERRORS:
                        self.log(f"包 {seq} 错误过多，放弃", "ERROR")
                        return False
                    self.log(f"包 {seq} 收到未知响应: {response}，重试", "WARNING")
                    f.seek(-len(chunk), os.SEEK_CUR)
        
        self.log(f"文件数据发送完成 ({sent_bytes} 字节)")
        
        # --- 第三步：发送 EOT ---
        for _ in range(3):
            if self._cancel:
                return False
            self.serial.write(bytes([self.EOT]))
            response = self._read_response()
            if response == self.ACK:
                self.log("EOT 已确认")
                break
            elif response == self.NAK:
                self.log("EOT 收到 NAK，重试", "WARNING")
                continue
            time.sleep(0.1)
        else:
            self.log("EOT 发送失败", "ERROR")
            return False
        
        # --- 第四步：发送结束包（空包）---
        if self._wait_for_crc():
            self._send_packet(0, b'', 128)
            response = self._read_response()
            if response == self.ACK:
                self.log("结束包已确认")
            else:
                self.log(f"结束包响应异常: {response}", "WARNING")
        
        self.log(f"✅ 文件传输完成: {filename}")
        return True


# ========== GUI 界面 ==========

class YModemGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("YMODEM 串口文件发送工具")
        self.root.geometry("720x540")
        self.root.minsize(640, 480)
        
        # 设置样式
        style = ttk.Style()
        style.theme_use('clam')
        
        # 变量
        self.selected_port = tk.StringVar()
        self.selected_baud = tk.StringVar(value="115200")
        self.selected_file = tk.StringVar()
        self.send_button_text = tk.StringVar(value="📤 发送文件")
        
        self.ymodem = None
        self.send_thread = None
        self.is_sending = False
        
        # 创建界面
        self._build_ui()
        
        # 自动扫描串口
        self._scan_ports()
    
    def _build_ui(self):
        """构建界面"""
        # 主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # ===== 串口设置区域 =====
        conn_frame = ttk.LabelFrame(main_frame, text="📡 串口设置", padding="10")
        conn_frame.pack(fill=tk.X, pady=(0, 10))
        
        # 串口选择
        ttk.Label(conn_frame, text="串口:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.port_combo = ttk.Combobox(conn_frame, textvariable=self.selected_port, width=25, state="readonly")
        self.port_combo.grid(row=0, column=1, sticky=tk.W, padx=5)
        
        self.refresh_btn = ttk.Button(conn_frame, text="🔄 刷新", command=self._scan_ports)
        self.refresh_btn.grid(row=0, column=2, padx=5)
        
        # 波特率
        ttk.Label(conn_frame, text="波特率:").grid(row=0, column=3, sticky=tk.W, padx=5)
        baud_combo = ttk.Combobox(conn_frame, textvariable=self.selected_baud, 
                                   values=["9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"],
                                   width=10, state="readonly")
        baud_combo.grid(row=0, column=4, sticky=tk.W, padx=5)
        
        # 列权重
        conn_frame.columnconfigure(5, weight=1)
        
        # ===== 文件选择区域 =====
        file_frame = ttk.LabelFrame(main_frame, text="📁 文件选择", padding="10")
        file_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(file_frame, text="文件:").grid(row=0, column=0, sticky=tk.W, padx=5)
        ttk.Entry(file_frame, textvariable=self.selected_file, width=60).grid(row=0, column=1, sticky=tk.EW, padx=5)
        ttk.Button(file_frame, text="📂 浏览...", command=self._browse_file).grid(row=0, column=2, padx=5)
        file_frame.columnconfigure(1, weight=1)
        
        # ===== 操作按钮 =====
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.send_btn = ttk.Button(
            btn_frame, 
            textvariable=self.send_button_text,
            command=self._toggle_send,
            width=20
        )
        self.send_btn.pack(side=tk.LEFT, padx=5)
        
        self.cancel_btn = ttk.Button(
            btn_frame,
            text="❌ 取消",
            command=self._cancel_send,
            width=10,
            state=tk.DISABLED
        )
        self.cancel_btn.pack(side=tk.LEFT, padx=5)
        
        self.clear_btn = ttk.Button(
            btn_frame,
            text="🧹 清空日志",
            command=self._clear_log
        )
        self.clear_btn.pack(side=tk.RIGHT, padx=5)
        
        # ===== 进度条 =====
        self.progress = ttk.Progressbar(main_frame, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=(0, 10))
        
        # ===== 日志输出 =====
        log_frame = ttk.LabelFrame(main_frame, text="📋 传输日志", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = scrolledtext.ScrolledText(
            log_frame, 
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white"
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # 配置日志颜色标签
        self.log_text.tag_configure("INFO", foreground="#d4d4d4")
        self.log_text.tag_configure("OK", foreground="#4ec94e")
        self.log_text.tag_configure("WARNING", foreground="#e6db74")
        self.log_text.tag_configure("ERROR", foreground="#f92672")
        self.log_text.tag_configure("HEADER", foreground="#66d9ef", font=("Consolas", 9, "bold"))
        
        self._log("YMODEM 串口文件发送工具 v1.0", "HEADER")
        self._log("请选择串口、波特率和文件后点击发送", "INFO")
    
    def _scan_ports(self):
        """扫描可用串口"""
        ports = serial.tools.list_ports.comports()
        port_list = []
        for p in sorted(ports):
            port_list.append(f"{p.device} - {p.description}")
        
        self.port_combo['values'] = port_list
        
        if port_list:
            self.selected_port.set(port_list[0])
            self._log(f"发现 {len(port_list)} 个串口", "OK")
            for p in port_list:
                self._log(f"  {p}", "INFO")
        else:
            self.selected_port.set("")
            self._log("未发现可用串口，请检查连接", "WARNING")
    
    def _browse_file(self):
        """选择文件"""
        filename = filedialog.askopenfilename(
            title="选择要发送的文件",
            filetypes=[
                ("所有文件", "*.*"),
                ("固件文件", "*.bin;*.hex;*.fw"),
                ("文本文件", "*.txt;*.csv"),
                ("图片文件", "*.jpg;*.png;*.bmp")
            ]
        )
        if filename:
            self.selected_file.set(filename)
            filesize = os.path.getsize(filename)
            self._log(f"已选择文件: {os.path.basename(filename)} ({filesize:,} 字节)", "OK")
    
    def _log(self, msg, level="INFO"):
        """向日志区域写入信息（线程安全）"""
        def _write():
            timestamp = datetime.now().strftime("%H:%M:%S")
            tag = level if level in ("INFO", "OK", "WARNING", "ERROR", "HEADER") else "INFO"
            self.log_text.insert(tk.END, f"[{timestamp}] {msg}\n", tag)
            self.log_text.see(tk.END)
        
        self.root.after(0, _write)
    
    def _toggle_send(self):
        """切换发送/停止"""
        if not self.is_sending:
            self._start_send()
        else:
            self._cancel_send()
    
    def _start_send(self):
        """开始发送"""
        # 验证
        port_str = self.selected_port.get()
        if not port_str:
            messagebox.showwarning("提示", "请先选择串口")
            return
        
        filepath = self.selected_file.get()
        if not filepath or not os.path.isfile(filepath):
            messagebox.showwarning("提示", "请选择要发送的文件")
            return
        
        # 提取串口名
        port = port_str.split(" - ")[0]
        baudrate = int(self.selected_baud.get())
        
        # 禁用界面
        self.is_sending = True
        self.send_button_text.set("⏳ 发送中...")
        self.send_btn.configure(state=tk.DISABLED)
        self.cancel_btn.configure(state=tk.NORMAL)
        self.refresh_btn.configure(state=tk.DISABLED)
        self.progress.start(10)
        
        self._log("=" * 50, "HEADER")
        self._log(f"开始传输: 串口={port}, 波特率={baudrate}", "HEADER")
        self._log(f"文件: {filepath} ({os.path.getsize(filepath):,} 字节)", "HEADER")
        self._log("=" * 50, "HEADER")
        
        # 启动发送线程
        self.send_thread = threading.Thread(
            target=self._send_file_thread,
            args=(port, baudrate, filepath),
            daemon=True
        )
        self.send_thread.start()
    
    def _send_file_thread(self, port, baudrate, filepath):
        """发送线程"""
        ymodem = YModem(port, baudrate, log_callback=self._log)
        self.ymodem = ymodem
        
        success = False
        try:
            if ymodem.open():
                self._log("⏳ 请确保接收端已准备好并处于接收模式...", "INFO")
                self._log("⏳ 接收端应显示等待或发送 'C' 字符...", "INFO")
                success = ymodem.send_file(filepath)
            else:
                self._log("❌ 串口打开失败，请检查串口是否被占用", "ERROR")
        except Exception as e:
            self._log(f"❌ 传输异常: {e}", "ERROR")
        finally:
            ymodem.close()
            self.ymodem = None
            self.root.after(0, self._send_done, success)
    
    def _send_done(self, success):
        """发送完成后的界面恢复"""
        self.is_sending = False
        self.send_button_text.set("📤 发送文件")
        self.send_btn.configure(state=tk.NORMAL)
        self.cancel_btn.configure(state=tk.DISABLED)
        self.refresh_btn.configure(state=tk.NORMAL)
        self.progress.stop()
        
        if success:
            self._log("✅ 传输成功完成！", "OK")
        else:
            self._log("❌ 传输失败或已取消", "ERROR")
        
        self._log("=" * 50, "HEADER")
    
    def _cancel_send(self):
        """取消发送"""
        if self.ymodem:
            self._log("⏹️ 用户取消传输", "WARNING")
            self.ymodem.cancel()
        self.cancel_btn.configure(state=tk.DISABLED)
    
    def _clear_log(self):
        """清空日志"""
        self.log_text.delete(1.0, tk.END)


# ========== 主程序 ==========

if __name__ == "__main__":
    root = tk.Tk()
    app = YModemGUI(root)
    root.mainloop()
