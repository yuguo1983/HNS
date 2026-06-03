use anyhow::{anyhow, Context, Result};
use std::io::{Read, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

// ── 协议常量 ──
const SOH: u8 = 0x01;
const STX: u8 = 0x02;
const EOT: u8 = 0x04;
const ACK: u8 = 0x06;
const NAK: u8 = 0x15;
const CAN: u8 = 0x18;
const CRC_CHAR: u8 = 0x43; // 'C'

const MAX_RETRIES: u32 = 15;
const TIMEOUT_MS: u64 = 3000;
const BLOCK_128: usize = 128;
const BLOCK_1024: usize = 1024;
const DEBUG: bool = false;

// ── 串口配置 ──
#[derive(Debug, Clone)]
pub struct SerialConfig {
    pub port_name: String,
    pub baud_rate: u32,
    pub data_bits: serialport::DataBits,
    pub stop_bits: serialport::StopBits,
    pub parity: serialport::Parity,
    pub flow_control: serialport::FlowControl,
    /// 每包数据发送后等待的时间(ms)，在等 ACK 前给接收方擦除/写入 FLASH
    pub flash_delay_ms: u64,
}

impl Default for SerialConfig {
    fn default() -> Self {
        Self {
            port_name: String::new(),
            baud_rate: 460800,
            data_bits: serialport::DataBits::Eight,
            stop_bits: serialport::StopBits::One,
            parity: serialport::Parity::None,
            flow_control: serialport::FlowControl::None,
            flash_delay_ms: 0,
        }
    }
}

// ── CRC-CCITT 查表 ──
static CRC_TABLE: [u16; 256] = [
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7, 0x8108, 0x9129, 0xA14A,
    0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF, 0x1231, 0x0210, 0x3273, 0x2252, 0x52B5, 0x4294,
    0x72F7, 0x62D6, 0x9339, 0x8318, 0xB37B, 0xA35A, 0xD3BD, 0xC39C, 0xF3FF, 0xE3DE, 0x2462,
    0x3443, 0x0420, 0x1401, 0x64E6, 0x74C7, 0x44A4, 0x5485, 0xA56A, 0xB54B, 0x8528, 0x9509,
    0xE5EE, 0xF5CF, 0xC5AC, 0xD58D, 0x3653, 0x2672, 0x1611, 0x0630, 0x76D7, 0x66F6, 0x5695,
    0x46B4, 0xB75B, 0xA77A, 0x9719, 0x8738, 0xF7DF, 0xE7FE, 0xD79D, 0xC7BC, 0x48C4, 0x58E5,
    0x6886, 0x78A7, 0x0840, 0x1861, 0x2802, 0x3823, 0xC9CC, 0xD9ED, 0xE98E, 0xF9AF, 0x8948,
    0x9969, 0xA90A, 0xB92B, 0x5AF5, 0x4AD4, 0x7AB7, 0x6A96, 0x1A71, 0x0A50, 0x3A33, 0x2A12,
    0xDBFD, 0xCBDC, 0xFBBF, 0xEB9E, 0x9B79, 0x8B58, 0xBB3B, 0xAB1A, 0x6CA6, 0x7C87, 0x4CE4,
    0x5CC5, 0x2C22, 0x3C03, 0x0C60, 0x1C41, 0xEDAE, 0xFD8F, 0xCDEC, 0xDDCD, 0xAD2A, 0xBD0B,
    0x8D68, 0x9D49, 0x7E97, 0x6EB6, 0x5ED5, 0x4EF4, 0x3E13, 0x2E32, 0x1E51, 0x0E70, 0xFF9F,
    0xEFBE, 0xDFDD, 0xCFFC, 0xBF1B, 0xAF3A, 0x9F59, 0x8F78, 0x9188, 0x81A9, 0xB1CA, 0xA1EB,
    0xD10C, 0xC12D, 0xF14E, 0xE16F, 0x1080, 0x00A1, 0x30C2, 0x20E3, 0x5004, 0x4025, 0x7046,
    0x6067, 0x83B9, 0x9398, 0xA3FB, 0xB3DA, 0xC33D, 0xD31C, 0xE37F, 0xF35E, 0x02B1, 0x1290,
    0x22F3, 0x32D2, 0x4235, 0x5214, 0x6277, 0x7256, 0xB5EA, 0xA5CB, 0x95A8, 0x8589, 0xF56E,
    0xE54F, 0xD52C, 0xC50D, 0x34E2, 0x24C3, 0x14A0, 0x0481, 0x7466, 0x6447, 0x5424, 0x4405,
    0xA7DB, 0xB7FA, 0x8799, 0x97B8, 0xE75F, 0xF77E, 0xC71D, 0xD73C, 0x26D3, 0x36F2, 0x0691,
    0x16B0, 0x6657, 0x7676, 0x4615, 0x5634, 0xD94C, 0xC96D, 0xF90E, 0xE92F, 0x99C8, 0x89E9,
    0xB98A, 0xA9AB, 0x5844, 0x4865, 0x7806, 0x6827, 0x18C0, 0x08E1, 0x3882, 0x28A3, 0xCB7D,
    0xDB5C, 0xEB3F, 0xFB1E, 0x8BF9, 0x9BD8, 0xABBB, 0xBB9A, 0x4A75, 0x5A54, 0x6A37, 0x7A16,
    0x0AF1, 0x1AD0, 0x2AB3, 0x3A92, 0xFD2E, 0xED0F, 0xDD6C, 0xCD4D, 0xBDAA, 0xAD8B, 0x9DE8,
    0x8DC9, 0x7C26, 0x6C07, 0x5C64, 0x4C45, 0x3CA2, 0x2C83, 0x1CE0, 0x0CC1, 0xEF1F, 0xFF3E,
    0xCF5D, 0xDF7C, 0xAF9B, 0xBFBA, 0x8FD9, 0x9FF8, 0x6E17, 0x7E36, 0x4E55, 0x5E74, 0x2E93,
    0x3EB2, 0x0ED1, 0x1EF0,
];

fn crc16(data: &[u8]) -> u16 {
    let mut crc: u16 = 0;
    for &byte in data {
        crc = (crc << 8) ^ CRC_TABLE[((crc >> 8) ^ byte as u16) as usize & 0xFF];
    }
    crc
}

/// 传输事件（从工作线程发往 UI 线程）
#[derive(Clone, Debug)]
pub enum TransferEvent {
    Status(String),
    Progress { file: String, cur: u64, total: u64 },
    OverallProgress(u32), // 0-100
    Finished { ok: bool, msg: String },
}

/// YMODEM 发送器
pub struct YmodemSender {
    cancel_flag: Arc<AtomicBool>,
    event_tx: Sender<TransferEvent>,
    event_rx: Receiver<TransferEvent>,
}

impl YmodemSender {
    pub fn new() -> Self {
        let (tx, rx) = mpsc::channel();
        YmodemSender {
            cancel_flag: Arc::new(AtomicBool::new(false)),
            event_tx: tx,
            event_rx: rx,
        }
    }

    pub fn event_receiver(&self) -> &Receiver<TransferEvent> {
        &self.event_rx
    }

    /// 取消传输
    pub fn cancel(&self) {
        self.cancel_flag.store(true, Ordering::SeqCst);
    }

    /// 开始发送多个文件（异步，在后台线程中执行）
    pub fn send_files(&self, config: SerialConfig, file_paths: Vec<String>) {
        self.cancel_flag.store(false, Ordering::SeqCst);
        let cancel_flag = self.cancel_flag.clone();
        let event_tx = self.event_tx.clone();

        thread::spawn(move || {
            let result = Self::run_transfer(&config, &file_paths, &event_tx, &cancel_flag);
            match result {
                Ok(()) => {
                    let _ = event_tx.send(TransferEvent::Finished {
                        ok: true,
                        msg: "全部文件传输成功完成".into(),
                    });
                }
                Err(e) => {
                    if cancel_flag.load(Ordering::SeqCst) {
                        let _ = event_tx.send(TransferEvent::Finished {
                            ok: false,
                            msg: "用户取消传输".into(),
                        });
                    } else {
                        let _ = event_tx.send(TransferEvent::Finished {
                            ok: false,
                            msg: format!("传输失败: {}", e),
                        });
                    }
                }
            }
        });
    }

    fn run_transfer(
        config: &SerialConfig,
        file_paths: &[String],
        evt: &Sender<TransferEvent>,
        cancel: &AtomicBool,
    ) -> Result<()> {
        // 打开串口
        let mut port = serialport::new(&config.port_name, config.baud_rate)
            .timeout(Duration::from_millis(TIMEOUT_MS))
            .data_bits(config.data_bits)
            .flow_control(config.flow_control)
            .parity(config.parity)
            .stop_bits(config.stop_bits)
            .open()
            .context("打开串口失败")?;

        // 清空缓冲区
        let _ = port.flush();

        for (idx, path) in file_paths.iter().enumerate() {
            if cancel.load(Ordering::SeqCst) {
                Self::send_cans(&mut port)?;
                return Err(anyhow!("取消"));
            }

            let file_path = Path::new(path);
            let file_name = file_path
                .file_name()
                .and_then(|n| n.to_str())
                .unwrap_or("unknown");
            let file_size = std::fs::metadata(path)
                .context("读取文件信息失败")?
                .len();

            let mut file = std::fs::File::open(path).context("打开文件失败")?;

            evt.send(TransferEvent::Status(format!(
                "[{}/{}] 发送: {} ({} 字节)",
                idx + 1,
                file_paths.len(),
                file_name,
                file_size
            )))
            .ok();

            // ── 发送单个文件 ──
            Self::send_single_file(
                &mut port,
                &mut file,
                file_name,
                file_size,
                evt,
                cancel,
                config.flash_delay_ms,
            )?;

            evt.send(TransferEvent::OverallProgress(
                ((idx + 1) * 100 / file_paths.len()) as u32,
            ))
            .ok();
        }

        // ── 所有文件发送完毕 → 发结束包 ──
        evt.send(TransferEvent::Status("发送结束包...".into())).ok();
        Self::send_close_packet(&mut port, evt, cancel)?;

        Ok(())
    }

    /// 发送单个文件
    fn send_single_file(
        port: &mut Box<dyn serialport::SerialPort>,
        file: &mut std::fs::File,
        file_name: &str,
        file_size: u64,
        evt: &Sender<TransferEvent>,
        cancel: &AtomicBool,
        flash_delay_ms: u64,
    ) -> Result<()> {
        // 1. 等待 'C'
        evt.send(TransferEvent::Status("等待接收方...".into())).ok();
        Self::wait_byte(port, CRC_CHAR, cancel, Some(evt)).context("等待接收方超时")?;

        // 2. 发送头包（内部已等待并消费 ACK）
        Self::send_header(port, file_name, file_size, evt, cancel, flash_delay_ms)?;

        // 3. 等待接收方发 'C' 表示准备接收数据
        Self::wait_byte(port, CRC_CHAR, cancel, Some(evt)).context("头包后未收到 'C'")?;

        // 4. 发送数据包
        let mut seq: u8 = 1;
        let mut bytes_sent: u64 = 0;

        loop {
            if cancel.load(Ordering::SeqCst) {
                Self::send_cans(port)?;
                return Err(anyhow!("取消"));
            }

            let _remaining = file_size - bytes_sent;
            let use_1024 = true;
            let block_size = if use_1024 { BLOCK_1024 } else { BLOCK_128 };

            let mut buf = vec![0u8; block_size];
            let n = file.read(&mut buf).context("读取文件失败")?;
            if n == 0 {
                break; // 文件读完
            }

            // 填充剩余字节
            for b in buf[n..].iter_mut() {
                *b = 0x1A;
            }

            let header = if use_1024 { STX } else { SOH };
            if DEBUG {
                evt.send(TransferEvent::Status(format!(
                    "[DEBUG] 发数据包 #{} ({}字节, 0x{:02X})",
                    seq, block_size, header
                )))
                .ok();
            }
            Self::send_packet(port, header, seq, &buf, Some(evt))?;
            bytes_sent += n as u64;
            seq = seq.wrapping_add(1);

            // 进度
            let pct = ((bytes_sent * 100) / file_size) as u32;
            evt.send(TransferEvent::Progress {
                file: file_name.to_string(),
                cur: bytes_sent,
                total: file_size,
            })
            .ok();
            evt.send(TransferEvent::OverallProgress(pct)).ok();

            // ⏱ 给接收方时间擦除/写入 FLASH（在等 ACK 之前）
            if flash_delay_ms > 0 {
                thread::sleep(Duration::from_millis(flash_delay_ms));
            }

            if DEBUG {
                evt.send(TransferEvent::Status(format!(
                    "[DEBUG] 等 ACK (包 #{})...",
                    seq.wrapping_sub(1)
                )))
                .ok();
            }
            if let Err(e) = Self::wait_byte_with_retry(port, ACK, |port| {
                if DEBUG {
                    evt.send(TransferEvent::Status(
                        "[DEBUG] ⏳ 超时/NAK，重发当前包...".into(),
                    ))
                    .ok();
                }
                Self::send_packet(port, header, seq.wrapping_sub(1), &buf, Some(evt)).ok();
            }, Some(evt)) {
                let err_msg = format!("数据包未收到 ACK: {:#}", e);
                if DEBUG {
                    evt.send(TransferEvent::Status(format!("[DEBUG] ❌ {}", err_msg))).ok();
                }
                return Err(anyhow!("{}", err_msg));
            }
        }

        // 5. 发送 EOT
        evt.send(TransferEvent::Status("发送 EOT...".into())).ok();
        Self::send_eot(port, evt, cancel)?;

        Ok(())
    }

    // ──────────── 协议原语 ────────────

    fn send_byte(port: &mut Box<dyn serialport::SerialPort>, b: u8) -> Result<()> {
        port.write_all(&[b]).context("写入串口失败")
    }

    fn send_cans(port: &mut Box<dyn serialport::SerialPort>) -> Result<()> {
        for _ in 0..5 {
            port.write_all(&[CAN]).ok();
        }
        port.flush().ok();
        Ok(())
    }

    /// 等待某个特定字节（忽略其他字节）
    fn wait_byte(
        port: &mut Box<dyn serialport::SerialPort>,
        expected: u8,
        cancel: &AtomicBool,
        evt: Option<&Sender<TransferEvent>>,
    ) -> Result<u8> {
        let mut buf = [0u8; 1];
        loop {
            if cancel.load(Ordering::SeqCst) {
                return Err(anyhow!("取消"));
            }
            match port.read(&mut buf) {
                Ok(0) => continue,
                Ok(_) => {
                    if let Some(evt) = evt {
                        let _ = evt.send(TransferEvent::Status(format!(
                            "[RAW<<] 0x{:02X} (等 0x{:02X})",
                            buf[0], expected
                        )));
                    }
                    if buf[0] == expected {
                        return Ok(buf[0]);
                    }
                    // CAN from receiver?
                    if buf[0] == CAN {
                        return Err(anyhow!("接收方取消传输"));
                    }
                    // 忽略其他字节
                }
                Err(e) => {
                    if e.kind() == std::io::ErrorKind::TimedOut {
                        if let Some(evt) = evt {
                            let _ = evt.send(TransferEvent::Status(format!(
                                "[RAW<<] 超时 (等 0x{:02X})",
                                expected
                            )));
                        }
                        return Err(anyhow!("超时"));
                    }
                    return Err(e).context("串口读取失败");
                }
            }
        }
    }

    /// 等待字节，失败时自动重试（调用 retry_fn 重发）
    fn wait_byte_with_retry<F>(
        port: &mut Box<dyn serialport::SerialPort>,
        expected: u8,
        retry_fn: F,
        evt: Option<&Sender<TransferEvent>>,
    ) -> Result<u8>
    where
        F: Fn(&mut Box<dyn serialport::SerialPort>),
    {
        let mut retries = 0;
        let mut buf = [0u8; 1];
        loop {
            match port.read(&mut buf) {
                Ok(0) => continue,
                Ok(_) => {
                    if let Some(evt) = evt {
                        let _ = evt.send(TransferEvent::Status(format!(
                            "[RAW<<] 0x{:02X} (等 0x{:02X}, #{})",
                            buf[0], expected, retries
                        )));
                    }
                    if buf[0] == expected {
                        return Ok(buf[0]);
                    }
                    if buf[0] == NAK {
                        retries += 1;
                        if let Some(evt) = evt {
                            let _ = evt.send(TransferEvent::Status(format!(
                                "[RAW<<] NAK #{}/{}",
                                retries, MAX_RETRIES
                            )));
                        }
                        if retries > MAX_RETRIES {
                            return Err(anyhow!("重试超限 (NAK)"));
                        }
                        retry_fn(port);
                        continue;
                    }
                    if buf[0] == CAN {
                        return Err(anyhow!("接收方取消"));
                    }
                    // 收到其他字节，忽略
                }
                Err(e) => {
                    if e.kind() == std::io::ErrorKind::TimedOut {
                        retries += 1;
                        if let Some(evt) = evt {
                            let _ = evt.send(TransferEvent::Status(format!(
                                "[RAW<<] 超时 #{}/{}",
                                retries, MAX_RETRIES
                            )));
                        }
                        if retries > MAX_RETRIES {
                            return Err(anyhow!("重试超限 (超时)"));
                        }
                        retry_fn(port);
                        continue;
                    }
                    return Err(e).context("串口读取失败");
                }
            }
        }
    }

    /// 发送数据包
    fn send_packet(
        port: &mut Box<dyn serialport::SerialPort>,
        header: u8,
        seq: u8,
        data: &[u8],
        evt: Option<&Sender<TransferEvent>>,
    ) -> Result<()> {
        let mut pkt = Vec::with_capacity(3 + data.len() + 2);
        pkt.push(header);
        pkt.push(seq);
        pkt.push(!seq);
        pkt.extend_from_slice(data);
        let crc = crc16(data);
        pkt.push((crc >> 8) as u8);
        pkt.push((crc & 0xFF) as u8);
        if let Some(evt) = evt {
            let _ = evt.send(TransferEvent::Status(format!(
                "[RAW>>] 0x{:02X} seq={} len={} crc=0x{:04X}",
                header,
                seq,
                data.len(),
                crc
            )));
        }
        port.write_all(&pkt).context("发送数据包失败")?;
        port.flush().ok();
        Ok(())
    }

    /// 发送文件头包
    fn send_header(
        port: &mut Box<dyn serialport::SerialPort>,
        file_name: &str,
        file_size: u64,
        evt: &Sender<TransferEvent>,
        cancel: &AtomicBool,
        _flash_delay_ms: u64, // 现在移到最后
    ) -> Result<()> {
        let meta = format!("{} {}", file_name, file_size);
        let mut data = meta.into_bytes();
        data.push(0u8);
        while data.len() < BLOCK_128 {
            data.push(0u8);
        }

        let mut retries = 0;
        loop {
            if cancel.load(Ordering::SeqCst) {
                Self::send_cans(port)?;
                return Err(anyhow!("取消"));
            }

            Self::send_packet(port, SOH, 0, &data, Some(evt))?;
            evt.send(TransferEvent::Status(format!(
                "发送文件头: {} ({} 字节)",
                file_name, file_size
            )))
            .ok();

            if DEBUG {
                evt.send(TransferEvent::Status(format!(
                    "[DEBUG] 等头包 ACK (重试#{})...",
                    retries
                )))
                .ok();
            }

            // 等待 ACK
            let mut buf = [0u8; 1];
            match port.read(&mut buf) {
                Ok(_) => {
                    if DEBUG {
                        evt.send(TransferEvent::Status(format!(
                            "[DEBUG] 收到 0x{:02X}",
                            buf[0]
                        )))
                        .ok();
                    }
                    if buf[0] == ACK {
                        // YMODEM: 接收方收到头包后回 ACK，然后立即发 'C' 表示准备接收数据
                        // 如果此时缓冲区里已经有 'C'（接收方早于我们到达），直接消费它
                        if let Ok(c) = port.read(&mut buf) {
                            if c > 0 && buf[0] == CRC_CHAR {
                                if DEBUG {
                                    evt.send(TransferEvent::Status("[DEBUG] 头包后直接收到 'C'".into())).ok();
                                }
                                return Ok(());
                            }
                        }
                        // 否则正常等待 'C'
                        evt.send(TransferEvent::Status("等待接收方准备就绪...".into())).ok();
                        return Self::wait_byte(port, CRC_CHAR, cancel, Some(evt)).map(|_| ());
                    }
                    if buf[0] == NAK || buf[0] == CAN {
                        retries += 1;
                        if retries > MAX_RETRIES {
                            return Err(anyhow!("头包重试超限"));
                        }
                        continue;
                    }
                    // 其他字节，忽略并继续等待
                }
                Err(e) => {
                    if e.kind() == std::io::ErrorKind::TimedOut {
                        if DEBUG {
                            evt.send(TransferEvent::Status(
                                "[DEBUG] ⏳ 头包超时，重试...".into(),
                            ))
                            .ok();
                        }
                        retries += 1;
                        if retries > MAX_RETRIES {
                            return Err(anyhow!("头包超时"));
                        }
                        continue;
                    }
                    return Err(e).context("读取串口失败");
                }
            }
        }
    }

    /// 发送 EOT
    fn send_eot(
        port: &mut Box<dyn serialport::SerialPort>,
        evt: &Sender<TransferEvent>,
        cancel: &AtomicBool,
    ) -> Result<()> {
        let mut retries = 0;
        loop {
            if cancel.load(Ordering::SeqCst) {
                Self::send_cans(port)?;
                return Err(anyhow!("取消"));
            }

            Self::send_byte(port, EOT)?;

            let mut buf = [0u8; 1];
            match port.read(&mut buf) {
                Ok(_) => {
                    if buf[0] == ACK {
                        evt.send(TransferEvent::Status("文件传输完成 ✓".into())).ok();
                        return Ok(());
                    }
                    if buf[0] == NAK {
                        retries += 1;
                        if retries > MAX_RETRIES {
                            return Err(anyhow!("EOT 重试超限"));
                        }
                        continue;
                    }
                }
                Err(e) => {
                    if e.kind() == std::io::ErrorKind::TimedOut {
                        retries += 1;
                        if retries > MAX_RETRIES {
                            return Err(anyhow!("EOT 超时"));
                        }
                        continue;
                    }
                    return Err(e).context("读取串口失败");
                }
            }
        }
    }

    /// 发送结束包（空头包）
    fn send_close_packet(
        port: &mut Box<dyn serialport::SerialPort>,
        evt: &Sender<TransferEvent>,
        cancel: &AtomicBool,
    ) -> Result<()> {
        let zero = vec![0u8; BLOCK_128];
        let mut retries = 0;

        loop {
            if cancel.load(Ordering::SeqCst) {
                return Err(anyhow!("取消"));
            }

            Self::send_packet(port, SOH, 0, &zero, Some(evt))?;

            let mut buf = [0u8; 1];
            match port.read(&mut buf) {
                Ok(_) => {
                    if buf[0] == ACK {
                        evt.send(TransferEvent::Status("全部传输完成 🎉".into())).ok();
                        return Ok(());
                    }
                    if buf[0] == NAK {
                        retries += 1;
                        if retries > MAX_RETRIES {
                            return Err(anyhow!("结束包重试超限"));
                        }
                        continue;
                    }
                }
                Err(e) => {
                    if e.kind() == std::io::ErrorKind::TimedOut {
                        retries += 1;
                        if retries > MAX_RETRIES {
                            return Err(anyhow!("结束包超时"));
                        }
                        continue;
                    }
                    return Err(e).context("读取串口失败");
                }
            }
        }
    }
}

impl Default for YmodemSender {
    fn default() -> Self {
        Self::new()
    }
}
