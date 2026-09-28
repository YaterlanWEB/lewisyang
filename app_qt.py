from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core import (
    LyricLine,
    Project,
    default_project,
    format_timestamp,
    load_project,
    load_srt,
    parse_timestamp,
    save_project,
    save_srt,
)
from render_engine import render_preview, render_video
from srt_generator import (
    DEVICE_CPU,
    DEVICE_CUDA,
    MODEL_ENHANCED,
    MODEL_LIGHTWEIGHT,
    QUALITY_ACCURATE,
    QUALITY_FAST,
    generate_srt_from_txt,
    gpu_runtime_status,
)


APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


class TaskSignals(QObject):
    log = Signal(str)
    done = Signal(object)
    failed = Signal(str)


class PathRow(QWidget):
    changed = Signal(str)

    def __init__(self, title: str, placeholder: str, mode: str, file_filter: str):
        super().__init__()
        self.mode = mode
        self.file_filter = file_filter
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.setMinimumHeight(40)

        label = QLabel(title)
        label.setFixedWidth(72)
        label.setObjectName("fieldLabel")
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setMinimumHeight(38)
        self.edit.textChanged.connect(self.changed.emit)
        button = QPushButton("选择")
        button.setObjectName("secondaryButton")
        button.setFixedWidth(64)
        button.setFixedHeight(38)
        button.clicked.connect(self.pick)

        layout.addWidget(label)
        layout.addWidget(self.edit, 1)
        layout.addWidget(button)

    def text(self) -> str:
        return self.edit.text().strip()

    def set_text(self, value: str) -> None:
        self.edit.setText(value)

    def pick(self) -> None:
        if self.mode == "save":
            path, _ = QFileDialog.getSaveFileName(self, "选择输出路径", self.text(), self.file_filter)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", self.text(), self.file_filter)
        if path:
            self.set_text(path)


