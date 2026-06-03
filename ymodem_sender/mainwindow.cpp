#include "mainwindow.h"
#include "ymodem.h"

#include <QVBoxLayout>
#include <QHBoxLayout>
#include <QGroupBox>
#include <QFormLayout>
#include <QSerialPortInfo>
#include <QFileDialog>
#include <QMessageBox>
#include <QDateTime>
#include <QFileInfo>

// ───────────────────────── 构造 ─────────────────────────
MainWindow::MainWindow(QWidget *parent)
    : QMainWindow(parent)
{
    setWindowTitle("YMODEM 文件发送器");
    setMinimumSize(640, 520);

    m_serial = new QSerialPort(this);
    m_ymodem = new YmodemSender(m_serial, this);

    connect(m_ymodem, &YmodemSender::statusMessage,   this, &MainWindow::onStatusMessage);
    connect(m_ymodem, &YmodemSender::progressChanged, this, &MainWindow::onProgressChanged);
    connect(m_ymodem, &YmodemSender::fileProgress,    this, &MainWindow::onFileProgress);
    connect(m_ymodem, &YmodemSender::finished,        this, &MainWindow::onFinished);
    connect(m_ymodem, &YmodemSender::errorOccurred,   this, &MainWindow::onError);

    setupUI();
    refreshPorts();
    updateUiState();
}

MainWindow::~MainWindow()
{
    if (m_serial->isOpen())
        m_serial->close();
}

// ───────────────────────── UI 构建 ─────────────────────────
void MainWindow::setupUI()
{
    auto *central = new QWidget(this);
    setCentralWidget(central);

    auto *mainLayout = new QVBoxLayout(central);
    mainLayout->setSpacing(8);

    // ── 串口设置 ──
    auto *portGroup = new QGroupBox("串口设置");
    auto *portLayout = new QHBoxLayout(portGroup);

    m_portCombo = new QComboBox;
    m_portCombo->setMinimumWidth(180);

    m_baudCombo = new QComboBox;
    m_baudCombo->addItems({"9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600"});
    m_baudCombo->setCurrentText("115200");

    m_btnRefresh = new QPushButton("刷新");
    m_btnOpen    = new QPushButton("打开串口");

    portLayout->addWidget(new QLabel("串口:"));
    portLayout->addWidget(m_portCombo);
    portLayout->addWidget(new QLabel("波特率:"));
    portLayout->addWidget(m_baudCombo);
    portLayout->addWidget(m_btnRefresh);
    portLayout->addWidget(m_btnOpen);
    portLayout->addStretch();

    mainLayout->addWidget(portGroup);

    // ── 文件列表 ──
    auto *fileGroup = new QGroupBox("文件列表");
    auto *fileLayout = new QVBoxLayout(fileGroup);

    m_fileList = new QListWidget;

    auto *btnLayout = new QHBoxLayout;
    m_btnAdd    = new QPushButton("添加文件");
    m_btnRemove = new QPushButton("移除选中");
    m_btnClear  = new QPushButton("清空列表");
    btnLayout->addWidget(m_btnAdd);
    btnLayout->addWidget(m_btnRemove);
    btnLayout->addWidget(m_btnClear);
    btnLayout->addStretch();

    fileLayout->addWidget(m_fileList);
    fileLayout->addLayout(btnLayout);

    mainLayout->addWidget(fileGroup);

    // ── 进度与发送控制 ──
    auto *ctrlLayout = new QHBoxLayout;
    m_btnSend   = new QPushButton("开始发送");
    m_btnCancel = new QPushButton("取消");
    m_progress  = new QProgressBar;
    m_progress->setMinimum(0);
    m_progress->setMaximum(100);

    ctrlLayout->addWidget(m_btnSend);
    ctrlLayout->addWidget(m_btnCancel);
    ctrlLayout->addWidget(m_progress, 1);

    mainLayout->addLayout(ctrlLayout);

    // ── 文件进度信息 ──
    m_fileLabel = new QLabel("就绪");
    mainLayout->addWidget(m_fileLabel);

    // ── 日志 ──
    m_logView = new QTextEdit;
    m_logView->setReadOnly(true);
    m_logView->setMaximumHeight(150);
    mainLayout->addWidget(m_logView, 1);

    // ── 信号连接 ──
    connect(m_btnRefresh, &QPushButton::clicked, this, &MainWindow::refreshPorts);
    connect(m_btnOpen,    &QPushButton::clicked, this, &MainWindow::openSerialPort);
    connect(m_btnAdd,     &QPushButton::clicked, this, &MainWindow::addFiles);
    connect(m_btnRemove,  &QPushButton::clicked, this, &MainWindow::removeFile);
    connect(m_btnClear,   &QPushButton::clicked, this, &MainWindow::clearFiles);
    connect(m_btnSend,    &QPushButton::clicked, this, &MainWindow::startTransfer);
    connect(m_btnCancel,  &QPushButton::clicked, this, &MainWindow::cancelTransfer);
}

