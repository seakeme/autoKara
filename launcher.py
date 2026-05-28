# -*- coding: utf-8 -*-
"""autoKara 启动器：先做环境健康检查，缺依赖时弹出友好对话框（而不是 pythonw 黑屏闪退），
通过后再加载 GUI。同时把本次会话的 stdout/stderr 旁路写到 %LOCALAPPDATA%\\autoKara\\logs。

这是安装版快捷方式和『重新配置环境』指向的真正入口。直接跑源码也可以：
    python launcher.py
"""
import os
import sys
import time
import traceback
import importlib.util

APP_NAME = "autoKara"
HERE = os.path.dirname(os.path.abspath(__file__))

# ── 1. 旁路日志：每次启动一个时间戳文件，保留最近 20 份 ──
def _setup_log_tee():
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    log_dir = os.path.join(base, APP_NAME, 'logs')
    try:
        os.makedirs(log_dir, exist_ok=True)
    except Exception:
        return None
    # 修剪
    try:
        files = sorted([f for f in os.listdir(log_dir) if f.endswith('.log')])
        for old in files[:-20]:
            try:
                os.remove(os.path.join(log_dir, old))
            except Exception:
                pass
    except Exception:
        pass
    log_path = os.path.join(log_dir, time.strftime("%Y%m%d-%H%M%S") + '.log')

    class _Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, s):
            for st in self.streams:
                try:
                    st.write(s)
                except Exception:
                    pass
        def flush(self):
            for st in self.streams:
                try:
                    st.flush()
                except Exception:
                    pass

    try:
        f = open(log_path, 'w', encoding='utf-8', buffering=1)
        # pythonw 下 sys.stdout/stderr 可能是 None
        sys.stdout = _Tee(sys.stdout, f) if sys.stdout else f
        sys.stderr = _Tee(sys.stderr, f) if sys.stderr else f
    except Exception:
        return None
    return log_path

LOG_PATH = _setup_log_tee()

# 必装依赖：模块名 → 给用户看的人话包名（pip install 名）
REQUIRED = [
    ("torch",      "torch"),
    ("torchaudio", "torchaudio"),
    ("librosa",    "librosa"),
    ("numpy",      "numpy"),
    ("soundfile",  "soundfile"),
    ("sudachipy",  "SudachiPy"),
    ("janome",     "Janome"),
    ("pykakasi",   "pykakasi"),
    ("pyphen",     "pyphen"),
    ("nltk",       "nltk"),
    ("demucs",     "demucs"),
    ("PIL",        "Pillow"),
]
# 至少需要一个的"组"：SudachiPy 的词典 core / full 任一即可（furigana.py 会自动回退）
REQUIRED_GROUPS = [
    (["sudachidict_full", "sudachidict_core"], "SudachiDict-core 或 SudachiDict-full"),
]

def _has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False

def _missing_packages():
    miss = []
    for mod, pretty in REQUIRED:
        if not _has_module(mod):
            miss.append(pretty)
    for mods, pretty in REQUIRED_GROUPS:
        if not any(_has_module(m) for m in mods):
            miss.append(pretty)
    return miss