class LyricsStudio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("歌词视频生成器 Studio v3.1")
        self.resize(1780, 940)
        self.setMinimumSize(1500, 800)

        self.project: Project = default_project(APP_DIR).normalized()
        self.project_path = APP_DIR / "project.lyricproj"
        self.preview_path = APP_DIR / "preview_qt.jpg"
        self.table_updating = False
        self.task_signals: list[TaskSignals] = []

        self._build_ui()
        self._apply_theme()
        self._load_project_to_ui()
        self._refresh_table()
        self.log("Studio 试验版已启动。")

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(16)
        self.setCentralWidget(root)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(14)

        setup = self._build_setup_panel()
        lyrics = self._build_lyrics_panel()
        right = self._build_preview_panel()
        splitter.addWidget(setup)
        splitter.addWidget(lyrics)
        splitter.addWidget(right)
        splitter.setSizes([500, 690, 590])

        root_layout.addWidget(splitter, 1)

    def _build_sidebar(self) -> QWidget:
        box = QFrame()
        box.setObjectName("sidebar")
        box.setFixedWidth(210)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("Lyrics Studio")
        title.setObjectName("appTitle")
        subtitle = QLabel("滚动歌词视频工作台")
        subtitle.setObjectName("muted")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        steps = [
            ("01", "素材"),
            ("02", "生成 SRT"),
            ("03", "校对"),
            ("04", "样式"),
            ("05", "导出"),
        ]
        for number, text in steps:
            row = QLabel(f"{number}  {text}")
            row.setObjectName("navItem")
            layout.addWidget(row)
        layout.addStretch(1)

        self.open_project_btn = QPushButton("打开项目")
        self.save_project_btn = QPushButton("保存项目")
        self.open_project_btn.clicked.connect(self.open_project)
        self.save_project_btn.clicked.connect(self.save_project_file)
        layout.addWidget(self.open_project_btn)
        layout.addWidget(self.save_project_btn)
        return box

    def _build_setup_panel(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(460)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(22, 18, 22, 18)
        hero_layout.setSpacing(6)
        h1 = QLabel("歌词视频工作台")
        h1.setObjectName("heroTitle")
        h2 = QLabel("先准备素材，再生成 SRT，最后校对导出。")
        h2.setObjectName("muted")
        hero_layout.addWidget(h1)
        hero_layout.addWidget(h2)
        project_row = QHBoxLayout()
        self.open_project_btn = QPushButton("打开项目")
        self.save_project_btn = QPushButton("保存项目")
        self.open_project_btn.clicked.connect(self.open_project)
        self.save_project_btn.clicked.connect(self.save_project_file)
        project_row.addWidget(self.open_project_btn)
        project_row.addWidget(self.save_project_btn)
        hero_layout.addLayout(project_row)
        layout.addWidget(hero)

        material = QFrame()
        material.setObjectName("card")
        material.setMinimumHeight(330)
        material_layout = QVBoxLayout(material)
        material_layout.setContentsMargins(18, 18, 18, 18)
        material_layout.setSpacing(8)
        material_layout.addWidget(self._section_title("文本信息与上传素材"))

        self.audio_row = PathRow("音频", "选择 WAV / MP3 / FLAC / M4A", "open", "音频 (*.wav *.mp3 *.flac *.m4a);;所有文件 (*.*)")
        self.bg_row = PathRow("背景", "选择背景图片", "open", "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)")
        self.txt_row = PathRow("TXT", "选择纯文本歌词", "open", "文本 (*.txt);;所有文件 (*.*)")
        self.srt_row = PathRow("SRT", "选择或设置 SRT 路径", "save", "SRT 字幕 (*.srt)")
        self.out_row = PathRow("MP4", "选择导出视频路径", "save", "MP4 视频 (*.mp4)")
        self.ffmpeg_row = PathRow("FFmpeg", "ffmpeg.exe 路径", "open", "ffmpeg.exe (ffmpeg.exe);;所有文件 (*.*)")
        for row in (self.audio_row, self.bg_row, self.txt_row, self.srt_row, self.out_row, self.ffmpeg_row):
            material_layout.addWidget(row)
        layout.addWidget(material, 1)

        action = QFrame()
        action.setObjectName("card")
        action.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        action_layout = QVBoxLayout(action)
        action_layout.setContentsMargins(18, 14, 18, 14)
        action_layout.setSpacing(8)
        action_layout.addWidget(self._section_title("生成与导出"))

        self.model_combo = QComboBox()
        self.model_combo.addItem("高精度（推荐）", QUALITY_ACCURATE)
        self.model_combo.addItem("快速兼容", QUALITY_FAST)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("Turbo 增强模型", MODEL_ENHANCED)
        self.engine_combo.addItem("Small 轻量模型", MODEL_LIGHTWEIGHT)
        self.device_combo = QComboBox()
        self.device_combo.addItem("CPU", DEVICE_CPU)
        self.device_combo.addItem("NVIDIA GPU", DEVICE_CUDA)
        self.generate_btn = QPushButton("从 TXT 生成 SRT 初稿")
        self.load_srt_btn = QPushButton("载入 SRT")
        self.save_srt_btn = QPushButton("保存 SRT")
        self.preview_btn = QPushButton("预览当前画面")
        self.render_btn = QPushButton("导出视频")
        self.generate_btn.setObjectName("primaryButton")
        self.render_btn.setObjectName("primaryButton")
        self.generate_btn.clicked.connect(self.generate_srt)
        self.load_srt_btn.clicked.connect(self.load_srt_file)
        self.save_srt_btn.clicked.connect(self.save_srt_file)
        self.preview_btn.clicked.connect(self.preview_current_frame)
        self.render_btn.clicked.connect(self.render_video_task)

        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        model_row.addWidget(QLabel("对齐模式"))
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(QLabel("识别模型"))
        model_row.addWidget(self.engine_combo, 1)
        model_row.addWidget(QLabel("运行设备"))
        model_row.addWidget(self.device_combo, 1)
        model_row.addWidget(self.load_srt_btn)
        model_row.addWidget(self.save_srt_btn)
        action_layout.addLayout(model_row)

        main_actions = QHBoxLayout()
        main_actions.setSpacing(8)
        main_actions.addWidget(self.generate_btn, 2)
        main_actions.addWidget(self.preview_btn, 1)
        main_actions.addWidget(self.render_btn, 1)
        action_layout.addLayout(main_actions)
        layout.addWidget(action)

        style_card = QFrame()
        style_card.setObjectName("card")
        style_layout = QGridLayout(style_card)
        style_layout.setContentsMargins(18, 18, 18, 18)
        style_layout.setHorizontalSpacing(10)
        style_layout.setVerticalSpacing(10)
        style_layout.addWidget(self._section_title("样式微调"), 0, 0, 1, 4)

        self.title_edit = QLineEdit()
        self.credit_edit = QLineEdit()
        self.title_size = self._spin(24, 96)
        self.active_size = self._spin(24, 72)
        self.near_size = self._spin(20, 60)
        self.center_y = self._spin(200, 1200)
        rows = [
            ("歌名", self.title_edit),
            ("词曲", self.credit_edit),
            ("歌名字号", self.title_size),
            ("当前字号", self.active_size),
            ("普通字号", self.near_size),
            ("歌词中心 Y", self.center_y),
        ]
        for idx, (label, widget) in enumerate(rows, start=1):
            style_layout.addWidget(QLabel(label), idx, 0)
            style_layout.addWidget(widget, idx, 1, 1, 3)
        layout.addWidget(style_card)
        return page

    def _build_lyrics_panel(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(560)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        lyrics = QFrame()
        lyrics.setObjectName("card")
        lyrics_layout = QVBoxLayout(lyrics)
        lyrics_layout.setContentsMargins(18, 18, 18, 18)
        lyrics_layout.setSpacing(12)
        lyrics_layout.addWidget(self._section_title("歌词校对"))

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("lyricsTable")
        self.table.setHorizontalHeaderLabels(["#", "开始", "结束", "歌词"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.cellChanged.connect(self._table_changed)
        lyrics_layout.addWidget(self.table, 1)

        range_row = QHBoxLayout()
        self.range_group = QButtonGroup(self)
        self.current_radio = QRadioButton("当前行")
        self.selected_radio = QRadioButton("选中行")
        self.to_end_radio = QRadioButton("当前到结尾")
        self.selected_radio.setChecked(True)
        for button in (self.current_radio, self.selected_radio, self.to_end_radio):
            self.range_group.addButton(button)
            range_row.addWidget(button)
        range_row.addStretch(1)
        lyrics_layout.addLayout(range_row)

        shift_row = QHBoxLayout()
        for seconds in (0.1, 0.5, 1.0):
            early = QPushButton(f"提前 {seconds:g}s")
            late = QPushButton(f"延后 {seconds:g}s")
            early.clicked.connect(lambda _=False, value=seconds: self.shift_time(-value))
            late.clicked.connect(lambda _=False, value=seconds: self.shift_time(value))
            shift_row.addWidget(early)
            shift_row.addWidget(late)
        lyrics_layout.addLayout(shift_row)

        edit_row = QHBoxLayout()
        self.merge_btn = QPushButton("合并到上一行")
        self.delete_btn = QPushButton("删除行")
        self.merge_btn.clicked.connect(self.merge_with_previous)
        self.delete_btn.clicked.connect(self.delete_current_line)
        edit_row.addStretch(1)
        edit_row.addWidget(self.merge_btn)
        edit_row.addWidget(self.delete_btn)
        lyrics_layout.addLayout(edit_row)

        layout.addWidget(lyrics, 1)
        return page

    def _build_center_panel(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QVBoxLayout(hero)
        hero_layout.setContentsMargins(22, 18, 22, 18)
        hero_layout.setSpacing(6)
        h1 = QLabel("本地生成 SRT，再做成滚动歌词视频")
        h1.setObjectName("heroTitle")
        h2 = QLabel("音频、TXT、背景图进入同一个流程；歌词可以逐行校对和微调。")
        h2.setObjectName("muted")
        hero_layout.addWidget(h1)
        hero_layout.addWidget(h2)
        layout.addWidget(hero)

        material = QFrame()
        material.setObjectName("card")
        material_layout = QVBoxLayout(material)
        material_layout.setContentsMargins(18, 18, 18, 18)
        material_layout.setSpacing(10)
        material_layout.addWidget(self._section_title("素材与输出"))

        self.audio_row = PathRow("音频", "选择 WAV / MP3 / FLAC / M4A", "open", "音频 (*.wav *.mp3 *.flac *.m4a);;所有文件 (*.*)")
        self.bg_row = PathRow("背景", "选择背景图片", "open", "图片 (*.png *.jpg *.jpeg *.bmp);;所有文件 (*.*)")
        self.txt_row = PathRow("TXT", "选择纯文本歌词", "open", "文本 (*.txt);;所有文件 (*.*)")
        self.srt_row = PathRow("SRT", "选择或设置 SRT 路径", "save", "SRT 字幕 (*.srt)")
        self.out_row = PathRow("MP4", "选择导出视频路径", "save", "MP4 视频 (*.mp4)")
        self.ffmpeg_row = PathRow("FFmpeg", "ffmpeg.exe 路径", "open", "ffmpeg.exe (ffmpeg.exe);;所有文件 (*.*)")
        for row in (self.audio_row, self.bg_row, self.txt_row, self.srt_row, self.out_row, self.ffmpeg_row):
            material_layout.addWidget(row)
        layout.addWidget(material)

        action = QFrame()
        action.setObjectName("card")
        action_layout = QHBoxLayout(action)
        action_layout.setContentsMargins(18, 14, 18, 14)
        action_layout.setSpacing(10)
        self.model_combo = QComboBox()
        self.model_combo.addItem("高精度（推荐）", QUALITY_ACCURATE)
        self.model_combo.addItem("快速兼容", QUALITY_FAST)
        self.model_combo.setFixedWidth(150)
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("Turbo 增强", MODEL_ENHANCED)
        self.engine_combo.addItem("Small 轻量", MODEL_LIGHTWEIGHT)
        self.device_combo = QComboBox()
        self.device_combo.addItem("CPU", DEVICE_CPU)
        self.device_combo.addItem("NVIDIA GPU", DEVICE_CUDA)
        self.generate_btn = QPushButton("从 TXT 生成 SRT 初稿")
        self.load_srt_btn = QPushButton("载入 SRT")
        self.save_srt_btn = QPushButton("保存 SRT")
        self.preview_btn = QPushButton("预览当前画面")
        self.render_btn = QPushButton("导出视频")
        self.generate_btn.setObjectName("primaryButton")
        self.render_btn.setObjectName("primaryButton")
        self.generate_btn.clicked.connect(self.generate_srt)
        self.load_srt_btn.clicked.connect(self.load_srt_file)
        self.save_srt_btn.clicked.connect(self.save_srt_file)
        self.preview_btn.clicked.connect(self.preview_current_frame)
        self.render_btn.clicked.connect(self.render_video_task)
        action_layout.addWidget(QLabel("模型"))
        action_layout.addWidget(self.model_combo)
        action_layout.addWidget(self.engine_combo)
        action_layout.addWidget(self.device_combo)
        action_layout.addWidget(self.generate_btn)
        action_layout.addStretch(1)
        action_layout.addWidget(self.load_srt_btn)
        action_layout.addWidget(self.save_srt_btn)
        action_layout.addWidget(self.preview_btn)
        action_layout.addWidget(self.render_btn)
        layout.addWidget(action)

        lyrics = QFrame()
        lyrics.setObjectName("card")
        lyrics_layout = QVBoxLayout(lyrics)
        lyrics_layout.setContentsMargins(18, 18, 18, 18)
        lyrics_layout.setSpacing(12)
        lyrics_layout.addWidget(self._section_title("歌词校对"))
        self.table = QTableWidget(0, 4)
        self.table.setObjectName("lyricsTable")
        self.table.setHorizontalHeaderLabels(["#", "开始", "结束", "歌词"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.cellChanged.connect(self._table_changed)
        lyrics_layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.range_group = QButtonGroup(self)
        self.current_radio = QRadioButton("当前行")
        self.selected_radio = QRadioButton("选中行")
        self.to_end_radio = QRadioButton("当前到结尾")
        self.selected_radio.setChecked(True)
        for button in (self.current_radio, self.selected_radio, self.to_end_radio):
            self.range_group.addButton(button)
            controls.addWidget(button)
        controls.addSpacing(14)
        for seconds in (0.1, 0.5, 1.0):
            early = QPushButton(f"提前 {seconds:g}s")
            late = QPushButton(f"延后 {seconds:g}s")
            early.clicked.connect(lambda _=False, value=seconds: self.shift_time(-value))
            late.clicked.connect(lambda _=False, value=seconds: self.shift_time(value))
            controls.addWidget(early)
            controls.addWidget(late)
        controls.addStretch(1)
        self.delete_btn = QPushButton("删除行")
        self.merge_btn = QPushButton("合并到上一行")
        self.delete_btn.clicked.connect(self.delete_current_line)
        self.merge_btn.clicked.connect(self.merge_with_previous)
        controls.addWidget(self.merge_btn)
        controls.addWidget(self.delete_btn)
        lyrics_layout.addLayout(controls)
        layout.addWidget(lyrics, 1)
        return page

    def _build_preview_panel(self) -> QWidget:
        page = QWidget()
        page.setMinimumWidth(540)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        preview_card = QFrame()
        preview_card.setObjectName("previewCard")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(18, 18, 18, 18)
        preview_layout.setSpacing(12)
        preview_layout.addWidget(self._section_title("画面预览"))
        self.preview_label = QLabel("选择一行歌词后预览")
        self.preview_label.setObjectName("preview")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(480, 640)
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_layout.addWidget(self.preview_label, 1)
        layout.addWidget(preview_card, 3)

        log_card = QFrame()
        log_card.setObjectName("card")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(18, 18, 18, 18)
        log_layout.setSpacing(10)
        log_layout.addWidget(self._section_title("任务日志"))
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(110)
        log_layout.addWidget(self.log_box)
        layout.addWidget(log_card)
        return page

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _spin(self, minimum: int, maximum: int) -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        return spin

    def _apply_theme(self) -> None:
        font = QFont("Microsoft YaHei UI", 10)
        QApplication.instance().setFont(font)
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f5f5f7; color: #1d1d1f; }
            #sidebar {
                background: rgba(255,255,255,0.82);
                border: 1px solid #e5e5ea;
                border-radius: 18px;
            }
            #appTitle { font-size: 22px; font-weight: 700; letter-spacing: 0px; }
            #muted, .QLabel#muted { color: #6e6e73; }
            #navItem {
                color: #3a3a3c;
                padding: 10px 12px;
                border-radius: 12px;
                background: rgba(255,255,255,0.55);
            }
            #hero {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ffffff, stop:1 #eef5ff);
                border: 1px solid #e5e5ea;
                border-radius: 20px;
            }
            #heroTitle { font-size: 24px; font-weight: 700; }
            #card, #previewCard {
                background: rgba(255,255,255,0.92);
                border: 1px solid #e5e5ea;
                border-radius: 18px;
            }
            #sectionTitle { font-size: 15px; font-weight: 700; }
            #fieldLabel { color: #6e6e73; }
            QLineEdit, QComboBox, QSpinBox {
                background: #ffffff;
                border: 1px solid #d8d8de;
                border-radius: 10px;
                padding: 8px 10px;
                min-height: 22px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid #007aff;
            }
            QPushButton {
                background: #ffffff;
                border: 1px solid #d8d8de;
                border-radius: 11px;
                padding: 8px 14px;
                color: #1d1d1f;
            }
            QPushButton:hover { background: #f0f6ff; border-color: #b9d7ff; }
            QPushButton#primaryButton {
                background: #007aff;
                color: #ffffff;
                border: 1px solid #007aff;
                font-weight: 600;
            }
            QPushButton#primaryButton:hover { background: #0a84ff; }
            QPushButton#secondaryButton { color: #007aff; }
            QTableWidget {
                background: #ffffff;
                border: 1px solid #e5e5ea;
                border-radius: 12px;
                gridline-color: transparent;
                selection-background-color: #dbeafe;
                selection-color: #1d1d1f;
            }
            QTableWidget#lyricsTable {
                font-size: 15px;
                alternate-background-color: #fbfbfd;
            }
            QTableWidget#lyricsTable::item {
                padding: 10px 8px;
                border-bottom: 1px solid #f0f0f2;
            }
            QTableWidget#lyricsTable::item:selected {
                background: #e8f2ff;
                color: #0b3d71;
            }
            QHeaderView::section {
                background: #f5f5f7;
                color: #6e6e73;
                border: 0;
                border-bottom: 1px solid #e5e5ea;
                padding: 8px;
                font-weight: 600;
            }
            #preview {
                background: #111111;
                color: #8e8e93;
                border-radius: 16px;
            }
            QTextEdit {
                background: #fbfbfd;
                border: 1px solid #e5e5ea;
                border-radius: 12px;
                padding: 8px;
            }
            QSplitter::handle { background: transparent; }
            """
        )

    def _load_project_to_ui(self) -> None:
        self.audio_row.set_text(self.project.audio_path)
        self.bg_row.set_text(self.project.background_path)
        self.txt_row.set_text(self.project.txt_path)
        self.srt_row.set_text(self.project.srt_path)
        self.out_row.set_text(self.project.output_path)
        self.ffmpeg_row.set_text(self.project.ffmpeg_path)
        self.title_edit.setText(self.project.title)
        self.credit_edit.setText(self.project.credit_line)
        style = self.project.normalized().style
        self.title_size.setValue(style.title_font_size)
        self.active_size.setValue(style.active_font_size)
        self.near_size.setValue(style.near_font_size)
        self.center_y.setValue(style.lyrics_center_y)

    def _ui_to_project(self) -> None:
        self.project.audio_path = self.audio_row.text()
        self.project.background_path = self.bg_row.text()
        self.project.txt_path = self.txt_row.text()
        self.project.srt_path = self.srt_row.text()
        self.project.output_path = self.out_row.text()
        self.project.ffmpeg_path = self.ffmpeg_row.text()
        self.project.title = self.title_edit.text().strip() or "未命名歌曲"
        self.project.credit_line = self.credit_edit.text().strip()
        style = self.project.normalized().style
        style.title_font_size = self.title_size.value()
        style.active_font_size = self.active_size.value()
        style.near_font_size = self.near_size.value()
        style.far_font_size = max(18, self.near_size.value() - 6)
        style.lyrics_center_y = self.center_y.value()

    def _refresh_table(self) -> None:
        self.table_updating = True
        self.table.setRowCount(0)
        for row, line in enumerate(self.project.lines or []):
            self.table.insertRow(row)
            self.table.setRowHeight(row, 62)
            values = [str(row + 1), format_timestamp(line.start), format_timestamp(line.end), line.text]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 3:
                    item.setFont(QFont("Microsoft YaHei UI", 15))
                if col == 0:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    item.setTextAlignment(Qt.AlignCenter)
                elif col in (1, 2):
                    item.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, col, item)
        self.table_updating = False

    def _table_changed(self, row: int, _col: int) -> None:
        if self.table_updating or row < 0 or row >= len(self.project.lines or []):
            return
        try:
            start = parse_timestamp(self.table.item(row, 1).text())
            end = parse_timestamp(self.table.item(row, 2).text())
            text = self.table.item(row, 3).text().strip()
            self.project.lines[row] = LyricLine(start, end, text)
        except Exception as exc:
            self.log(f"第 {row + 1} 行格式有误：{exc}")

    def _selected_rows(self) -> list[int]:
        return sorted({index.row() for index in self.table.selectedIndexes()})

    def _active_row(self) -> int | None:
        rows = self._selected_rows()
        if rows:
            return rows[0]
        row = self.table.currentRow()
        return row if row >= 0 else None

    def _target_rows(self) -> list[int]:
        active = self._active_row()
        if active is None:
            return []
        if self.current_radio.isChecked():
            return [active]
        if self.to_end_radio.isChecked():
            return list(range(active, len(self.project.lines or [])))
        return self._selected_rows() or [active]

    def log(self, message: str) -> None:
        self.log_box.append(message)

    def _run_task(self, task, done, failed=None) -> None:
        signals = TaskSignals()
        self.task_signals.append(signals)
        signals.log.connect(self.log)
        signals.done.connect(done)
        signals.failed.connect(lambda msg: QMessageBox.critical(self, "任务失败", msg))
        signals.failed.connect(lambda msg: self.log(f"失败：{msg}"))
        if failed is not None:
            signals.failed.connect(failed)

        def runner() -> None:
            try:
                result = task(signals.log.emit)
                signals.done.emit(result)
            except Exception as exc:
                signals.failed.emit(str(exc))

        threading.Thread(target=runner, daemon=True).start()

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "打开项目", str(APP_DIR), "歌词项目 (*.lyricproj *.json);;所有文件 (*.*)")
        if not path:
            return
        try:
            self.project = load_project(path).normalized()
            self.project_path = Path(path)
            self._load_project_to_ui()
            self._refresh_table()
            self.log(f"已打开项目：{path}")
        except Exception as exc:
            QMessageBox.critical(self, "打开失败", str(exc))

    def save_project_file(self) -> None:
        self._ui_to_project()
        path, _ = QFileDialog.getSaveFileName(self, "保存项目", str(self.project_path), "歌词项目 (*.lyricproj);;JSON (*.json)")
        if not path:
            return
        try:
            save_project(path, self.project)
            self.project_path = Path(path)
            self.log(f"已保存项目：{path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def load_srt_file(self) -> None:
        self._ui_to_project()
        if not self.project.srt_path:
            path, _ = QFileDialog.getOpenFileName(self, "载入 SRT", str(APP_DIR), "SRT 字幕 (*.srt)")
            if path:
                self.srt_row.set_text(path)
                self.project.srt_path = path
        try:
            self.project.lines = load_srt(self.project.srt_path)
            self._refresh_table()
            self.log(f"已载入 SRT：{self.project.srt_path}")
        except Exception as exc:
            QMessageBox.critical(self, "载入失败", str(exc))

    def save_srt_file(self) -> None:
        self._ui_to_project()
        if not self.project.srt_path:
            QMessageBox.warning(self, "缺少路径", "请先设置 SRT 保存路径。")
            return
        try:
            save_srt(self.project.srt_path, self.project.lines or [])
            self.log(f"已保存 SRT：{self.project.srt_path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def generate_srt(self) -> None:
        self._ui_to_project()
        if not self.project.audio_path or not self.project.txt_path:
            QMessageBox.warning(self, "缺少素材", "请先选择音频和 TXT 歌词。")
            return
        if not self.project.srt_path:
            self.project.srt_path = str(Path(self.project.audio_path).with_suffix(".srt"))
            self.srt_row.set_text(self.project.srt_path)

        self.generate_btn.setEnabled(False)
        self.log("开始用 TXT 歌词约束音频对齐...")
        quality_mode = self.model_combo.currentData() or QUALITY_ACCURATE
        model_name = self.engine_combo.currentData() or MODEL_ENHANCED
        device = self.device_combo.currentData() or DEVICE_CPU

        def task(progress):
            return generate_srt_from_txt(
                self.project.audio_path,
                self.project.txt_path,
                self.project.srt_path,
                model_name=model_name,
                progress=progress,
                quality_mode=quality_mode,
                device=device,
            )

        def done(lines):
            self.project.lines = lines
            self._refresh_table()
            self.generate_btn.setEnabled(True)
            self.log("SRT 初稿已载入表格。")

        self._run_task(task, done, lambda _msg: self.generate_btn.setEnabled(True))

    def shift_time(self, delta: float) -> None:
        rows = self._target_rows()
        if not rows or not self.project.lines:
            return
        for row in rows:
            line = self.project.lines[row]
            duration = max(0.1, line.end - line.start)
            start = max(0.0, line.start + delta)
            end = max(start + 0.1, start + duration)
            self.project.lines[row] = LyricLine(start, end, line.text)
        self._refresh_table()
        for row in rows:
            self.table.selectRow(row)
        self.log(f"已调整 {len(rows)} 行：{delta:+g}s")

    def merge_with_previous(self) -> None:
        row = self._active_row()
        if row is None or row <= 0 or not self.project.lines:
            return
        prev = self.project.lines[row - 1]
        cur = self.project.lines[row]
        self.project.lines[row - 1] = LyricLine(prev.start, cur.end, f"{prev.text} {cur.text}".strip())
        del self.project.lines[row]
        self._refresh_table()
        self.table.selectRow(row - 1)
        self.log(f"已合并第 {row} 行和第 {row + 1} 行。")

    def delete_current_line(self) -> None:
        row = self._active_row()
        if row is None or not self.project.lines:
            return
        del self.project.lines[row]
        self._refresh_table()
        if self.project.lines:
            self.table.selectRow(min(row, len(self.project.lines) - 1))
        self.log(f"已删除第 {row + 1} 行。")

    def preview_current_frame(self) -> None:
        self._ui_to_project()
        row = self._active_row()
        timestamp = self.project.lines[row].start if row is not None and self.project.lines else 10.0
        try:
            render_preview(self.project, self.preview_path, timestamp)
            self._show_preview_image()
            self.log(f"已生成预览帧：{format_timestamp(timestamp)}")
        except Exception as exc:
            QMessageBox.critical(self, "预览失败", str(exc))

    def _show_preview_image(self) -> None:
        pixmap = QPixmap(str(self.preview_path))
        if pixmap.isNull():
            return
        scaled = pixmap.scaled(self.preview_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview_label.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.preview_path.exists() and self.preview_label.pixmap() is not None:
            self._show_preview_image()

    def render_video_task(self) -> None:
        self._ui_to_project()
        if not self.project.output_path:
            QMessageBox.warning(self, "缺少路径", "请先设置 MP4 输出路径。")
            return
        self.save_srt_file()
        self.render_btn.setEnabled(False)
        self.log("开始导出视频...")

        def task(progress):
            return render_video(self.project, progress)

        def done(path):
            self.render_btn.setEnabled(True)
            self.out_row.set_text(str(path))
            self.log(f"导出完成：{path}")

        self._run_task(task, done, lambda _msg: self.render_btn.setEnabled(True))


def run_portable_self_test() -> int:
    report_path = APP_DIR / "绿色版自检结果.txt"
    checks: list[str] = []
    try:
        model_dir = APP_DIR / "models" / MODEL_LIGHTWEIGHT
        enhanced_model_dir = APP_DIR / "models" / MODEL_ENHANCED
        vad_path = APP_DIR / "_internal" / "faster_whisper" / "assets" / "silero_vad_v6.onnx"
        ffmpeg_path = APP_DIR / "ffmpeg" / "ffmpeg.exe"
        ffprobe_path = APP_DIR / "ffmpeg" / "ffprobe.exe"

        for label, path in (
            ("small 模型", model_dir / "model.bin"),
            ("Turbo 增强模型", enhanced_model_dir / "model.bin"),
            ("VAD 模型", vad_path),
            ("FFmpeg", ffmpeg_path),
            ("FFprobe", ffprobe_path),
        ):
            if not path.exists():
                raise FileNotFoundError(f"缺少{label}：{path}")
            checks.append(f"[正常] {label}：{path}")

        available, gpu_message = gpu_runtime_status(APP_DIR)
        checks.append(f"[{'正常' if available else '可选'}] GPU：{gpu_message}")

        from faster_whisper import WhisperModel

        WhisperModel(str(model_dir), device="cpu", compute_type="int8")
        checks.append("[正常] faster-whisper CPU 模型载入成功")
        WhisperModel(str(enhanced_model_dir), device="cpu", compute_type="int8")
        checks.append("[正常] Turbo 增强模型 CPU 载入成功")
        if available:
            import wave

            gpu_probe_path = APP_DIR / ".gpu_self_test.wav"
            try:
                with wave.open(str(gpu_probe_path), "wb") as probe_wav:
                    probe_wav.setnchannels(1)
                    probe_wav.setsampwidth(2)
                    probe_wav.setframerate(16_000)
                    probe_wav.writeframes(b"\x00\x00" * 16_000)
                gpu_model = WhisperModel(str(enhanced_model_dir), device="cuda", compute_type="float16")
                segments, _info = gpu_model.transcribe(
                    str(gpu_probe_path), language="zh", beam_size=1, vad_filter=False
                )
                list(segments)
                checks.append("[正常] Turbo 增强模型 GPU 实际推理成功")
            finally:
                gpu_probe_path.unlink(missing_ok=True)

        version = subprocess.run(
            [str(ffmpeg_path), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=20,
            check=True,
        ).stdout.splitlines()[0]
        checks.append(f"[正常] {version}")
        probe_version = subprocess.run(
            [str(ffprobe_path), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=20,
            check=True,
        ).stdout.splitlines()[0]
        checks.append(f"[正常] {probe_version}")
        checks.append("\n自检通过：本机可以运行歌词视频生成器 Studio。")
        result = 0
    except Exception as exc:
        checks.append(f"\n[失败] {type(exc).__name__}: {exc}")
        result = 1

    report_path.write_text("\n".join(checks), encoding="utf-8-sig")
    return result


def main() -> None:
    if "--self-test" in sys.argv:
        raise SystemExit(run_portable_self_test())
    app = QApplication(sys.argv)
    window = LyricsStudio()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