// ───────────────────────── 串口操作 ─────────────────────────
void MainWindow::refreshPorts()
{
    m_portCombo->clear();
    const auto ports = QSerialPortInfo::availablePorts();
    for (const auto &p : ports)
        m_portCombo->addItem(p.portName() + " - " + p.description(), p.portName());
    if (m_portCombo->count() == 0)
        m_portCombo->addItem("(无可用串口)");
}

void MainWindow::openSerialPort()
{
    if (m_portOpen) {
        closeSerialPort();
        return;
    }

    QString portName = m_portCombo->currentData().toString();
    if (portName.isEmpty()) {
        QMessageBox::warning(this, "提示", "请选择有效串口");
        return;
    }

    m_serial->setPortName(portName);
    m_serial->setBaudRate(m_baudCombo->currentText().toInt());
    m_serial->setDataBits(QSerialPort::Data8);
    m_serial->setParity(QSerialPort::NoParity);
    m_serial->setStopBits(QSerialPort::OneStop);
    m_serial->setFlowControl(QSerialPort::NoFlowControl);

    if (!m_serial->open(QIODevice::ReadWrite)) {
        QMessageBox::critical(this, "错误", "打开串口失败:\n" + m_serial->errorString());
        return;
    }

    m_portOpen = true;
    m_btnOpen->setText("关闭串口");
    m_portCombo->setEnabled(false);
    m_baudCombo->setEnabled(false);
    m_btnRefresh->setEnabled(false);
    log("串口已打开: " + portName + " @" + m_baudCombo->currentText());
    updateUiState();
}

void MainWindow::closeSerialPort()
{
    if (m_serial->isOpen()) {
        m_serial->close();
        m_ymodem->cancel();
    }
    m_portOpen = false;
    m_btnOpen->setText("打开串口");
    m_portCombo->setEnabled(true);
    m_baudCombo->setEnabled(true);
    m_btnRefresh->setEnabled(true);
    log("串口已关闭");
    updateUiState();
}

// ───────────────────────── 文件操作 ─────────────────────────
void MainWindow::addFiles()
{
    QStringList files = QFileDialog::getOpenFileNames(this, "选择文件");
    for (const auto &f : files) {
        // 避免重复
        bool dup = false;
        for (int i = 0; i < m_fileList->count(); ++i)
            if (m_fileList->item(i)->data(Qt::UserRole).toString() == f) { dup = true; break; }
        if (!dup) {
            auto *item = new QListWidgetItem(QFileInfo(f).fileName());
            item->setData(Qt::UserRole, f);
            item->setToolTip(f);
            m_fileList->addItem(item);
        }
    }
    updateUiState();
}

void MainWindow::removeFile()
{
    auto items = m_fileList->selectedItems();
    for (auto *it : items)
        delete it;
    updateUiState();
}

void MainWindow::clearFiles()
{
    m_fileList->clear();
    updateUiState();
}

// ───────────────────────── 传输控制 ─────────────────────────
void MainWindow::startTransfer()
{
    if (m_fileList->count() == 0) {
        QMessageBox::warning(this, "提示", "请先添加文件");
        return;
    }
    if (!m_portOpen) {
        QMessageBox::warning(this, "提示", "请先打开串口");
        return;
    }

    QStringList paths;
    for (int i = 0; i < m_fileList->count(); ++i)
        paths << m_fileList->item(i)->data(Qt::UserRole).toString();

    log("开始传输 " + QString::number(paths.size()) + " 个文件...");
    m_progress->setValue(0);
    m_fileLabel->setText("正在传输...");
    m_ymodem->sendFiles(paths);
    updateUiState();
}

void MainWindow::cancelTransfer()
{
    m_ymodem->cancel();
    log("用户取消传输");
    updateUiState();
}

// ───────────────────────── 信号处理 ─────────────────────────
void MainWindow::onStatusMessage(const QString &msg)
{
    log(msg);
}

void MainWindow::onProgressChanged(int percent)
{
    m_progress->setValue(percent);
}

void MainWindow::onFileProgress(const QString &file, int cur, int total)
{
    QString info = QString("[%1] %2 / %3 KB")
        .arg(file)
        .arg(cur / 1024)
        .arg(total / 1024);
    m_fileLabel->setText(info);
}

void MainWindow::onFinished(bool ok, const QString &msg)
{
    if (ok) {
        log("✅ " + msg);
        m_fileLabel->setText("完成: " + msg);
    } else {
        log("❌ " + msg);
        m_fileLabel->setText("失败: " + msg);
    }
    m_progress->setValue(ok ? 100 : 0);
    updateUiState();
}

void MainWindow::onError(const QString &err)
{
    log("⚠️ " + err);
}

// ───────────────────────── 辅助 ─────────────────────────
void MainWindow::updateUiState()
{
    bool idle = (m_serial->isOpen() && m_fileList->count() > 0);
    m_btnSend->setEnabled(idle);
    m_btnCancel->setEnabled(m_serial->isOpen());
    m_btnAdd->setEnabled(m_serial->isOpen());
}

void MainWindow::log(const QString &msg)
{
    QString ts = QDateTime::currentDateTime().toString("HH:mm:ss");
    m_logView->append(QString("[%1] %2").arg(ts, msg));
}
