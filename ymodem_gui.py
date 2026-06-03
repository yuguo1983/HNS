"""
YMODEM 串口文件发送工具 - GUI 版
基于 tkinter + pyserial
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import os
import sys
import time
import serial
import serial.tools.list_ports


def calc_checksum(data: bytes) -> int:
    """计算校验和"""
    return sum(data) & 0xFF


class YModemSender:
    """YMODEM 协议发送器"""

    SOH = b'\x01'  # 128字节数据块
    STX = b'\x02'  # 1024字节数据块
    EOT = b'\x04'
    ACK = b'\x06'
    NAK = b'\x15'
    CAN = b'\x18'
    C = b'\x43'  # 'C'

    def __init__(self, port: str, baudrate: int = 115200, timeout: int = 10):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.ser = None
        self.cancel_flag = False

    def connect(self) -> bool:
        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=8,
                parity='N',
                stopbits=1,
                timeout=self.timeout,
                xonxoff=False,
                rtscts=False
            )
            return True
        except Exception as e:
            raise Exception(f"打开串口失败: {e}")

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _read_byte(self) -> bytes:
        data = self.ser.read(1)
        if not data:
            raise TimeoutError("接收超时")
        return data

    def _wait_for(self, expected: bytes, max_retries: int = 50) -> bool:
        """等待接收指定的字节"""
        for _ in range(max_retries):
            if self.cancel_flag:
                raise InterruptedError("用户取消")
            try:
                byte = self._read_byte()
                if byte == expected:
                    return True
            except TimeoutError:
                pass
        return False

    def _send_packet(self, seq: int, data: bytes, is_last: bool = False) -> bool:
        """发送一个数据包"""
        if self.cancel_flag:
            raise InterruptedError("用户取消")

        seq_byte = seq & 0xFF
        seq_comp = (~seq_byte) & 0xFF

        if len(data) == 1024:
            header = self.STX
        elif len(data) == 128:
            header = self.SOH
        else:
            # 填充到128字节
            data = data + b'\x1a' * (128 - len(data))
            header = self.SOH

        packet = header + bytes([seq_byte, seq_comp]) + data

        if len(data) == 128:
            packet += bytes([calc_checksum(data)])
        else:
            packet += bytes([calc_checksum(data[:256])])  # 简化处理

        self.ser.write(packet)

        # 等待 ACK
        for _ in range(10):
            if self.cancel_flag:
                raise InterruptedError("用户取消")
            try:
                resp = self._read_byte()
                if resp == self.ACK:
                    return True
                elif resp == self.NAK:
                    return False  # 重发
                elif resp == self.CAN:
                    raise Exception("对方取消了传输")
            except TimeoutError:
                pass
        return False

    def send_file(self, filepath: str, progress_callback=None) -> str:
        """发送文件，返回文件名"""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"文件不存在: {filepath}")

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        self.cancel_flag = False

        if not self.ser or not self.ser.is_open:
            self.connect()

        # ----- 阶段1: 发送文件名头部 -----
        # 等待接收方发送 'C'
        self.ser.reset_input_buffer()
        self.ser.write(self.C)
        time.sleep(0.1)

        if not self._wait_for(self.C, max_retries=100):
            raise Exception("等待接收方就绪超时，请确认接收端已启动")

        # 构造文件名包: filename\0filesize
        header_data = f"{filename}\x00{filesize}".encode()
        header_data = header_data + b'\x00' * (128 - len(header_data))

        seq = 0
        for _ in range(10):
            if self.cancel_flag:
                raise InterruptedError("用户取消")
            try:
                self.ser.write(self.SOH + bytes([seq, (~seq) & 0xFF]) + header_data + bytes([calc_checksum(header_data)]))
                resp = self._read_byte()
                if resp == self.ACK:
                    break
                elif resp == self.CAN:
                    raise Exception("对方取消了传输")
            except TimeoutError:
                continue
        else:
            raise Exception("发送文件名头失败")

        # 等待接收方发送 'C' 开始数据
        if not self._wait_for(self.C, max_retries=50):
            raise Exception("等待接收方就绪超时")

        # ----- 阶段2: 发送文件数据 -----
        seq = 1
        sent_bytes = 0
        with open(filepath, 'rb') as f:
            while True:
                if self.cancel_flag:
                    raise InterruptedError("用户取消")

                chunk = f.read(128)
                if not chunk:
                    break

                if len(chunk) < 128:
                    chunk = chunk + b'\x1a' * (128 - len(chunk))

                for retry in range(10):
                    if self.cancel_flag:
                        raise InterruptedError("用户取消")
                    try:
                        self.ser.write(self.SOH + bytes([seq & 0xFF, (~seq) & 0xFF]) + chunk + bytes([calc_checksum(chunk)]))
                        resp = self._read_byte()
                        if resp == self.ACK:
                            break
                    except TimeoutError:
                        continue
                else:
                    raise Exception("数据包发送失败")

                sent_bytes += min(128, filesize - sent_bytes)
                seq = (seq + 1) & 0xFF

                if progress_callback:
                    progress_callback(sent_bytes, filesize)

        # ----- 阶段3: 发送 EOT -----
        for _ in range(3):
            if self.cancel_flag:
                raise InterruptedError("用户取消")
            self.ser.write(self.EOT)
            try:
                resp = self._read_byte()
                if resp == self.ACK:
                    break
            except TimeoutError:
                continue

        # 等待 NAK
        try:
            resp = self._read_byte()
        except TimeoutError:
            pass

        # 发送结束包
        self.ser.write(self.SOH + bytes([0, 0xFF]) + b'\x00' * 128 + bytes([0]))
        try:
            self._read_byte()
        except TimeoutError:
            pass

        return filename


class YModemGUI:
    """YMODEM 图形界面"""

    def __init__(self, root):
        self.root = root
        self.root.title("YMODEM 串口文件发送工具")
        self.root.geometry("580x480")
        self.root.resizable(False, False)
        self.root.configure(bg='#f0f0f0')

        self.sender = None
        self.send_thread = None
        self.selected_file = ""

        self._setup_styles()
        self._create_widgets()
        self._refresh_ports()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TLabel', background='#f0f0f0', font=('Microsoft YaHei', 10))
        style.configure('TButton', font=('Microsoft YaHei', 10))
        style.configure('Header.TLabel', font=('Microsoft YaHei', 12, 'bold'))

    def _create_widgets(self):
        # ---- 标题 ----
        header = ttk.Label(self.root, text="📡 YMODEM 串口文件发送", style='Header.TLabel')
        header.pack(pady=(15, 5))

        # ---- 主框架 ----
        main_frame = ttk.Frame(self.root, padding=15)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ---- 串口设置 ----
        ttk.Label(main_frame, text="串口设置", font=('Microsoft YaHei', 10, 'bold')).grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 5))

        ttk.Label(main_frame, text="串口:").grid(row=1, column=0, sticky='w', padx=5, pady=3)
        self.port_combo = ttk.Combobox(main_frame, width=22, state='readonly')
        self.port_combo.grid(row=1, column=1, padx=5, pady=3, sticky='ew')
        ttk.Button(main_frame, text="🔄 刷新", width=8, command=self._refresh_ports).grid(row=1, column=2, padx=5, pady=3)

        ttk.Label(main_frame, text="波特率:").grid(row=2, column=0, sticky='w', padx=5, pady=3)
        self.baud_combo = ttk.Combobox(main_frame, width=22, state='readonly',
                                       values=['9600', '19200', '38400', '57600', '115200', '230400', '460800', '921600'])
        self.baud_combo.set('115200')
        self.baud_combo.grid(row=2, column=1, padx=5, pady=3, sticky='ew')

        # ---- 分隔线 ----
        ttk.Separator(main_frame, orient='horizontal').grid(row=3, column=0, columnspan=3, sticky='ew', pady=10)

        # ---- 文件选择 ----
        ttk.Label(main_frame, text="文件设置", font=('Microsoft YaHei', 10, 'bold')).grid(row=4, column=0, columnspan=3, sticky='w', pady=(0, 5))

        self.file_path_var = tk.StringVar()
        ttk.Entry(main_frame, textvariable=self.file_path_var, width=30, state='readonly').grid(row=5, column=0, columnspan=2, padx=5, pady=3, sticky='ew')
        ttk.Button(main_frame, text="📁 选择文件", width=10, command=self._select_file).grid(row=5, column=2, padx=5, pady=3)

        # ---- 文件信息 ----
        self.info_var = tk.StringVar(value="未选择文件")
        ttk.Label(main_frame, textvariable=self.info_var, foreground='#666').grid(row=6, column=0, columnspan=3, sticky='w', padx=5, pady=2)

        # ---- 分隔线 ----
        ttk.Separator(main_frame, orient='horizontal').grid(row=7, column=0, columnspan=3, sticky='ew', pady=10)

        # ---- 进度条 ----
        self.progress = ttk.Progressbar(main_frame, length=450, mode='determinate')
        self.progress.grid(row=8, column=0, columnspan=3, padx=5, pady=5, sticky='ew')

        self.progress_label = ttk.Label(main_frame, text="就绪")
        self.progress_label.grid(row=9, column=0, columnspan=3, padx=5, pady=2)

        # ---- 操作按钮 ----
        btn_frame = ttk.Frame(main_frame)
        btn_frame.grid(row=10, column=0, columnspan=3, pady=10)

        self.send_btn = ttk.Button(btn_frame, text="🚀 发送文件", width=15, command=self._start_send)
        self.send_btn.pack(side=tk.LEFT, padx=5)

        self.cancel_btn = ttk.Button(btn_frame, text="⏹ 取消", width=10, command=self._cancel_send, state='disabled')
        self.cancel_btn.pack(side=tk.LEFT, padx=5)

        # ---- 日志区域 ----
        ttk.Label(main_frame, text="日志输出", font=('Microsoft YaHei', 10, 'bold')).grid(row=11, column=0, columnspan=3, sticky='w', pady=(5, 0))

        log_frame = ttk.Frame(main_frame)
        log_frame.grid(row=12, column=0, columnspan=3, sticky='nsew', pady=5)

        self.log_text = tk.Text(log_frame, height=8, width=65, wrap=tk.WORD, font=('Consolas', 9),
                                bg='#1e1e1e', fg='#d4d4d4', insertbackground='white')
        scrollbar = ttk.Scrollbar(log_frame, orient='vertical', command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(12, weight=1)

        self._log("🟢 YMODEM 发送工具已启动")

    def _log(self, msg: str):
        """输出日志"""
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.root.update()

    def _refresh_ports(self):
        """刷新串口列表"""
        ports = serial.tools.list_ports.comports()
        port_list = [f"{p.device} - {p.description}" for p in ports]
        self.port_combo['values'] = port_list
        if port_list:
            self.port_combo.set(port_list[0])
        else:
            self.port_combo.set("")
        self._log(f"🔍 扫描到 {len(port_list)} 个串口")

    def _select_file(self):
        """选择文件"""
        filepath = filedialog.askopenfilename(title="选择要发送的文件")
        if filepath:
            self.selected_file = filepath
            self.file_path_var.set(filepath)
            size = os.path.getsize(filepath)
            size_str = self._format_size(size)
            self.info_var.set(f"📄 {os.path.basename(filepath)}  |  大小: {size_str}")
            self._log(f"📎 已选择文件: {filepath} ({size_str})")

    def _format_size(self, size: int) -> str:
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / 1024 / 1024:.2f} MB"

    def _progress_callback(self, current: int, total: int):
        """进度回调"""
        self.root.after(0, self._update_progress, current, total)

    def _update_progress(self, current: int, total: int):
        """更新进度"""
        if total > 0:
            pct = int(current / total * 100)
            self.progress['value'] = pct
            size_str = self._format_size(current)
            total_str = self._format_size(total)
            self.progress_label.config(text=f"发送中... {pct}% ({size_str}/{total_str})")
            self.root.update()

    def _start_send(self):
        """开始发送（线程）"""
        if not self.selected_file:
            messagebox.showwarning("提示", "请先选择要发送的文件")
            return

        port_text = self.port_combo.get()
        if not port_text:
            messagebox.showwarning("提示", "请选择串口")
            return

        port = port_text.split(" - ")[0]
        baudrate = int(self.baud_combo.get())

        self.send_btn.config(state='disabled')
        self.cancel_btn.config(state='normal')

        self.progress['value'] = 0
        self.progress_label.config(text="准备发送...")
        self._log(f"🚀 开始发送 -> {port} @ {baudrate} bps")

        self.send_thread = threading.Thread(target=self._send_task, args=(port, baudrate), daemon=True)
        self.send_thread.start()

    def _send_task(self, port: str, baudrate: int):
        """发送任务"""
        try:
            self.sender = YModemSender(port, baudrate)
            self.sender.connect()
            self._log("✅ 串口已连接")

            filename = self.sender.send_file(
                self.selected_file,
                progress_callback=self._progress_callback
            )

            self.root.after(0, self._send_success, filename)

        except InterruptedError:
            self.root.after(0, self._send_cancelled)
        except Exception as e:
            self.root.after(0, self._send_error, str(e))
        finally:
            if self.sender:
                self.sender.close()

    def _send_success(self, filename: str):
        """发送成功"""
        self._log(f"✅ 发送完成: {filename}")
        self.progress_label.config(text="✅ 发送成功！")
        self.progress['value'] = 100
        self.send_btn.config(state='normal')
        self.cancel_btn.config(state='disabled')
        messagebox.showinfo("成功", f"文件 '{filename}' 发送成功！")

    def _send_error(self, error: str):
        """发送失败"""
        self._log(f"❌ 发送失败: {error}")
        self.progress_label.config(text="❌ 发送失败")
        self.send_btn.config(state='normal')
        self.cancel_btn.config(state='disabled')
        messagebox.showerror("错误", f"发送失败:\n{error}")

    def _send_cancelled(self):
        """发送取消"""
        self._log("⏹ 发送已取消")
        self.progress_label.config(text="已取消")
        self.send_btn.config(state='normal')
        self.cancel_btn.config(state='disabled')

    def _cancel_send(self):
        """取消发送"""
        if self.sender:
            self.sender.cancel_flag = True
        self.cancel_btn.config(state='disabled')
        self._log("⏹ 正在取消...")


def main():
    root = tk.Tk()
    app = YModemGUI(root)
    root.mainloop()


if __name__ == '__main__':
    main()
