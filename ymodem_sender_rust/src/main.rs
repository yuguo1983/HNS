mod ymodem;

use chrono::Local;
use eframe::egui;
use egui::{CentralPanel, CollapsingHeader, ScrollArea, TopBottomPanel};
use std::sync::mpsc::TryRecvError;
use ymodem::{SerialConfig, TransferEvent, YmodemSender};

#[derive(Default)]
struct AppState {
    port_name: String,
    baud_rate: u32,
    available_ports: Vec<String>,
    file_paths: Vec<String>,
    is_transferring: bool,
    progress: u32,
    file_progress: String,
    status_text: String,
    logs: Vec<String>,
    sender: Option<YmodemSender>,
    // 串口参数
    data_bits_idx: usize,
    stop_bits_idx: usize,
    parity_idx: usize,
    flow_ctrl_idx: usize,
    flash_delay_ms: u64,
}

const DATA_BITS_OPTIONS: &[&str] = &["5", "6", "7", "8"];
const STOP_BITS_OPTIONS: &[&str] = &["1", "2"];
const PARITY_OPTIONS: &[&str] = &["无", "奇校验", "偶校验"];
const FLOW_CTRL_OPTIONS: &[&str] = &["无", "软件(XON/XOFF)", "硬件(RTS/CTS)"];

impl AppState {
    fn new() -> Self {
        let mut s = Self::default();
        s.baud_rate = 460800;
        s.data_bits_idx = 3; // 8
        s.stop_bits_idx = 0; // 1
        s.parity_idx = 0;    // None
        s.flow_ctrl_idx = 0; // None
        s.flash_delay_ms = 0;
        s.refresh_ports();
        s
    }

    fn refresh_ports(&mut self) {
        self.available_ports = serialport::available_ports()
            .unwrap_or_default()
            .into_iter()
            .map(|p| p.port_name)
            .collect();
        if self.port_name.is_empty() && !self.available_ports.is_empty() {
            self.port_name = self.available_ports[0].clone();
        }
    }

    fn build_serial_config(&self) -> SerialConfig {
        let data_bits = match self.data_bits_idx {
            0 => serialport::DataBits::Five,
            1 => serialport::DataBits::Six,
            2 => serialport::DataBits::Seven,
            _ => serialport::DataBits::Eight,
        };
        let stop_bits = match self.stop_bits_idx {
            0 => serialport::StopBits::One,
            _ => serialport::StopBits::Two,
        };
        let parity = match self.parity_idx {
            0 => serialport::Parity::None,
            1 => serialport::Parity::Odd,
            _ => serialport::Parity::Even,
        };
        let flow_control = match self.flow_ctrl_idx {
            0 => serialport::FlowControl::None,
            1 => serialport::FlowControl::Software,
            _ => serialport::FlowControl::Hardware,
        };

        SerialConfig {
            port_name: self.port_name.clone(),
            baud_rate: self.baud_rate,
            data_bits,
            stop_bits,
            parity,
            flow_control,
            flash_delay_ms: self.flash_delay_ms,
        }
    }

    fn add_log(&mut self, msg: String) {
        let ts = Local::now().format("%H:%M:%S").to_string();
        self.logs.push(format!("[{}] {}", ts, msg));
        if self.logs.len() > 500 {
            self.logs.remove(0);
        }
    }

    fn poll_events(&mut self) {
        // 先收集所有事件（仅不可变借用 sender），避免借用冲突
        let mut events = Vec::new();
        let mut disconnected = false;

        if let Some(ref sender) = self.sender {
            loop {
                match sender.event_receiver().try_recv() {
                    Ok(event) => events.push(event),
                    Err(TryRecvError::Empty) => break,
                    Err(TryRecvError::Disconnected) => {
                        disconnected = true;
                        break;
                    }
                }
            }
        }

        // 再处理事件（此时可以安全地可变借用 self）
        for event in events {
            match event {
                TransferEvent::Status(msg) => self.add_log(msg),
                TransferEvent::Progress { file, cur, total } => {
                    self.file_progress =
                        format!("[{}] {} / {} KB", file, cur / 1024, total / 1024);
                }
                TransferEvent::OverallProgress(pct) => self.progress = pct,
                TransferEvent::Finished { ok, msg } => {
                    if ok {
                        self.add_log(format!("✅ {}", msg));
                        self.status_text = format!("完成: {}", msg);
                        self.progress = 100;
                    } else {
                        self.add_log(format!("❌ {}", msg));
                        self.status_text = format!("失败: {}", msg);
                        self.progress = 0;
                    }
                    self.is_transferring = false;
                    self.sender = None;
                }
            }
        }

        if disconnected {
            self.is_transferring = false;
            self.sender = None;
        }
    }

