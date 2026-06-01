"""YMODEM Skill Handler - 串口文件发送"""
import struct, os, time, json
import serial
import serial.tools.list_ports

SOH, STX, EOT, ACK, NAK, CAN, CRC_CHAR = 0x01, 0x02, 0x04, 0x06, 0x15, 0x18, 0x43
MAX_RETRIES = 30

def _crc16(data):
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc

def _make_header(name, size):
    d = name.encode('ascii') + b'\x00' + str(size).encode('ascii') + b'\x00'
    d += b'\x00' * (128 - len(d))
    return bytes([SOH, 0x00, 0xFF]) + d + struct.pack('>H', _crc16(d))

def _make_packet(seq, block, size=1024):
    prefix = STX if size == 1024 else SOH
    if len(block) < size:
        block += b'\x1A' * (size - len(block))
    m = seq & 0xFF
    return bytes([prefix, m, (~m) & 0xFF]) + block + struct.pack('>H', _crc16(block))

def _make_eot():
    d = b'\x00' * 128
    return bytes([SOH, 0x00, 0xFF]) + d + struct.pack('>H', _crc16(d))

def handle_ymodem(action="", port="", file="", baudrate=115200, **kwargs):
    if action == "list_ports":
        ports = [f"{p.device} - {p.description}" for p in serial.tools.list_ports.comports()]
        return json.dumps({"ports": ports}, ensure_ascii=False)

    if action == "send":
        if not port or not file:
            return json.dumps({"error": "需要指定 port 和 file 参数"})
        if not os.path.exists(file):
            return json.dumps({"error": f"文件不存在: {file}"})

        try:
            ser = serial.Serial(port=port, baudrate=baudrate, timeout=5)
            ser.reset_input_buffer()
        except Exception as e:
            return json.dumps({"error": f"打开串口失败: {e}"})

        filename = os.path.basename(file)
        filesize = os.path.getsize(file)
        result = {"file": filename, "size": filesize, "log": []}

        def log_msg(m):
            result["log"].append(m)

        # 等待 'C'
        got_c = False
        for _ in range(60):
            if ser.in_waiting:
                b = ser.read(1)[0]
                if b == CRC_CHAR:
                    got_c = True
                    log_msg("收到 CRC 请求 'C'")
                    break
                elif b == NAK:
                    got_c = True
                    log_msg("收到 NAK (校验和模式)")
                    break
                elif b == CAN:
                    ser.close()
                    return json.dumps({"error": "对方取消传输", **result})
            time.sleep(1)

        if not got_c:
            ser.close()
            return json.dumps({"error": "超时：未收到接收方请求", **result})

        # 发送文件头
        header = _make_header(filename, filesize)
        ser.reset_input_buffer()
        ser.write(header)
        log_msg(f"文件头发送: {filename} ({filesize} bytes)")

        ack = ser.read(1)
        if not ack or ack[0] != ACK:
            ser.close()
            return json.dumps({"error": f"文件头未收到 ACK, 收到 {ack.hex() if ack else '超时'}", **result})

        # 发送数据
        seq = 1
        with open(file, "rb") as f:
            while True:
                block = f.read(1024)
                if not block:
                    break
                packet = _make_packet(seq, block)
                for retry in range(MAX_RETRIES):
                    ser.write(packet)
                    ack = ser.read(1)
                    if ack and ack[0] == ACK:
                        break
                    if ack and ack[0] == CAN:
                        ser.close()
                        return json.dumps({"error": f"传输取消 (包 {seq})", **result})
                else:
                    ser.close()
                    return json.dumps({"error": f"包 {seq} 发送失败 (重试耗尽)", **result})
                seq += 1

        # 发送 EOT
        eot = _make_eot()
        ser.write(bytes([EOT]))
        ack = ser.read(1)
        if ack and ack[0] == ACK:
            ser.write(eot)
            ser.close()
            log_msg(f"传输完成: {seq-1} 个数据包")
            result["status"] = "ok"
            result["packets"] = seq - 1
        else:
            ser.close()
            return json.dumps({"error": "EOT 未收到 ACK", **result})

        return json.dumps(result, ensure_ascii=False)

    return json.dumps({"error": f"未知 action: {action}"})
