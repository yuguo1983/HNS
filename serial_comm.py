"""
===========================================================
 SerialComm — Python串口通信封装类
===========================================================
基于 pyserial 库，提供简洁一致的串口通信接口。
支持：端口扫描、打开/关闭、参数配置、读写数据、
超时控制、异步接收回调。

依赖: pyserial>=3.0
安装: pip install pyserial

API 摘要
--------
class SerialComm:
    scan_ports()                       -> list[dict]
    open(port, baudrate, ...)          -> bool
    close()
    write(data: bytes)                 -> int
    read(size: int = 1)                -> bytes
    read_until(terminator: bytes)      -> bytes
    set_callback(callable)             -> None
    is_open                            -> bool
    port                               -> str | None
    settings                            -> dict

版本: 1.0.0
作者: SerialComm Module
===========================================================
"""

import threading
import time
from typing import Optional, Callable, List

try:
    import serial
    import serial.tools.list_ports as list_ports
except ImportError:
    raise ImportError(
        "请先安装 pyserial 库: pip install pyserial"
    )


class SerialComm:
    """串口通信封装类，提供同步读写与异步接收回调功能。"""

    # ------------------------------------------------------------------
    # 内部常量：校验位映射表 (pyserial 常量)
    # ------------------------------------------------------------------
    _PARITY_MAP = {
        'N': serial.PARITY_NONE,
        'E': serial.PARITY_EVEN,
        'O': serial.PARITY_ODD,
        'M': serial.PARITY_MARK,
        'S': serial.PARITY_SPACE,
    }

    _STOPBIT_MAP = {
        1: serial.STOPBITS_ONE,
        1.5: serial.STOPBITS_ONE_POINT_FIVE,
        2: serial.STOPBITS_TWO,
    }

    _BYTESIZE_MAP = {
        5: serial.FIVEBITS,
        6: serial.SIXBITS,
        7: serial.SEVENBITS,
        8: serial.EIGHTBITS,
    }

    def __init__(self):
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._callback: Optional[Callable[[bytes], None]] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._reader_active = False
        self._reader_terminator: Optional[bytes] = None

    # ==================================================================
    # 1. 端口扫描
    # ==================================================================
    @staticmethod
    def scan_ports() -> List[dict]:
        """
        扫描系统中所有可用的串行通信端口。

        Returns
        -------
        list[dict]
            每个元素包含以下字段:
            - device (str): 端口设备名，如 'COM3' / '/dev/ttyUSB0'
            - description (str): 厂商描述，如 'USB Serial Port'
            - hwid (str): 硬件ID
            - vid (int | None): USB 供应商 ID
            - pid (int | None): USB 产品 ID
            - serial_number (str | None): 序列号

        Example
        -------
        >>> ports = SerialComm.scan_ports()
        >>> for p in ports:
        ...     print(p['device'], p['description'])
        COM3 USB Serial Port
        """
        result = []
        for port in list_ports.comports():
            result.append({
                'device': port.device,
                'description': port.description,
                'hwid': port.hwid,
                'vid': port.vid,
                'pid': port.pid,
                'serial_number': port.serial_number,
            })
        return result

    # ==================================================================
    # 2. 打开串口
    # ==================================================================
    def open(
        self,
        port: str,
        baudrate: int = 9600,
        bytesize: int = 8,
        parity: str = 'N',
        stopbits: int | float = 1,
        timeout: Optional[float] = None,
        write_timeout: Optional[float] = None,
        xonxoff: bool = False,
        rtscts: bool = False,
    ) -> bool:
        """
        打开指定的串行端口。

        Parameters
        ----------
        port : str
            端口名，如 'COM3' (Windows) 或 '/dev/ttyUSB0' (Linux/macOS)。
        baudrate : int, default=9600
            波特率，标准值: 9600, 19200, 38400, 57600, 115200 等。
        bytesize : int, default=8
            数据位，支持 5, 6, 7, 8。
        parity : str, default='N'
            校验位: 'N'=无, 'E'=偶校验, 'O'=奇校验, 'M'=标记, 'S'=空格。
        stopbits : int | float, default=1
            停止位: 1, 1.5, 2。
        timeout : float | None, default=None
            读取超时(秒)。None=阻塞等待；0=非阻塞；>0=最多等待N秒。
        write_timeout : float | None, default=None
            写入超时(秒)。None=无超时。
        xonxoff : bool, default=False
            软件流控制 (XON/XOFF)。
        rtscts : bool, default=False
            硬件流控制 (RTS/CTS)。

        Returns
        -------
        bool
            成功打开返回 True，失败返回 False（可通过异常获取详细信息）。

        Raises
        ------
        serial.SerialException
            端口不存在、已被占用或权限不足时抛出。
        ValueError
            参数值不合法时抛出。

        Example
        -------
        >>> comm = SerialComm()
        >>> comm.open('COM3', 115200, timeout=1)
        True
        """
        # 参数校验与映射
        if parity.upper() not in self._PARITY_MAP:
            raise ValueError(
                f"不支持的校验位: {parity!r}，可选: {list(self._PARITY_MAP.keys())}"
            )
        if bytesize not in self._BYTESIZE_MAP:
            raise ValueError(
                f"不支持的数据位: {bytesize}，可选: {list(self._BYTESIZE_MAP.keys())}"
            )
        if stopbits not in self._STOPBIT_MAP:
            raise ValueError(
                f"不支持的停止位: {stopbits}，可选: {list(self._STOPBIT_MAP.keys())}"
            )

        # 如果已打开，先关闭
        self.close()

        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=baudrate,
                bytesize=self._BYTESIZE_MAP[bytesize],
                parity=self._PARITY_MAP[parity.upper()],
                stopbits=self._STOPBIT_MAP[stopbits],
                timeout=timeout,
                write_timeout=write_timeout,
                xonxoff=xonxoff,
                rtscts=rtscts,
            )
            return True
        except (serial.SerialException, ValueError) as e:
            self._serial = None
            raise e

    # ==================================================================
    # 3. 关闭串口
    # ==================================================================
    def close(self) -> None:
        """
        关闭串口，停止异步接收线程（如有）。

        关闭后可以再次调用 open() 重新打开。

        Example
        -------
        >>> comm.close()
        """
        # 停止异步接收线程
        self.stop_async_read()

        with self._lock:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None

    # ==================================================================
    # 4. 写入数据
    # ==================================================================
    def write(self, data: bytes) -> int:
        """
        向串口发送数据。

        Parameters
        ----------
        data : bytes
            要发送的字节数据。传入 str 会自动编码为 utf-8。

        Returns
        -------
        int
            实际写入的字节数。

        Raises
        ------
        serial.SerialNotOpenError
            串口未打开时抛出。
        serial.SerialTimeoutException
            写入超时时抛出（仅设置了 write_timeout 时可能发生）。

        Example
        -------
        >>> comm.write(b'AT\\r\\n')
        5
        >>> comm.write('你好')
        6
        """
        if isinstance(data, str):
            data = data.encode('utf-8')

        if self._serial is None or not self._serial.is_open:
            raise serial.SerialNotOpenError("串口未打开，请先调用 open()")

        with self._lock:
            return self._serial.write(data)

    # ==================================================================
    # 5. 读取数据
    # ==================================================================
    def read(self, size: int = 1) -> bytes:
        """
        从串口读取指定字节数的数据。

        Parameters
        ----------
        size : int, default=1
            要读取的字节数。

        Returns
        -------
        bytes
            读取到的数据。实际返回的字节数 <= size。
            如果设置了 timeout，超时后返回已读取到的数据。

        Raises
        ------
        serial.SerialNotOpenError
            串口未打开时抛出。

        Example
        -------
        >>> data = comm.read(10)       # 最多读10字节
        >>> data = comm.read()         # 读1字节
        """
        if self._serial is None or not self._serial.is_open:
            raise serial.SerialNotOpenError("串口未打开，请先调用 open()")

        with self._lock:
            return self._serial.read(size)

    def read_until(self, terminator: bytes) -> bytes:
        """
        从串口读取数据直到遇到指定的终止序列。

        Parameters
        ----------
        terminator : bytes
            终止序列，如 b'\\n'、b'\\r\\n'、b'OK' 等。

        Returns
        -------
        bytes
            包含终止序列在内的所有已读取数据。
            如果超时（已设置 timeout），则返回超时前已读取的数据（可能不含 terminator）。

        Raises
        ------
        serial.SerialNotOpenError
            串口未打开时抛出。

        Example
        -------
        >>> line = comm.read_until(b'\\n')    # 读取一行
        >>> response = comm.read_until(b'OK') # 读取直到 'OK'
        """
        if self._serial is None or not self._serial.is_open:
            raise serial.SerialNotOpenError("串口未打开，请先调用 open()")

        with self._lock:
            return self._serial.read_until(terminator)

    def read_line(self) -> bytes:
        """
        读取一行数据（以 b'\\n' 为终止符）。
        等价于 read_until(b'\\n')。

        Returns
        -------
        bytes
            包含换行符在内的一行数据。
        """
        return self.read_until(b'\n')

    def read_all(self) -> bytes:
        """
        读取当前接收缓冲区中的所有数据（非阻塞）。

        Returns
        -------
        bytes
            缓冲区中所有可用数据，无数据时返回 b''。
        """
        if self._serial is None or not self._serial.is_open:
            raise serial.SerialNotOpenError("串口未打开，请先调用 open()")

        with self._lock:
            # in_waiting 返回接收缓冲区中的字节数
            size = self._serial.in_waiting
            if size > 0:
                return self._serial.read(size)
            return b''

    # ==================================================================
    # 6. 异步接收回调
    # ==================================================================
    def set_callback(self, callback: Optional[Callable[[bytes], None]]) -> None:
        """
        设置数据接收回调函数。
        配合 start_async_read() 使用，当收到数据时会自动调用此回调。

        设置 None 可清除回调。

        Parameters
        ----------
        callback : Callable[[bytes], None] | None
            回调函数，接收一个 bytes 参数（收到的数据）。
            设置为 None 可移除已有回调。

        Example
        -------
        >>> def on_data(data: bytes):
        ...     print(f"收到: {data}")
        >>> comm.set_callback(on_data)
        >>> comm.start_async_read()
        """
        self._callback = callback

    def start_async_read(self, interval: float = 0.01) -> None:
        """
        启动异步数据接收线程。
        当串口有数据到达时，自动调用已注册的回调函数。
        线程会持续运行直到 stop_async_read() 或 close() 被调用。

        Parameters
        ----------
        interval : float, default=0.01
            轮询间隔（秒），建议 0.01~0.05。

        Raises
        ------
        RuntimeError
            串口未打开或已有异步线程在运行。
        ValueError
            未设置回调函数时抛出。

        Example
        -------
        >>> comm.start_async_read()  # 开始异步接收
        """
        if self._callback is None:
            raise ValueError("请先通过 set_callback() 设置回调函数")

        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("串口未打开，无法启动异步读取")

        if self._reader_active:
            raise RuntimeError("异步读取线程已在运行中")

        self._reader_active = True
        self._reader_thread = threading.Thread(
            target=self._async_reader_loop,
            args=(interval,),
            daemon=True,
            name="SerialComm-AsyncReader",
        )
        self._reader_thread.start()

    def stop_async_read(self) -> None:
        """
        停止异步数据接收线程。
        调用后等待线程安全退出。
        """
        self._reader_active = False
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)
        self._reader_thread = None

    def _async_reader_loop(self, interval: float) -> None:
        """异步读取线程的主循环。"""
        while self._reader_active:
            try:
                if self._serial and self._serial.is_open:
                    # 检查是否有数据可读
                    if self._serial.in_waiting > 0:
                        with self._lock:
                            data = self._serial.read(
                                self._serial.in_waiting
                            )
                        if data and self._callback:
                            try:
                                self._callback(data)
                            except Exception:
                                # 防止用户回调异常导致线程退出
                                pass
                time.sleep(interval)
            except Exception:
                if self._reader_active:
                    time.sleep(interval)

    # ==================================================================
    # 7. 属性 / 状态查询
    # ==================================================================
    @property
    def is_open(self) -> bool:
        """
        串口是否已打开。

        Returns
        -------
        bool
        """
        return self._serial is not None and self._serial.is_open

    @property
    def port(self) -> Optional[str]:
        """
        当前打开的端口名。

        Returns
        -------
        str | None
            未打开时返回 None。
        """
        if self._serial and self._serial.is_open:
            return self._serial.port
        return None

    @property
    def settings(self) -> dict:
        """
        当前串口参数配置。

        Returns
        -------
        dict
            包含以下字段:
            - port (str | None)
            - baudrate (int)
            - bytesize (int)
            - parity (str)
            - stopbits (int | float)
            - timeout (float | None)
            - write_timeout (float | None)
            - xonxoff (bool)
            - rtscts (bool)
            - is_open (bool)

        未打开时返回仅含 is_open=False 的字典。
        """
        if self._serial is None or not self._serial.is_open:
            return {'is_open': False}

        # 反向映射 pyserial 常量到可读值
        parity_rev = {v: k for k, v in self._PARITY_MAP.items()}
        stopbit_rev = {v: k for k, v in self._STOPBIT_MAP.items()}
        bytesize_rev = {v: k for k, v in self._BYTESIZE_MAP.items()}

        return {
            'is_open': True,
            'port': self._serial.port,
            'baudrate': self._serial.baudrate,
            'bytesize': bytesize_rev.get(self._serial.bytesize, self._serial.bytesize),
            'parity': parity_rev.get(self._serial.parity, self._serial.parity),
            'stopbits': stopbit_rev.get(self._serial.stopbits, self._serial.stopbits),
            'timeout': self._serial.timeout,
            'write_timeout': self._serial.write_timeout,
            'xonxoff': self._serial.xonxoff,
            'rtscts': self._serial.rtscts,
        }

    def __enter__(self):
        """支持 with 语句上下文管理器。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出 with 块时自动关闭串口。"""
        self.close()
        return False

    def __del__(self):
        """析构时自动关闭串口。"""
        self.close()

    def __repr__(self) -> str:
        if self.is_open:
            return (
                f"<SerialComm {self._serial.port} "
                f"baud={self._serial.baudrate} "
                f"open=True>"
            )
        return "<SerialComm open=False>"
