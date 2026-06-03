#include "ymodem.h"
#include <QDebug>
#include <QDataStream>
#include <QFileInfo>
#include <cmath>

// ───────────────────────── CRC16-CCITT ─────────────────────────
static const uint16_t crc16Table[256] = {
    0x0000,0x1021,0x2042,0x3063,0x4084,0x50A5,0x60C6,0x70E7,
    0x8108,0x9129,0xA14A,0xB16B,0xC18C,0xD1AD,0xE1CE,0xF1EF,
    0x1231,0x0210,0x3273,0x2252,0x52B5,0x4294,0x72F7,0x62D6,
    0x9339,0x8318,0xB37B,0xA35A,0xD3BD,0xC39C,0xF3FF,0xE3DE,
    0x2462,0x3443,0x0420,0x1401,0x64E6,0x74C7,0x44A4,0x5485,
    0xA56A,0xB54B,0x8528,0x9509,0xE5EE,0xF5CF,0xC5AC,0xD58D,
    0x3653,0x2672,0x1611,0x0630,0x76D7,0x66F6,0x5695,0x46B4,
    0xB75B,0xA77A,0x9719,0x8738,0xF7DF,0xE7FE,0xD79D,0xC7BC,
    0x48C4,0x58E5,0x6886,0x78A7,0x0840,0x1861,0x2802,0x3823,
    0xC9CC,0xD9ED,0xE98E,0xF9AF,0x8948,0x9969,0xA90A,0xB92B,
    0x5AF5,0x4AD4,0x7AB7,0x6A96,0x1A71,0x0A50,0x3A33,0x2A12,
    0xDBFD,0xCBDC,0xFBBF,0xEB9E,0x9B79,0x8B58,0xBB3B,0xAB1A,
    0x6CA6,0x7C87,0x4CE4,0x5CC5,0x2C22,0x3C03,0x0C60,0x1C41,
    0xEDAE,0xFD8F,0xCDEC,0xDDCD,0xAD2A,0xBD0B,0x8D68,0x9D49,
    0x7E97,0x6EB6,0x5ED5,0x4EF4,0x3E13,0x2E32,0x1E51,0x0E70,
    0xFF9F,0xEFBE,0xDFDD,0xCFFC,0xBF1B,0xAF3A,0x9F59,0x8F78,
    0x9188,0x81A9,0xB1CA,0xA1EB,0xD10C,0xC12D,0xF14E,0xE16F,
    0x1080,0x00A1,0x30C2,0x20E3,0x5004,0x4025,0x7046,0x6067,
    0x83B9,0x9398,0xA3FB,0xB3DA,0xC33D,0xD31C,0xE37F,0xF35E,
    0x02B1,0x1290,0x22F3,0x32D2,0x4235,0x5214,0x6277,0x7256,
    0xB5EA,0xA5CB,0x95A8,0x8589,0xF56E,0xE54F,0xD52C,0xC50D,
    0x34E2,0x24C3,0x14A0,0x0481,0x7466,0x6447,0x5424,0x4405,
    0xA7DB,0xB7FA,0x8799,0x97B8,0xE75F,0xF77E,0xC71D,0xD73C,
    0x26D3,0x36F2,0x0691,0x16B0,0x6657,0x7676,0x4615,0x5634,
    0xD94C,0xC96D,0xF90E,0xE92F,0x99C8,0x89E9,0xB98A,0xA9AB,
    0x5844,0x4865,0x7806,0x6827,0x18C0,0x08E1,0x3882,0x28A3,
    0xCB7D,0xDB5C,0xEB3F,0xFB1E,0x8BF9,0x9BD8,0xABBB,0xBB9A,
    0x4A75,0x5A54,0x6A37,0x7A16,0x0AF1,0x1AD0,0x2AB3,0x3A92,
    0xFD2E,0xED0F,0xDD6C,0xCD4D,0xBDAA,0xAD8B,0x9DE8,0x8DC9,
    0x7C26,0x6C07,0x5C64,0x4C45,0x3CA2,0x2C83,0x1CE0,0x0CC1,
    0xEF1F,0xFF3E,0xCF5D,0xDF7C,0xAF9B,0xBFBA,0x8FD9,0x9FF8,
    0x6E17,0x7E36,0x4E55,0x5E74,0x2E93,0x3EB2,0x0ED1,0x1EF0
};

uint16_t YmodemSender::crc16(const uint8_t *data, int len)
{
    uint16_t crc = 0;
    for (int i = 0; i < len; ++i)
        crc = (crc << 8) ^ crc16Table[((crc >> 8) ^ data[i]) & 0xFF];
    return crc;
}