    fn start_transfer(&mut self) {
        let paths = self.file_paths.clone();
        if paths.is_empty() {
            return;
        }

        let config = self.build_serial_config();

        self.add_log(format!(
            "🔌 串口: {} | {}bps | {}位 | {}停止 | {} | {}",
            config.port_name,
            config.baud_rate,
            DATA_BITS_OPTIONS[self.data_bits_idx],
            STOP_BITS_OPTIONS[self.stop_bits_idx],
            PARITY_OPTIONS[self.parity_idx],
            FLOW_CTRL_OPTIONS[self.flow_ctrl_idx],
        ));

        let sender = YmodemSender::new();
        self.sender = Some(sender);

        self.is_transferring = true;
        self.progress = 0;
        self.file_progress = String::new();
        self.status_text = "传输中...".to_string();
        self.add_log("🚀 开始传输...".to_string());

        // send_files 内部会启动线程，只需要 &self 引用即可
        if let Some(ref sender) = self.sender {
            sender.send_files(config, paths);
        }
    }
}

fn main() {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([700.0, 560.0])
            .with_title("YMODEM 文件发送工具"),
        ..Default::default()
    };

    let _ = eframe::run_native(
        "YMODEM 文件发送工具",
        options,
        Box::new(|cc| {
            // 配置中文字体支持
            let mut fonts = egui::FontDefinitions::default();

            // 在 Windows 系统上加载微软雅黑字体
            let chinese_font_paths = [
                r"C:\Windows\Fonts\msyh.ttc",
                r"C:\Windows\Fonts\msyhbd.ttc",
                r"C:\Windows\Fonts\SIMHEI.TTF",
                r"C:\Windows\Fonts\msyh.ttf",
            ];

            for path in &chinese_font_paths {
                if let Ok(data) = std::fs::read(path) {
                    let font_name = format!(
                        "chinese_{}",
                        std::path::Path::new(path)
                            .file_stem()
                            .and_then(|s| s.to_str())
                            .unwrap_or("font")
                    );
                    fonts
                        .font_data
                        .insert(font_name.clone(), egui::FontData::from_owned(data));
                    fonts
                        .families
                        .get_mut(&egui::FontFamily::Proportional)
                        .unwrap()
                        .insert(0, font_name.clone());
                    fonts
                        .families
                        .get_mut(&egui::FontFamily::Monospace)
                        .unwrap()
                        .insert(0, font_name);
                    break;
                }
            }

            cc.egui_ctx.set_fonts(fonts);
            Box::new(AppState::new())
        }),
    );
}

