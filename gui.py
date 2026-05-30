"""
卡拉OK字幕生成器 GUI
拖入音频+歌词，一键生成带时间轴的卡拉OK字幕文件
"""

import sys
import os
import re
import threading
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from PIL import Image, ImageTk

from furigana import add_furigana
import main as main_module
import separate

# ── 拖拽支持（可选） ──────────────────────────────────────────
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# ── 配色 ──────────────────────────────────────────────────────
BG_MAIN   = "#E8F4FD"   # 主背景淡蓝
BG_CARD   = "#FFFFFF"   # 卡片白色
BG_LOG    = "#F8FBFF"   # 日志区
BLUE      = "#4A90D9"   # 按钮蓝
BLUE_HOVER = "#357ABD"  # 按钮悬停
TEXT_DARK  = "#333333"  # 正文
TEXT_GREY  = "#999999"  # 提示文字
GREEN      = "#27AE60"  # 成功
RED        = "#E74C3C"  # 错误
CARD_BORDER = "#D0DFF0"

FONT_TITLE = ("Microsoft YaHei", 16, "bold")
FONT_LABEL = ("Microsoft YaHei", 10)
FONT_PATH  = ("Microsoft YaHei", 9)
FONT_LOG   = ("Consolas", 9)
FONT_BTN   = ("Microsoft YaHei", 10, "bold")

# ── 标准输出重定向 ────────────────────────────────────────────
class LogRedirector:
    """把工作线程的 stdout/stderr 安全地转发到主线程日志框。
    线程安全（Lock）+ 容错（bytes/解码失败一律 'replace'），避免 Windows GBK 与 UTF-8 混用时崩溃。"""
    def __init__(self, callback):
        self.callback = callback
        self.buffer = ""
        import threading
        self._lock = threading.Lock()

    def write(self, text):
        # 防御：上游若误传 bytes（少见，但 C 扩展可能），先解码
        if isinstance(text, (bytes, bytearray)):
            try:
                text = text.decode('utf-8', errors='replace')
            except Exception:
                text = str(text)
        emit = []
        with self._lock:
            self.buffer += text
            if '\n' in self.buffer:
                lines = self.buffer.split('\n')
                self.buffer = lines.pop()
                emit = [l for l in lines if l.strip()]
        for line in emit:
            self.callback(line.strip())

    def flush(self):
        with self._lock:
            rest = self.buffer.strip()
            self.buffer = ""
        if rest:
            self.callback(rest)


