#ifndef MAINWINDOW_H
#define MAINWINDOW_H

#include <QMainWindow>
#include <QSerialPort>
#include <QComboBox>
#include <QPushButton>
#include <QProgressBar>
#include <QLabel>
#include <QTextEdit>
#include <QLineEdit>
#include <QListWidget>

class YmodemSender;

class MainWindow : public QMainWindow
{
    Q_OBJECT
public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow() override;

private slots:
    void refreshPorts();
    void openSerialPort();
    void closeSerialPort();
    void addFiles();
    void removeFile();
    void clearFiles();
    void startTransfer();
    void cancelTransfer();

    void onStatusMessage(const QString &msg);
    void onProgressChanged(int percent);
    void onFileProgress(const QString &file, int cur, int total);
    void onFinished(bool ok, const QString &msg);
    void onError(const QString &err);

private:
    void setupUI();
    void updateUiState();

    QComboBox   *m_portCombo = nullptr;
    QComboBox   *m_baudCombo = nullptr;
    QPushButton *m_btnRefresh = nullptr;
    QPushButton *m_btnOpen    = nullptr;
    QPushButton *m_btnAdd     = nullptr;
    QPushButton *m_btnRemove  = nullptr;
    QPushButton *m_btnClear   = nullptr;
    QPushButton *m_btnSend    = nullptr;
    QPushButton *m_btnCancel  = nullptr;
    QListWidget *m_fileList   = nullptr;
    QProgressBar *m_progress  = nullptr;
    QLabel       *m_fileLabel = nullptr;
    QTextEdit    *m_logView   = nullptr;

    QSerialPort  *m_serial = nullptr;
    YmodemSender *m_ymodem = nullptr;
    bool m_portOpen = false;
};

#endif // MAINWINDOW_H
