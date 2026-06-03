#ifndef YMODEM_H
#define YMODEM_H

#include <QObject>
#include <QSerialPort>
#include <QFile>
#include <QTimer>

/// YMODEM 协议控制字符
enum YmodemChar : uint8_t {
    SOH = 0x01,   // 128 字节数据块
    STX = 0x02,   // 1024 字节数据块
    EOT = 0x04,   // 传输结束
    ACK = 0x06,   // 确认
    NAK = 0x15,   // 否定确认
    CAN = 0x18,   // 取消
    CRC = 0x43,   // 'C' — 请求 CRC-16 模式
};

/// YMODEM 文件发送器
class YmodemSender : public QObject
{
    Q_OBJECT
public:
    explicit YmodemSender(QSerialPort *port, QObject *parent = nullptr);

    /// 发送一个或多个文件
    void sendFiles(const QStringList &filePaths);

    /// 取消当前传输
    void cancel();

signals:
    void statusMessage(const QString &msg);
    void progressChanged(int percent);       // 0~100
    void fileProgress(const QString &file, int cur, int total); // 当前文件进度
    void finished(bool ok, const QString &msg);
    void errorOccurred(const QString &err);

private slots:
    void onReadyRead();
    void onTimeout();

private:
    enum State {
        Idle,
        WaitForCRC,
        SendHeader,
        WaitHeaderAck,
        WaitDataCRC,
        SendData,
        WaitDataAck,
        SendEOT,
        WaitEOTAck,
        SendEOT2,
        SendClose,
        WaitCloseAck,
        Complete
    };

    QSerialPort *m_port = nullptr;
    QTimer      *m_timer = nullptr;

    State       m_state = Idle;
    QStringList m_fileList;
    int         m_fileIndex = 0;
    QFile       m_file;
    qint64      m_fileSize = 0;
    qint64      m_bytesSent = 0;

    int         m_packetSeq = 0;
    int         m_retryCount = 0;
    static constexpr int MAX_RETRIES = 15;
    static constexpr int TIMEOUT_MS  = 3000;

    // 内部缓冲区（收）
    QByteArray  m_rxBuf;

    void setState(State s);
    void startSend();

    void sendPacket(uint8_t hdr, int seq, const QByteArray &data);
    bool sendHeaderPacket();
    bool sendDataPacket();
    bool sendEOT();
    bool sendClosePacket();
    void sendByte(uint8_t b);

    static uint16_t crc16(const uint8_t *data, int len);
    static QByteArray buildCrcBytes(const uint8_t *data, int len);
};

#endif // YMODEM_H