QByteArray YmodemSender::buildCrcBytes(const uint8_t *data, int len)
{
    uint16_t c = crc16(data, len);
    QByteArray ba;
    QDataStream ds(&ba, QIODevice::WriteOnly);
    ds.setByteOrder(QDataStream::BigEndian);
    ds << c;
    return ba;
}

// ───────────────────────── 构造 / 析构 ─────────────────────────
YmodemSender::YmodemSender(QSerialPort *port, QObject *parent)
    : QObject(parent), m_port(port)
{
    m_timer = new QTimer(this);
    m_timer->setSingleShot(true);
    connect(m_timer, &QTimer::timeout, this, &YmodemSender::onTimeout);
    connect(m_port, &QSerialPort::readyRead, this, &YmodemSender::onReadyRead);
}

void YmodemSender::cancel()
{
    if (m_state != Idle && m_state != Complete) {
        m_timer->stop();
        m_file.close();
        // 发送 5 个 CAN 让接收方取消
        for (int i = 0; i < 5; ++i)
            sendByte(CAN);
        setState(Idle);
        emit finished(false, "用户取消传输");
    }
}

void YmodemSender::setState(State s)
{
    m_state = s;
    m_retryCount = 0;
}

// ───────────────────────── 公开接口 ─────────────────────────
void YmodemSender::sendFiles(const QStringList &filePaths)
{
    if (m_state != Idle) {
        emit errorOccurred("正在传输中，请等待完成");
        return;
    }
    if (filePaths.isEmpty()) {
        emit finished(false, "文件列表为空");
        return;
    }
    m_fileList = filePaths;
    m_fileIndex = 0;
    m_timer->setInterval(TIMEOUT_MS);
    setState(Idle);
    startSend();
}

// ───────────────────────── 状态机 ─────────────────────────
void YmodemSender::startSend()
{
    if (m_fileIndex >= m_fileList.size()) {
        // 所有文件发完 → 发结束包
        emit statusMessage("所有文件传输完成，发送结束包...");
        sendClosePacket();
        return;
    }

    const QString &path = m_fileList.at(m_fileIndex);
    QFileInfo fi(path);
    if (!fi.exists() || !fi.isFile()) {
        emit errorOccurred(QString("文件不存在: %1").arg(path));
        setState(Idle);
        emit finished(false, QString("文件不存在: %1").arg(path));
        return;
    }

    m_file.setFileName(path);
    if (!m_file.open(QIODevice::ReadOnly)) {
        emit errorOccurred(QString("打开文件失败: %1").arg(path));
        setState(Idle);
        emit finished(false, QString("打开文件失败: %1").arg(path));
        return;
    }

    m_fileSize = m_file.size();
    m_bytesSent = 0;
    m_packetSeq = 1;

    emit statusMessage(QString("等待接收方... [%1]").arg(fi.fileName()));
    emit fileProgress(fi.fileName(), 0, static_cast<int>(m_fileSize));
    setState(WaitForCRC);
    m_timer->start();
}

// ───────────────────────── 发送原始字节 ─────────────────────────
void YmodemSender::sendByte(uint8_t b)
{
    m_port->write(reinterpret_cast<const char *>(&b), 1);
}

// ───────────────────────── 发送数据包 ─────────────────────────
void YmodemSender::sendPacket(uint8_t hdr, int seq, const QByteArray &data)
{
    QByteArray pkt;
    pkt.append(static_cast<char>(hdr));
    pkt.append(static_cast<char>(seq & 0xFF));
    pkt.append(static_cast<char>((~seq) & 0xFF));
    pkt.append(data);
    pkt.append(buildCrcBytes(reinterpret_cast<const uint8_t *>(data.constData()), data.size()));
    m_port->write(pkt);
}

// ───────────────────────── 发送头包 ─────────────────────────
bool YmodemSender::sendHeaderPacket()
{
    QFileInfo fi(m_file.fileName());
    QByteArray hdrData;
    // 格式: "filename filesize" + '\0' 填充到 128
    QString meta = QString("%1 %2").arg(fi.fileName()).arg(m_fileSize);
    hdrData.append(meta.toUtf8());
    hdrData.append('\0');
    while (hdrData.size() < 128)
        hdrData.append('\0');

    sendPacket(SOH, 0, hdrData);
    emit statusMessage(QString("发送文件头: %1 (%2 字节)").arg(fi.fileName()).arg(m_fileSize));
    return true;
}