impl eframe::App for AppState {
    fn update(&mut self, ctx: &egui::Context, _frame: &mut eframe::Frame) {
        self.poll_events();

        TopBottomPanel::top("toolbar").show(ctx, |ui| {
            // ── 第一行：基本设置 ──
            ui.horizontal(|ui| {
                ui.label("串口:");
                egui::ComboBox::new("port_combo", "选择串口")
                    .selected_text(if self.port_name.is_empty() {
                        "选择串口...".to_string()
                    } else {
                        self.port_name.clone()
                    })
                    .show_ui(ui, |ui| {
                        for p in &self.available_ports {
                            ui.selectable_value(&mut self.port_name, p.clone(), p);
                        }
                    });

                ui.label("波特率:");
                let baud_str = self.baud_rate.to_string();
                let mut selected_baud = baud_str.clone();
                egui::ComboBox::new("baud_combo", "baud rate")
                    .selected_text(&selected_baud)
                    .show_ui(ui, |ui| {
                        for b in &[9600u32, 19200, 38400, 57600, 115200, 230400, 460800, 921600] {
                            ui.selectable_value(
                                &mut selected_baud,
                                b.to_string(),
                                b.to_string(),
                            );
                        }
                    });
                self.baud_rate = selected_baud.parse().unwrap_or(115200);

                if ui.button("🔄 刷新").clicked() {
                    self.refresh_ports();
                }
            });

            // ── 第二行：高级串口参数（可折叠） ──
            CollapsingHeader::new("⚙ 高级串口设置")
                .default_open(false)
                .show(ui, |ui| {
                    ui.horizontal(|ui| {
                        ui.label("数据位:");
                        egui::ComboBox::new("data_bits_combo", "data bits")
                            .selected_text(DATA_BITS_OPTIONS[self.data_bits_idx])
                            .show_ui(ui, |ui| {
                                for (i, label) in DATA_BITS_OPTIONS.iter().enumerate() {
                                    ui.selectable_value(
                                        &mut self.data_bits_idx,
                                        i,
                                        *label,
                                    );
                                }
                            });

                        ui.label("停止位:");
                        egui::ComboBox::new("stop_bits_combo", "stop bits")
                            .selected_text(STOP_BITS_OPTIONS[self.stop_bits_idx])
                            .show_ui(ui, |ui| {
                                for (i, label) in STOP_BITS_OPTIONS.iter().enumerate() {
                                    ui.selectable_value(
                                        &mut self.stop_bits_idx,
                                        i,
                                        *label,
                                    );
                                }
                            });
                    });

                    ui.horizontal(|ui| {
                        ui.label("校验:");
                        egui::ComboBox::new("parity_combo", "parity")
                            .selected_text(PARITY_OPTIONS[self.parity_idx])
                            .show_ui(ui, |ui| {
                                for (i, label) in PARITY_OPTIONS.iter().enumerate() {
                                    ui.selectable_value(&mut self.parity_idx, i, *label);
                                }
                            });

                        ui.label("流控:");
                        egui::ComboBox::new("flow_ctrl_combo", "flow control")
                            .selected_text(FLOW_CTRL_OPTIONS[self.flow_ctrl_idx])
                            .show_ui(ui, |ui| {
                                for (i, label) in FLOW_CTRL_OPTIONS.iter().enumerate() {
                                    ui.selectable_value(
                                        &mut self.flow_ctrl_idx,
                                        i,
                                        *label,
                                    );
                                }
                            });

                        ui.label("FLASH延时(ms):");
                        ui.add(
                            egui::DragValue::new(&mut self.flash_delay_ms)
                                .clamp_range(0..=10000u64)
                                .speed(1),
                        );
                    });
                });
        });

        CentralPanel::default().show(ctx, |ui| {
            ui.label(egui::RichText::new("📁 文件列表").strong());

            if self.file_paths.is_empty() {
                ui.label("（未添加文件）");
            } else {
                let mut remove_idx = None;
                for (idx, path) in self.file_paths.iter().enumerate() {
                    ui.horizontal(|ui| {
                        if ui.button("✕").clicked() {
                            remove_idx = Some(idx);
                        }
                        let fname =
                            path.split(|c| c == '/' || c == '\\').last().unwrap_or(path);
                        ui.label(fname);
                    });
                }
                if let Some(idx) = remove_idx {
                    self.file_paths.remove(idx);
                }
            }

            ui.separator();

            ui.horizontal(|ui| {
                if ui.button("📂 添加文件").clicked() {
                    if let Some(files) = rfd::FileDialog::new()
                        .add_filter("所有文件", &["*"])
                        .pick_files()
                    {
                        for f in files {
                            let path = f.display().to_string();
                            if !self.file_paths.contains(&path) {
                                self.file_paths.push(path);
                            }
                        }
                    }
                }

                if ui.button("清空").clicked() {
                    self.file_paths.clear();
                }
            });

            ui.separator();

            if !self.is_transferring {
                let send_btn = egui::Button::new("▶ 开始发送")
                    .min_size([120.0, 32.0].into())
                    .fill(egui::Color32::from_rgb(76, 175, 80));
                if ui
                    .add_enabled(
                        !self.file_paths.is_empty() && !self.port_name.is_empty(),
                        send_btn,
                    )
                    .clicked()
                {
                    self.start_transfer();
                }
            } else {
                if ui.button("⏹ 取消").clicked() {
                    if let Some(ref sender) = self.sender {
                        sender.cancel();
                    }
                }
            }

            if !self.file_progress.is_empty() {
                ui.label(&self.file_progress);
            }
            if !self.status_text.is_empty() {
                ui.label(&self.status_text);
            }

            if self.is_transferring {
                ui.add(
                    egui::ProgressBar::new(self.progress as f32 / 100.0)
                        .text(format!("{}%", self.progress)),
                );
            }

            ui.separator();
            ui.label(egui::RichText::new("📋 日志").strong());

            ScrollArea::vertical()
                .max_height(150.0)
                .stick_to_bottom(true)
                .show(ui, |ui| {
                    for log in &self.logs {
                        ui.label(log);
                    }
                });
        });

        ctx.request_repaint_after(std::time::Duration::from_millis(100));
    }
}