def _show_env_error(missing):
    """Tk 弹窗告诉用户缺什么 + 给一键修复按钮。"""
    try:
        import tkinter as tk
    except Exception:
        # tkinter 都没有 —— 极少见，只能 stderr
        sys.stderr.write("autoKara: tkinter 不可用，且缺以下包：\n  " +
                         "\n  ".join(missing) + "\n请重新运行 setup_env.bat\n")
        return

    root = tk.Tk()
    root.title(f"{APP_NAME} — 环境未就绪")
    try:
        root.geometry("540x360")
    except Exception:
        pass
    root.configure(bg="#FFF3F0")

    tk.Label(root, text="⚠ 缺少运行依赖", font=("Microsoft YaHei", 14, "bold"),
             bg="#FFF3F0", fg="#C0392B").pack(padx=20, pady=(18, 6))
    tk.Label(root,
             text="以下包未安装或加载失败，autoKara 无法启动：",
             font=("Microsoft YaHei", 10), bg="#FFF3F0", fg="#333").pack(anchor=tk.W, padx=20)
    box = tk.Text(root, height=8, font=("Consolas", 10), bg="#FFFFFF",
                  relief=tk.SOLID, borderwidth=1)
    box.pack(fill=tk.BOTH, expand=True, padx=20, pady=8)
    box.insert(tk.END, "\n".join("  • " + p for p in missing))
    box.config(state=tk.DISABLED)

    setup_path = os.path.join(HERE, 'installer', 'setup_env.bat')
    if not os.path.isfile(setup_path):
        setup_path = os.path.join(HERE, 'setup_env.bat')

    def _run_setup():
        try:
            os.startfile(setup_path)
        except Exception as e:
            tk.messagebox.showerror("无法启动 setup_env.bat", str(e))
        root.destroy()

    btns = tk.Frame(root, bg="#FFF3F0")
    btns.pack(fill=tk.X, padx=20, pady=12)
    if os.path.isfile(setup_path):
        tk.Button(btns, text="一键重新配置环境", font=("Microsoft YaHei", 10, "bold"),
                  bg="#4A90D9", fg="white", activebackground="#357ABD",
                  activeforeground="white", relief=tk.FLAT, cursor="hand2",
                  padx=14, pady=6, command=_run_setup).pack(side=tk.LEFT)
    tk.Button(btns, text="关闭", font=("Microsoft YaHei", 10),
              bg="#CCCCCC", fg="#333", relief=tk.FLAT, cursor="hand2",
              padx=14, pady=6, command=root.destroy).pack(side=tk.RIGHT)
    if LOG_PATH:
        tk.Label(root, text=f"日志: {LOG_PATH}",
                 font=("Consolas", 8), bg="#FFF3F0", fg="#666").pack(side=tk.BOTTOM, pady=4)
    root.mainloop()

def _show_crash_dialog(exc_text):
    """GUI 启动后未捕获异常的兜底弹窗（替代黑屏闪退）。"""
    try:
        import tkinter as tk
        from tkinter import scrolledtext
    except Exception:
        sys.stderr.write("autoKara 崩溃:\n" + exc_text + "\n")
        return
    root = tk.Tk()
    root.title(f"{APP_NAME} — 程序错误")
    try: root.geometry("700x460")
    except Exception: pass
    root.configure(bg="#FFF3F0")
    tk.Label(root, text="autoKara 遇到错误并已停止运行",
             font=("Microsoft YaHei", 14, "bold"), bg="#FFF3F0", fg="#C0392B").pack(padx=20, pady=(18, 6))
    if LOG_PATH:
        tk.Label(root, text=f"完整日志: {LOG_PATH}",
                 font=("Consolas", 9), bg="#FFF3F0", fg="#333").pack(anchor=tk.W, padx=20)
    txt = scrolledtext.ScrolledText(root, font=("Consolas", 9), wrap=tk.WORD)
    txt.pack(fill=tk.BOTH, expand=True, padx=20, pady=8)
    txt.insert(tk.END, exc_text)
    txt.config(state=tk.DISABLED)
    tk.Button(root, text="关闭", font=("Microsoft YaHei", 10),
              bg="#CCCCCC", fg="#333", relief=tk.FLAT, cursor="hand2",
              padx=14, pady=6, command=root.destroy).pack(pady=10)
    root.mainloop()

def main():
    # 强制 UTF-8 stdio，避免 Windows GBK 控制台在 print 中文时崩
    try:
        if sys.stdout: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if sys.stderr: sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    os.environ.setdefault('PYTHONUTF8', '1')

    print(f"autoKara launcher start  cwd={os.getcwd()}  log={LOG_PATH}")
    missing = _missing_packages()
    if missing:
        print("missing packages:", missing)
        _show_env_error(missing)
        sys.exit(1)

    # 主入口
    sys.path.insert(0, HERE)
    try:
        import gui
        gui.main()
    except Exception:
        exc = traceback.format_exc()
        print(exc)
        _show_crash_dialog(exc)
        sys.exit(1)

if __name__ == '__main__':
    main()