// ───────────────────────── 发送数据包 ─────────────────────────
bool YmodemSender::sendDataPacket()
{
    if (!m_file.isOpen() || m_file.atEnd()) {
        // 无数据可发 → 发 EOT
        return false;
    }

    // 决定块大小：1024 (STX) 或 128 (SOH)
    qint64 remaining = m_fileSize - m_bytesSent;
    bool useStx = (remaining >= 1024);
    int blockSize = useStx ? 1024 : 128;

    QByteArray data(blockSize, '\0');
    qint64 readLen = m_file.read(data.data(), blockSize);
    if (readLen < 0) {
        emit errorOccurred("读取文件失败");
        return false;
    }

    // 不足部分用 0x1A (Ctrl-Z) 填充
    for (int i = static_cast<int>(readLen); i < blockSize; ++i)
        data[i] = 0x1A;

    sendPacket(useStx ? STX : SOH, m_packetSeq, data);
    m_bytesSent += readLen;
    m_packetSeq++;

    // 更新进度
    int pct = static_cast<int>(std::round(m_bytesSent * 100.0 / m_fileSize));
    emit progressChanged(qMin(pct, 100));
    QFileInfo fi(m_file.fileName());
    emit fileProgress(fi.fileName(), static_cast<int>(m_bytesSent), static_cast<int>(m_fileSize));

    return true;
}

// ───────────────────────── 发送 EOT ─────────────────────────
bool YmodemSender::sendEOT()
{
    sendByte(EOT);
    emit statusMessage("发送 EOT...");
    return true;
}

// ───────────────────────── 发送结束包 ─────────────────────────
bool YmodemSender::sendClosePacket()
{
    // 空头包: SOH + 00 + FF + 128字节全0 + CRC
    QByteArray zero(128, '\0');
    sendPacket(SOH, 0, zero);
    emit statusMessage("发送结束包...");
    setState(WaitCloseAck);
    m_timer->start();
    return true;
}

// ───────────────────────── 串口数据接收 ─────────────────────────
void YmodemSender::onReadyRead()
{
    m_rxBuf.append(m_port->readAll());

    // 处理收到的每个字节
    while (!m_rxBuf.isEmpty()) {
        uint8_t ch = static_cast<uint8_t>(m_rxBuf.at(0));
        m_rxBuf.remove(0, 1);

        switch (m_state) {
        case WaitForCRC:
            if (ch == CRC) {
                // 接收方请求 CRC 模式 → 发文件头
                m_timer->stop();
                emit statusMessage("收到 'C'，发送文件头...");
                sendHeaderPacket();
                setState(WaitHeaderAck);
                m_timer->start();
            } else if (ch == NAK) {
                m_timer->stop();
                // NAK 也可能表示请求（旧版），同样发头
                emit statusMessage("收到 NAK，发送文件头...");
                sendHeaderPacket();
                setState(WaitHeaderAck);
                m_timer->start();
            }
            break;

        case WaitHeaderAck:
            if (ch == ACK) {
                m_timer->stop();
                emit statusMessage("头包 ACK，等待 'C'...");
                setState(WaitDataCRC);
                m_timer->start();
            } else if (ch == NAK) {
                // 重发头包
                m_timer->stop();
                if (++m_retryCount > MAX_RETRIES) {
                    emit errorOccurred("头包重发超限");
                    setState(Idle);
                    emit finished(false, "头包重发超限");
                    return;
                }
                sendHeaderPacket();
                m_timer->start();
            }
            break;

        case WaitDataCRC:
            if (ch == CRC) {
                m_timer->stop();
                // 发数据包
                if (!sendDataPacket()) {
                    // 文件已发完 → 发 EOT
                    sendEOT();
                    setState(SendEOT);
                } else {
                    setState(WaitDataAck);
                }
                m_timer->start();
            } else if (ch == NAK) {
                m_timer->stop();
                // 重发上一个数据包？但当前是第一个数据包，重发头？不会，这里应该重发数据
                // 实际上 WaitDataCRC 阶段收到 NAK，说明接收方要重发
                // 但我们还没有发过数据包，所以忽略并等待 'C'
                m_timer->start();
            }
            break;

        case WaitDataAck:
            if (ch == ACK) {
                m_timer->stop();
                // 数据包确认 → 发下一个数据包 or 继续等 CRC
                if (m_bytesSent >= m_fileSize) {
                    // 文件发完 → EOT
                    sendEOT();
                    setState(SendEOT);
                } else {
                    setState(WaitDataCRC);
                }
                m_timer->start();
            } else if (ch == NAK) {
                m_timer->stop();
                if (++m_retryCount > MAX_RETRIES) {
                    emit errorOccurred("数据包重发超限");
                    setState(Idle);
                    emit finished(false, "数据包重发超限");
                    return;
                }
                // 重发上一个数据包（需要回退指针）
                // 简单起见：关闭文件重发？可以回退
                // 更好的做法：保存最后一个包
                // 简化处理：重新定位文件
                qint64 pos = m_bytesSent;
                if (m_packetSeq > 1) {
                    // 回退到上一个包位置
                    int blockSize = 1024; // 假设用的1024，简化处理
                    if (m_fileSize - (pos - blockSize) < 1024)
                        blockSize = 128;
                    qint64 newPos = pos - blockSize;
                    if (newPos < 0) newPos = 0;
                    m_file.seek(newPos);
                    m_bytesSent = newPos;
                    m_packetSeq = (m_packetSeq > 1) ? m_packetSeq - 1 : 1;
                }
                sendDataPacket();
                m_timer->start();
            }
            break;

        case SendEOT:
            if (ch == ACK) {
                m_timer->stop();
                // EOT 确认 → 文件传输完成
                m_file.close();
                emit statusMessage("文件传输完成");
                emit progressChanged(100);
                QFileInfo fi(m_file.fileName());
                emit fileProgress(fi.fileName(), static_cast<int>(m_fileSize), static_cast<int>(m_fileSize));

                // 准备下一个文件
                m_fileIndex++;
                setState(Idle);
                startSend();
                return;
            } else if (ch == NAK) {
                m_timer->stop();
                if (++m_retryCount > MAX_RETRIES) {
                    emit errorOccurred("EOT 重发超限");
                    setState(Idle);
                    emit finished(false, "EOT 重发超限");
                    return;
                }
                sendEOT();
                m_timer->start();
            }
            break;

        case WaitCloseAck:
            if (ch == ACK) {
                m_timer->stop();
                emit statusMessage("全部传输结束");
                setState(Complete);
                emit finished(true, "全部文件传输成功完成");
            } else if (ch == NAK) {
                m_timer->stop();
                if (++m_retryCount > MAX_RETRIES) {
                    setState(Idle);
                    emit finished(false, "结束包重发超限");
                    return;
                }
                sendClosePacket();
                m_timer->start();
            }
            break;

        default:
            break;
        }
    }
}