# ── 主界面 ────────────────────────────────────────────────────
class KaraokeApp:
    def __init__(self, root):
        self.root = root
        self.root.title("卡拉OK字幕生成器")
        self.root.geometry("780x800")
        self.root.minsize(600, 700)
        self.root.configure(bg=BG_MAIN)

        # 状态变量
        self.audio_path = tk.StringVar()
        self.lyrics_text = ""          # 歌词原文（不再追踪文件路径）
        self.lyrics_status = tk.StringVar(value="未输入歌词")
        _default_out = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(_default_out):
            _default_out = os.path.expanduser("~")
        self.output_dir = tk.StringVar(value=_default_out)
        self.auto_furi = tk.BooleanVar(value=True)
        self.auto_full = tk.BooleanVar(value=True)   # 全自动模式：跳过注音确认弹窗
        self.tail_correct = tk.StringVar(value="3")
        self.audio_speed = tk.StringVar(value="1.0")
        self.chars_per_line = tk.StringVar(value="0")
        self.advanced_visible = False
        self.processing = False
        self.stage_var = tk.StringVar(value="就绪")
        self._temp_files = []       # 自动清理：弹窗/启动产生的临时文件
        self._last_output = None    # 上次成功输出的 .ass 路径（完成面板用）
        self._last_audio_dir = None # 浏览对话框记忆上次目录

        self._load_settings()       # 从 %LOCALAPPDATA%\autoKara\settings.json 恢复用户偏好
        self._build_ui()
        self._setup_dnd()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 用户偏好持久化 ──────────────────────────────────────
    def _settings_path(self):
        base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'autoKara', 'settings.json')

    def _load_settings(self):
        try:
            import json
            p = self._settings_path()
            if not os.path.isfile(p):
                return
            with open(p, 'r', encoding='utf-8') as f:
                d = json.load(f)
            if 'output_dir' in d and os.path.isdir(d['output_dir']):
                self.output_dir.set(d['output_dir'])
            for key, var in (('auto_furi', self.auto_furi),
                             ('auto_full', self.auto_full)):
                if key in d:
                    var.set(bool(d[key]))
            for key, var in (('tail_correct', self.tail_correct),
                             ('audio_speed', self.audio_speed),
                             ('chars_per_line', self.chars_per_line)):
                if key in d:
                    var.set(str(d[key]))
            self._last_audio_dir = d.get('last_audio_dir')
        except Exception:
            pass  # 损坏的设置文件不阻塞启动

    def _save_settings(self):
        try:
            import json
            p = self._settings_path()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            d = {
                'output_dir': self.output_dir.get(),
                'auto_furi': self.auto_furi.get(),
                'auto_full': self.auto_full.get(),
                'tail_correct': self.tail_correct.get(),
                'audio_speed': self.audio_speed.get(),
                'chars_per_line': self.chars_per_line.get(),
                'last_audio_dir': self._last_audio_dir,
            }
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        # 标题区域 —— Logo + 标题
        title_frame = tk.Frame(self.root, bg=BG_MAIN)
        title_frame.pack(fill=tk.X, padx=24, pady=(16, 6))

        # Logo + 窗口图标
        logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knm.png")
        if os.path.exists(logo_path):
            img = Image.open(logo_path)
            # 窗口图标：生成临时 .ico（Windows 任务栏 + 标题栏）
            ico_img = img.resize((64, 64), Image.LANCZOS)
            self._ico_path = os.path.join(tempfile.gettempdir(), "fa_kara_icon.ico")
            ico_img.save(self._ico_path, format="ICO")
            self.root.iconbitmap(self._ico_path)
            # 界面 Logo（大尺寸）
            img = img.resize((80, 80), Image.LANCZOS)
            self.logo_img = ImageTk.PhotoImage(img)
            tk.Label(title_frame, image=self.logo_img, bg=BG_MAIN).pack()
        else:
            self.logo_img = None

        tk.Label(title_frame, text="卡拉OK字幕生成器",
                 font=FONT_TITLE, bg=BG_MAIN, fg=TEXT_DARK).pack()
        tk.Label(title_frame, text="音频 + 歌词 → 带时间轴的字幕文件",
                 font=("Microsoft YaHei", 9), bg=BG_MAIN, fg=TEXT_GREY).pack()

        # 可滚动主内容区
        self.canvas = tk.Canvas(self.root, bg=BG_MAIN, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self.root, orient=tk.VERTICAL,
                                       command=self.canvas.yview)
        self.scroll_frame = tk.Frame(self.canvas, bg=BG_MAIN)

        self.scroll_frame.bind("<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        self._canvas_win = self.canvas.create_window(
            (0, 0), window=self.scroll_frame, anchor=tk.NW)

        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        # 画布宽度变化时同步内部窗口宽度
        def _on_canvas_resize(event):
            self.canvas.itemconfig(self._canvas_win, width=event.width)
        self.canvas.bind("<Configure>", _on_canvas_resize)
        # 鼠标滚轮滚动
        def _on_mousewheel(event):
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", _on_mousewheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True,
                         padx=(24, 0), pady=(4, 12))
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 24), pady=(4, 12))

        main = tk.Frame(self.scroll_frame, bg=BG_MAIN)
        main.pack(fill=tk.X)

        # ── 音频文件卡片 ──
        self._build_file_card(main, "音频文件", "拖拽音频文件到此处，或点击浏览",
                              self.audio_path, self._browse_audio)

        # ── 歌词区（状态栏 + 按钮） ──
        self._build_lyrics_section(main)

        # ── 输出目录卡片 ──
        out_card = tk.Frame(main, bg=BG_CARD, highlightbackground=CARD_BORDER,
                            highlightthickness=1, padx=14, pady=10)
        out_card.pack(fill=tk.X, pady=(0, 10))
        tk.Label(out_card, text="输出目录", font=FONT_LABEL,
                 bg=BG_CARD, fg=TEXT_DARK).grid(row=0, column=0, sticky=tk.W, columnspan=2)
        self.out_label = tk.Label(out_card, textvariable=self.output_dir,
                                  font=FONT_PATH, bg=BG_CARD, fg=GREEN, anchor=tk.W)
        self.out_label.grid(row=1, column=0, sticky=tk.EW, pady=(4, 0))
        tk.Button(out_card, text="浏览...", font=FONT_LABEL,
                  bg=BLUE, fg="white", activebackground=BLUE_HOVER,
                  activeforeground="white", relief=tk.FLAT, cursor="hand2",
                  padx=12, pady=2,
                  command=self._browse_output).grid(row=1, column=1, padx=(8, 0), pady=(4, 0))
        out_card.grid_columnconfigure(0, weight=1)

        # ── 选项区 ──
        opt_frame = tk.Frame(main, bg=BG_MAIN)
        opt_frame.pack(fill=tk.X, pady=(0, 6))
        tk.Checkbutton(opt_frame, text="自动注音（纯日文歌词自动添加 {漢字|よみ} 标记）",
                       variable=self.auto_furi, font=FONT_LABEL,
                       bg=BG_MAIN, fg=TEXT_DARK, activebackground=BG_MAIN,
                       selectcolor=BG_MAIN, anchor=tk.W).pack(anchor=tk.W)
        tk.Checkbutton(opt_frame, text="全自动模式（跳过注音确认弹窗，全程无需人工介入）",
                       variable=self.auto_full, font=FONT_LABEL,
                       bg=BG_MAIN, fg=TEXT_DARK, activebackground=BG_MAIN,
                       selectcolor=BG_MAIN, anchor=tk.W).pack(anchor=tk.W)

        # ── 高级参数（折叠） ──
        self.adv_toggle = tk.Label(main, text="▶ 高级参数",
                                   font=FONT_LABEL, bg=BG_MAIN, fg=BLUE, cursor="hand2")
        self.adv_toggle.pack(anchor=tk.W)
        self.adv_toggle.bind("<Button-1>", lambda e: self._toggle_advanced())

        self.adv_frame = tk.Frame(main, bg=BG_CARD, highlightbackground=CARD_BORDER,
                                  highlightthickness=1, padx=14, pady=10)
        # 默认不 pack，等展开时再 pack

        adv_inner = tk.Frame(self.adv_frame, bg=BG_CARD)
        adv_inner.pack()

        self._adv_row(adv_inner, 0, "尾音修正:", self.tail_correct, "1/2/3，默认3",
                      is_combo=True, combo_values=["1", "2", "3"])
        self._adv_row(adv_inner, 1, "音频倍速:", self.audio_speed, "默认 1.0")
        self._adv_row(adv_inner, 2, "每行字数限制:", self.chars_per_line, "0=不限制")

        # ── 阶段标签 + 进度条 ──
        stage_frame = tk.Frame(main, bg=BG_MAIN)
        stage_frame.pack(fill=tk.X, pady=(8, 2))
        tk.Label(stage_frame, text="当前阶段: ", font=FONT_LABEL,
                 bg=BG_MAIN, fg=TEXT_DARK).pack(side=tk.LEFT)
        self.stage_label = tk.Label(stage_frame, textvariable=self.stage_var,
                                     font=("Microsoft YaHei", 10, "bold"),
                                     bg=BG_MAIN, fg=BLUE)
        self.stage_label.pack(side=tk.LEFT)
        self.progress = ttk.Progressbar(main, mode='indeterminate')
        self.progress.pack(fill=tk.X, pady=(2, 8))

        # ── 日志区 ──
        log_frame = tk.Frame(main, bg=BG_LOG, highlightbackground=CARD_BORDER,
                             highlightthickness=1, height=200)
        log_frame.pack(fill=tk.X, pady=(0, 8))
        log_frame.pack_propagate(False)
        tk.Label(log_frame, text="  处理日志", font=FONT_LABEL,
                 bg=BG_LOG, fg=TEXT_DARK, anchor=tk.W).pack(fill=tk.X, padx=4, pady=(4, 0))

        self.log_text = tk.Text(log_frame, font=FONT_LOG, bg=BG_LOG, fg=TEXT_DARK,
                                relief=tk.FLAT, padx=8, pady=6, wrap=tk.WORD,
                                state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        # 日志颜色标签
        self.log_text.tag_config("success", foreground=GREEN)
        self.log_text.tag_config("error", foreground=RED)
        self.log_text.tag_config("info", foreground=BLUE)

        # 日志滚动条
        scrollbar = ttk.Scrollbar(self.log_text, orient=tk.VERTICAL,
                                  command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 日志框滚轮：只滚动日志本身，不触发外层 Canvas 滚动
        def _on_log_wheel(event):
            self.log_text.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"
        self.log_text.bind("<Enter>",
            lambda e: self.log_text.bind("<MouseWheel>", _on_log_wheel))
        self.log_text.bind("<Leave>",
            lambda e: self.log_text.unbind("<MouseWheel>"))

        # ── 开始按钮 ──
        self.run_btn = tk.Button(main, text="▶  开始处理", font=FONT_BTN,
                                 bg=BLUE, fg="white", activebackground=BLUE_HOVER,
                                 activeforeground="white", relief=tk.FLAT,
                                 cursor="hand2", padx=30, pady=8,
                                 command=self._start_processing)
        self.run_btn.pack(pady=(4, 8))

    def _build_file_card(self, parent, title, placeholder, path_var, browse_cmd):
        """构建音频文件输入卡片"""
        card = tk.Frame(parent, bg=BG_CARD, highlightbackground=CARD_BORDER,
                        highlightthickness=1, padx=14, pady=10)
        card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(card, text=title, font=FONT_LABEL,
                 bg=BG_CARD, fg=TEXT_DARK).grid(row=0, column=0, sticky=tk.W, columnspan=2)

        # 拖放区视觉提示：浅蓝底色 + 虚线感边框 + 悬停效果
        self.audio_drop_label = tk.Label(card, text=placeholder,
                                         font=("Microsoft YaHei", 10), bg="#F0F6FC",
                                         fg=TEXT_GREY, anchor=tk.CENTER, padx=10, pady=14,
                                         relief=tk.RIDGE, borderwidth=2)
        self.audio_drop_label.grid(row=1, column=0, sticky=tk.EW, pady=(4, 0))
        self.audio_drop_label.bind("<Button-1>", lambda e: browse_cmd())
        self.audio_drop_label.bind("<Enter>",
            lambda e: self.audio_drop_label.config(bg="#E1EFFC"))
        self.audio_drop_label.bind("<Leave>",
            lambda e: self.audio_drop_label.config(bg="#F0F6FC"))

        path_lbl = tk.Label(card, textvariable=path_var,
                            font=FONT_PATH, bg=BG_CARD, fg=GREEN, anchor=tk.W)
        path_lbl.grid(row=2, column=0, sticky=tk.EW, pady=(4, 0))

        tk.Button(card, text="浏览...", font=FONT_LABEL,
                  bg=BLUE, fg="white", activebackground=BLUE_HOVER,
                  activeforeground="white", relief=tk.FLAT, cursor="hand2",
                  padx=12, pady=2,
                  command=browse_cmd).grid(row=1, column=1, padx=(8, 0), pady=(4, 0),
                                           sticky=tk.N)

        card.grid_columnconfigure(0, weight=1)

    def _build_lyrics_section(self, parent):
        """构建歌词状态栏 + 按钮"""
        card = tk.Frame(parent, bg=BG_CARD, highlightbackground=CARD_BORDER,
                        highlightthickness=1, padx=14, pady=10)
        card.pack(fill=tk.X, pady=(0, 10))

        tk.Label(card, text="歌词", font=FONT_LABEL,
                 bg=BG_CARD, fg=TEXT_DARK).grid(row=0, column=0, sticky=tk.W, columnspan=3)

        self.lyrics_status_label = tk.Label(card, textvariable=self.lyrics_status,
                                            font=FONT_PATH, bg=BG_CARD, fg=GREEN, anchor=tk.W)
        self.lyrics_status_label.grid(row=1, column=0, sticky=tk.EW, pady=(4, 0))

        btn_frame = tk.Frame(card, bg=BG_CARD)
        btn_frame.grid(row=1, column=1, columnspan=2, sticky=tk.E, pady=(4, 0))

        tk.Button(btn_frame, text="输入歌词...", font=FONT_LABEL,
                  bg=BLUE, fg="white", activebackground=BLUE_HOVER,
                  activeforeground="white", relief=tk.FLAT, cursor="hand2",
                  padx=10, pady=2,
                  command=self._open_lyrics_popup).pack(side=tk.LEFT, padx=(0, 6))

        tk.Button(btn_frame, text="从文件导入...", font=FONT_LABEL,
                  bg="#8EBBE0", fg="white", activebackground="#7AABD0",
                  activeforeground="white", relief=tk.FLAT, cursor="hand2",
                  padx=10, pady=2,
                  command=self._import_lyrics_file).pack(side=tk.LEFT)

        card.grid_columnconfigure(0, weight=1)

    # ── 弹窗 ────────────────────────────────────────────────

    def _make_popup(self, title, width, height):
        """创建模态弹窗模板"""
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.geometry(f"{width}x{height}")
        popup.configure(bg=BG_MAIN)
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_set()
        # 居中于主窗口
        popup.update_idletasks()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - height) // 2
        popup.geometry(f"+{max(rx, 0)}+{max(ry, 0)}")
        return popup

    def _open_lyrics_popup(self, from_file=None):
        """歌词输入弹窗"""
        popup = self._make_popup("输入歌词", 720, 600)

        tk.Label(popup, text="请粘贴日文歌词（每行一句）。\n汉字可用「{漢字|よみ}」标记，也可用「夜空（そら）」指定人为读音。",
                 font=FONT_LABEL, bg=BG_MAIN, fg=TEXT_DARK, justify=tk.LEFT).pack(anchor=tk.W, padx=14, pady=(12, 4))

        text_frame = tk.Frame(popup, bg=BG_CARD, highlightbackground=CARD_BORDER,
                              highlightthickness=1)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))

        text_widget = tk.Text(text_frame, font=("Consolas", 11),
                              bg="#FEFEFE", fg=TEXT_DARK,
                              relief=tk.FLAT, padx=10, pady=8,
                              wrap=tk.WORD, undo=True)
        text_widget.pack(fill=tk.BOTH, expand=True)

        # 如果有已有内容或从文件导入，预填充
        if from_file:
            text_widget.insert(1.0, from_file)
        elif self.lyrics_text:
            text_widget.insert(1.0, self.lyrics_text)

        btn_frame = tk.Frame(popup, bg=BG_MAIN)
        btn_frame.pack(pady=(0, 14))

        result = {"text": None}

        def on_confirm():
            result["text"] = text_widget.get(1.0, tk.END).strip()
            popup.destroy()

        def on_cancel():
            popup.destroy()

        tk.Button(btn_frame, text="确认", font=FONT_BTN,
                  bg=BLUE, fg="white", activebackground=BLUE_HOVER,
                  activeforeground="white", relief=tk.FLAT, cursor="hand2",
                  padx=24, pady=6, command=on_confirm).pack(side=tk.LEFT, padx=(0, 12))

        tk.Button(btn_frame, text="取消", font=FONT_BTN,
                  bg="#CCCCCC", fg=TEXT_DARK, activebackground="#BBBBBB",
                  activeforeground=TEXT_DARK, relief=tk.FLAT, cursor="hand2",
                  padx=24, pady=6, command=on_cancel).pack(side=tk.LEFT)

        popup.wait_window()

        if result["text"]:
            self.lyrics_text = result["text"]
            line_count = self.lyrics_text.count('\n') + 1
            self.lyrics_status.set(f"已输入歌词 ({line_count} 行)")
            self.lyrics_status_label.config(fg=GREEN)
            self.log_message(f"歌词已录入: {line_count} 行", "success")

    def _import_lyrics_file(self):
        """从文件导入歌词 → 弹窗显示内容"""
        path = filedialog.askopenfilename(
            title="选择歌词文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            fname = os.path.basename(path)
            self.lyrics_status.set(f"已导入: {fname}")
            self.lyrics_status_label.config(fg=GREEN)
            self.log_message(f"已从文件导入: {fname}", "info")
            # 打开弹窗让用户确认/编辑
            self._open_lyrics_popup(from_file=content)
        except Exception as e:
            self.log_message(f"导入失败: {e}", "error")

    def _open_furigana_popup(self, annotated_text):
        """注音预览弹窗（模态）—— 用户检查编辑后确认"""
        popup = self._make_popup("注音预览 - 请检查并修改", 680, 550)

        tk.Label(popup, text="请检查注音结果，可直接编辑修改。\n人或读音如「夜空（そら）」会自动转为「{夜空|そら}」。确认后继续对齐：",
                 font=FONT_LABEL, bg=BG_MAIN, fg=TEXT_DARK, justify=tk.LEFT).pack(anchor=tk.W, padx=14, pady=(12, 4))

        text_frame = tk.Frame(popup, bg=BG_CARD, highlightbackground=CARD_BORDER,
                              highlightthickness=1)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 10))

        text_widget = tk.Text(text_frame, font=("Consolas", 11),
                              bg="#FEFEFE", fg=TEXT_DARK,
                              relief=tk.FLAT, padx=10, pady=8,
                              wrap=tk.WORD, undo=True)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert(1.0, annotated_text)

        btn_frame = tk.Frame(popup, bg=BG_MAIN)
        btn_frame.pack(pady=(0, 14))

        confirmed = {"text": None}

        def on_confirm():
            confirmed["text"] = text_widget.get(1.0, tk.END).strip()
            popup.destroy()

        def on_cancel():
            popup.destroy()

        tk.Button(btn_frame, text="确认并继续", font=FONT_BTN,
                  bg=BLUE, fg="white", activebackground=BLUE_HOVER,
                  activeforeground="white", relief=tk.FLAT, cursor="hand2",
                  padx=24, pady=6, command=on_confirm).pack(side=tk.LEFT, padx=(0, 12))

        tk.Button(btn_frame, text="取消", font=FONT_BTN,
                  bg="#CCCCCC", fg=TEXT_DARK, activebackground="#BBBBBB",
                  activeforeground=TEXT_DARK, relief=tk.FLAT, cursor="hand2",
                  padx=24, pady=6, command=on_cancel).pack(side=tk.LEFT)

        popup.wait_window()
        return confirmed["text"]

    def _adv_row(self, parent, row, label, var, hint,
                 is_combo=False, combo_values=None):
        tk.Label(parent, text=label, font=FONT_LABEL, bg=BG_CARD,
                 fg=TEXT_DARK).grid(row=row, column=0, sticky=tk.W, pady=3)
        if is_combo:
            cb = ttk.Combobox(parent, textvariable=var, values=combo_values,
                              width=3, font=FONT_LABEL, state="readonly")
            cb.grid(row=row, column=1, sticky=tk.W, padx=(6, 14), pady=3)
        else:
            tk.Entry(parent, textvariable=var, font=FONT_LABEL,
                     width=7, bg=BG_CARD, relief=tk.SOLID,
                     borderwidth=1).grid(row=row, column=1, sticky=tk.W, padx=(6, 14), pady=3)
        tk.Label(parent, text=hint, font=("Microsoft YaHei", 8), bg=BG_CARD,
                 fg=TEXT_GREY).grid(row=row, column=2, sticky=tk.W, pady=3)

    # ── 拖拽 ────────────────────────────────────────────────

    def _setup_dnd(self):
        if not HAS_DND:
            return
        try:
            self.audio_drop_label.drop_target_register(DND_FILES)
            self.audio_drop_label.dnd_bind('<<Drop>>', self._on_drop_audio)
        except Exception:
            pass

    def _parse_drop_path(self, data):
        """解析拖拽得到的文件路径"""
        path = data.strip()
        if path.startswith('{') and path.endswith('}'):
            path = path[1:-1]
        # Windows 可能带回车换行
        path = path.split('\n')[0].strip()
        return os.path.normpath(path.replace('/', os.sep))

    def _on_drop_audio(self, event):
        path = self._parse_drop_path(event.data)
        if os.path.isfile(path):
            if not separate.is_supported_audio(path):
                self.log_message(f"错误：不支持的音频格式", "error")
                return
            self.audio_path.set(path)
            fname = os.path.basename(path)
            self.audio_drop_label.config(text=f"  ✓ 已选择: {fname}", fg=GREEN)

    def _update_drop_label(self, label, path, success):
        if success:
            fname = os.path.basename(path)
            label.config(text=f"  ✓ 已选择: {fname}", fg=GREEN)
        else:
            label.config(text=path, fg=TEXT_GREY)

    # ── 浏览 ────────────────────────────────────────────────

    def _browse_audio(self):
        kwargs = dict(title="选择音频文件",
                      filetypes=[("音频文件", "*.wav;*.mp3;*.flac;*.ogg;*.m4a"),
                                 ("所有文件", "*.*")])
        if self._last_audio_dir and os.path.isdir(self._last_audio_dir):
            kwargs['initialdir'] = self._last_audio_dir
        path = filedialog.askopenfilename(**kwargs)
        if path:
            self.audio_path.set(os.path.normpath(path))
            self._last_audio_dir = os.path.dirname(path)
            self._update_drop_label(self.audio_drop_label, path, True)

    def _browse_output(self):
        path = filedialog.askdirectory(title="选择输出文件夹")
        if path:
            self.output_dir.set(os.path.normpath(path))

    # ── 高级参数折叠 ─────────────────────────────────────────

    def _toggle_advanced(self):
        if self.advanced_visible:
            self.adv_frame.pack_forget()
            self.adv_toggle.config(text="▶ 高级参数")
        else:
            self.adv_frame.pack(fill=tk.X, pady=(0, 6), after=self.adv_toggle)
            self.adv_toggle.config(text="▼ 高级参数")
        self.advanced_visible = not self.advanced_visible

    # ── 日志 ─────────────────────────────────────────────────

    def log_message(self, message, tag=""):
        """线程安全地追加日志"""
        def _append():
            # 自动检测阶段
            if "正在进行人声分离" in message:
                self.stage_var.set("人声分离中...")
            elif "人声分离完成" in message:
                self.stage_var.set("打轴对齐中...")
            elif "Adding timelines" in message or "对齐" in message:
                self.stage_var.set("打轴对齐中...")
            elif "Success" in message or "所有文件已输出" in message:
                self.stage_var.set("完成")
                self.progress.stop()
            # 追加日志
            self.log_text.config(state=tk.NORMAL)
            self.log_text.insert(tk.END, message + "\n", tag)
            self.log_text.see(tk.END)
            self.log_text.config(state=tk.DISABLED)
        self.root.after(0, _append)

    def log(self, message):
        """通用日志（供 print 重定向使用）"""
        tag = ""
        if "错误" in message or "error" in message.lower() or "失败" in message:
            tag = "error"
        elif "完成" in message or "success" in message.lower() or "成功" in message:
            tag = "success"
        self.log_message(message, tag)

    # ── 处理流程 ─────────────────────────────────────────────

    def _start_processing(self):
        if self.processing:
            messagebox.showwarning("提示", "正在处理中，请等待完成。")
            return

        if not self.lyrics_text.strip():
            self.log_message("错误：请先输入歌词", "error")
            messagebox.showerror("输入错误", "请先输入歌词（点击「输入歌词...」按钮）")
            return

        audio = self.audio_path.get()
        output = self.output_dir.get()

        if not audio or not os.path.isfile(audio):
            self.log_message("错误：请选择音频文件", "error")
            messagebox.showerror("输入错误", "请选择音频文件 (wav/mp3/flac/ogg/m4a)")
            return

        if not os.path.isdir(output):
            os.makedirs(output, exist_ok=True)

        # 清日志
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        need_annotate = self.auto_furi.get() and not self._has_furigana(self.lyrics_text)

        if need_annotate:
            # 阶段1：自动注音 → 弹窗预览
            self.log_message("检测到原始歌词，正在自动注音...", "info")
            lines = self.lyrics_text.strip().split('\n')
            result = []
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                result.append(add_furigana(line) if not self._has_furigana(line) else line)
            annotated = '\n'.join(result)
            if self.auto_full.get():
                self.log_message("全自动模式：跳过注音确认，直接对齐", "info")
                lyrics_to_use = annotated
            else:
                self.log_message("自动注音完成，请在弹窗中检查并修改", "success")
                edited = self._open_furigana_popup(annotated)
                if edited is None:
                    self.log_message("已取消注音预览，处理中止", "")
                    return
                lyrics_to_use = edited
                self.log_message("使用编辑后的注音结果继续...", "info")
        else:
            lyrics_to_use = self.lyrics_text

        # 写入临时文件（注册到 self._temp_files 由关窗回调清理）
        tmp = tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8')
        tmp.write(lyrics_to_use)
        tmp.close()
        self._temp_files.append(tmp.name)

        self._run_pipeline(audio, tmp.name, output)

    def _run_pipeline(self, audio, lyrics_file, output):
        """在后台线程执行对齐管线"""
        self.processing = True
        self.run_btn.config(state=tk.DISABLED, text="处理中...")
        self.stage_var.set("准备中...")
        self.progress.start(10)

        # 高级参数解析 + 钳位 + 用户可见警告（不再静默吞掉错值）
        def _coerce(var, default, lo, hi, name, cast):
            raw = var.get().strip()
            try:
                v = cast(raw)
            except (ValueError, TypeError):
                self.log_message(f"⚠ 高级参数 {name}='{raw}' 不是合法数字，已用默认值 {default}", "error")
                var.set(str(default))
                return default
            if v < lo or v > hi:
                v_clamped = max(lo, min(hi, v))
                self.log_message(f"⚠ 高级参数 {name}={v} 超出范围 [{lo}, {hi}]，已钳位到 {v_clamped}", "error")
                var.set(str(v_clamped))
                return v_clamped
            return v

        tail = _coerce(self.tail_correct, 3, 0, 3, "尾音修正", int)
        speed = _coerce(self.audio_speed, 1.0, 0.25, 4.0, "音频倍速", float)
        cpl = _coerce(self.chars_per_line, 0, 0, 200, "每行字数", int)

        self.log_message(f"参数: 尾音修正={tail}, 倍速={speed}, 字数限制={cpl}", "info")
        self.log_message("开始对齐处理...\n", "info")

        thread = threading.Thread(
            target=self._worker,
            args=(audio, lyrics_file, output, tail, speed, cpl),
            daemon=True)
        thread.start()

    def _has_furigana(self, text):
        return bool(re.search(r'\{[^}]+\|[^}]+\}', text))

    def _worker(self, audio, lyrics, output, tail, speed, cpl):
        """后台线程：运行对齐管线"""
        # 强制 UTF-8：Windows 控制台默认 GBK 会让日志里的中文/日文崩成乱码
        try:
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass
        os.environ['PYTHONIOENCODING'] = 'utf-8'
        os.environ['PYTHONUTF8'] = '1'
        old_stdout = sys.stdout
        sys.stdout = LogRedirector(self.log)

        try:
            main_module.run_pipeline(
                audio, lyrics, output,
                tail_correct=tail,
                audio_speed=speed,
                output_characters_per_line=cpl,
            )
            self.root.after(0, self._on_complete, True, "")
        except Exception as e:
            import traceback
            self.log_message(traceback.format_exc(), "error")
            self.root.after(0, self._on_complete, False, str(e))
        finally:
            sys.stdout = old_stdout

    def _on_complete(self, success, error_msg):
        self.processing = False
        self.progress.stop()
        self.run_btn.config(state=tk.NORMAL, text="▶  开始处理")

        if success:
            self.log_message(f"\n✓ 完成！文件已输出到: {self.output_dir.get()}", "success")
            self._show_completion_panel()
        else:
            self.log_message(f"\n✗ 处理失败: {error_msg}", "error")
            messagebox.showerror("处理失败", f"对齐过程中出现错误:\n{error_msg}")

    def _cleanup_temp_files(self):
        """清理本次会话产生的临时歌词文件等，不影响 .ass 输出。"""
        for p in self._temp_files:
            try:
                if os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
        self._temp_files = []

    def _on_close(self):
        """关窗回调：若处理中则二次确认，否则保存设置 + 清理临时文件后正常关闭。"""
        if self.processing:
            if not messagebox.askyesno("处理中",
                                        "字幕正在生成。强制关闭可能丢失本次结果。\n确认关闭吗？"):
                return
        try:
            self._save_settings()
        except Exception:
            pass
        self._cleanup_temp_files()
        self.root.destroy()

    # ── 完成面板：替换原 MessageBox，把 QC 报告关键信息直接展示给用户 ──
    def _show_completion_panel(self):
        audio = self.audio_path.get()
        out = self.output_dir.get()
        base = os.path.splitext(os.path.basename(audio))[0]
        ass_path = os.path.join(out, base + '.ass')
        qc_path = os.path.join(out, base + '.qc.txt')

        overall = None
        flagged_text = None
        try:
            if os.path.isfile(qc_path):
                with open(qc_path, encoding='utf-8') as f:
                    head = f.read(2000)
                import re as _re
                m1 = _re.search(r'整曲平均置信度: (\d+\.\d+)', head)
                if m1:
                    overall = float(m1.group(1))
                m2 = _re.search(r'重点复查（最低 \d+ 行，标 !）: (.+)', head)
                if m2:
                    flagged_text = m2.group(1).strip()
        except Exception:
            pass

        popup = self._make_popup("处理完成", 600, 380)

        # 状态条：根据 overall 着色（高=绿、低=橙、极低=红）
        if overall is None or overall >= 0.35:
            bar_bg, icon = GREEN, "✓"
        elif overall >= 0.2:
            bar_bg, icon = "#E67E22", "⚠"
        else:
            bar_bg, icon = RED, "⚠"
        status = tk.Frame(popup, bg=bar_bg, height=64)
        status.pack(fill=tk.X)
        status.pack_propagate(False)
        tk.Label(status, text=f"{icon}  字幕已生成",
                 font=("Microsoft YaHei", 14, "bold"),
                 bg=bar_bg, fg="white").pack(side=tk.LEFT, padx=20, pady=12)
        if overall is not None:
            tk.Label(status, text=f"质量评分 {overall:.3f}",
                     font=("Microsoft YaHei", 11),
                     bg=bar_bg, fg="white").pack(side=tk.RIGHT, padx=20)

        body = tk.Frame(popup, bg=BG_CARD)
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=10)
        tk.Label(body, text="输出位置:", font=FONT_LABEL,
                 bg=BG_CARD, fg=TEXT_DARK).pack(anchor=tk.W)
        tk.Label(body, text=out, font=FONT_PATH,
                 bg=BG_CARD, fg=GREEN, anchor=tk.W).pack(anchor=tk.W, pady=(0, 6))

        if flagged_text:
            tk.Label(body, text="重点复查（可在 Aegisub 中确认这几行）:",
                     font=FONT_LABEL, bg=BG_CARD, fg=TEXT_DARK).pack(anchor=tk.W, pady=(8, 2))
            tk.Label(body, text=flagged_text, font=("Consolas", 10),
                     bg=BG_CARD, fg="#E67E22", anchor=tk.W, wraplength=540, justify=tk.LEFT).pack(anchor=tk.W)
        elif overall is not None:
            tk.Label(body, text="✓ 没有明显可疑的行，可直接使用",
                     font=FONT_LABEL, bg=BG_CARD, fg=GREEN).pack(anchor=tk.W, pady=(8, 2))

        btns = tk.Frame(popup, bg=BG_MAIN)
        btns.pack(fill=tk.X, padx=16, pady=12)

        def _safe_open(p):
            try:
                os.startfile(p)
            except Exception as e:
                messagebox.showerror("打开失败", f"{p}\n{e}")

        def _copy(p):
            self.root.clipboard_clear()
            self.root.clipboard_append(p)

        for text, cmd in [
            ("打开输出文件夹", lambda: _safe_open(out)),
            ("复制 ASS 路径", lambda: _copy(ass_path)),
            ("打开 ASS 文件", lambda: _safe_open(ass_path)),
        ]:
            tk.Button(btns, text=text, font=FONT_LABEL,
                      bg=BLUE, fg="white", activebackground=BLUE_HOVER,
                      activeforeground="white", relief=tk.FLAT, cursor="hand2",
                      padx=10, pady=4, command=cmd).pack(side=tk.LEFT, padx=(0, 8))
        if os.path.isfile(qc_path):
            tk.Button(btns, text="查看 QC 报告", font=FONT_LABEL,
                      bg="#8EBBE0", fg="white", activebackground="#7AABD0",
                      activeforeground="white", relief=tk.FLAT, cursor="hand2",
                      padx=10, pady=4,
                      command=lambda: _safe_open(qc_path)).pack(side=tk.LEFT)
        tk.Button(btns, text="关闭", font=FONT_LABEL,
                  bg="#CCCCCC", fg=TEXT_DARK, relief=tk.FLAT, cursor="hand2",
                  padx=10, pady=4, command=popup.destroy).pack(side=tk.RIGHT)


# ── 入口 ──────────────────────────────────────────────────────
def main():
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    app = KaraokeApp(root)

    app.log_message("autoKara 卡拉OK字幕生成器 已就绪", "info")
    app.log_message("请拖入或选择音频和歌词文件", "")
    app.log_message(f"输出目录: {app.output_dir.get()}\n", "")

    root.mainloop()


if __name__ == '__main__':
    main()