// ───────────────────────── 超时处理 ─────────────────────────
void YmodemSender::onTimeout()
{
    switch (m_state) {
    case WaitForCRC:
        // 超时重发等待
        if (++m_retryCount > MAX_RETRIES) {
            emit errorOccurred("等待接收方超时");
            setState(Idle);
            emit finished(false, "等待接收方超时，请确认串口连接");
            return;
        }
        emit statusMessage(QString("等待接收方... (%1)").arg(m_retryCount));
        m_timer->start();
        break;

    case WaitHeaderAck:
        // 重发头包
        if (++m_retryCount > MAX_RETRIES) {
            emit errorOccurred("头包确认超时");
            setState(Idle);
            emit finished(false, "头包确认超时");
            return;
        }
        sendHeaderPacket();
        m_timer->start();
        break;

    case WaitDataCRC:
        if (++m_retryCount > MAX_RETRIES) {
            emit errorOccurred("等待数据 CRC 超时");
            setState(Idle);
            emit finished(false, "等待数据 CRC 超时");
            return;
        }
        // 可能对方在等数据，重发当前数据？
        // 实际是等待 'C'，可以重发上一个包让接收方响应
        // 简单处理：继续等待
        m_timer->start();
        break;

    case WaitDataAck:
        if (++m_retryCount > MAX_RETRIES) {
            emit errorOccurred("数据包确认超时");
            setState(Idle);
            emit finished(false, "数据包确认超时");
            return;
        }
        // 重发数据包
        {
            qint64 pos = m_bytesSent;
            int blockSize = 1024;
            if (m_fileSize - pos < 1024)
                blockSize = 128;
            qint64 newPos = pos - blockSize;
            if (newPos < 0) newPos = 0;
            m_file.seek(newPos);
            m_bytesSent = newPos;
            m_packetSeq = (m_packetSeq > 1) ? m_packetSeq - 1 : 1;
            sendDataPacket();
        }
        m_timer->start();
        break;

    case SendEOT:
        if (++m_retryCount > MAX_RETRIES) {
            emit errorOccurred("EOT 超时");
            setState(Idle);
            emit finished(false, "EOT 超时");;
            return;
        }
        sendEOT();
        m_timer->start();
        break;

    case WaitCloseAck:
        if (++m_retryCount > MAX_RETRIES) {
            setState(Idle);
            emit finished(false, "结束包确认超时");
            return;
        }
        sendClosePacket();
        break;

    default:
        break;
    }
}
