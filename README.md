# autoKara — 基于 MMS-FA 的卡拉OK字幕自动注音 + 打轴工具

主要参考了 [FA-Kara](https://github.com/moriwx/FA-Kara)，加上了自动注音、人声分离、分句约束对齐，ass 模板里也预排版好了 K1/K2 双层卡拉OK 效果。gui 顶部的 knm.png 出自 leaf 社的《To Heart2》，图是我自己设计的。

从 **人声音频 + 日文歌词** 自动生成带注音和时间轴的卡拉OK字幕（ASS）。

核心思路：把歌词转成音素序列，用 Meta 的 [MMS-FA](https://arxiv.org/abs/2305.13516) 强制对齐到音频，得到每个音节的时间戳。MMS-FA 在有技巧的尾音 / 拖长音上仍不完美，所以输出里多附一份 `.qc.txt` 置信度报告——告诉你哪几句不太稳，便于人工复查。

## 一键安装（Windows）

最省事的路：去 [Releases](https://github.com/seakeme/autoKara/releases) 下载 `autoKara-setup.exe`，双击 → Next 装完就行。无需先装 Python。

安装临近结尾会弹出一个黑色 cmd 窗口，在配私有 Python、装 torch、下模型，**这一步大概 10–40 分钟，请保持窗口开着别关**（关了会留个半装状态，下次启动 autoKara 时会自动重新接着配）。看到 `环境配置完成！` 字样就好了。

国内网络拉得慢的话，先开 PowerShell 走镜像：

```powershell
$env:AUTOKARA_MIRROR = "cn"
& "$env:LOCALAPPDATA\Programs\autoKara\setup_env.bat"
```

会切清华 + 阿里云 PyPI 镜像、HF-mirror。

装坏了不用重装：开始菜单 → autoKara → **重新配置环境**，已下完的会自动跳过、只续传剩下的。

## 怎么用

### GUI

桌面双击 autoKara。

1. 拖一个音频到顶部灰色框（或点 浏览）
2. 点 **输入歌词**，把日文歌词粘进去（每行一句），点确认
3. 默认输出到桌面，可改
4. 点 **▶ 开始处理**
5. 等几分钟，弹出"处理完成"面板：质量分、可疑行号、打开输出文件夹按钮

歌词可以是纯日文（会自动注音），也可以是已经标好振假名的：

```
{私|わたし}は{明日|あした}{行|い}く
```

输出两个文件到你选的目录：

- `*.ass` — 卡拉OK字幕成品，用 [Aegisub](https://aegisub.org/) 打开就是 K1/K2 双层模板
- `*.qc.txt` — 置信度报告。顶部列出全曲最低 ~10% 的行号，进 Aegisub 优先听这几句

### CLI

```bash
python main.py -i input/ -o output/
```

把音频和同名 `.txt` 歌词放 `input/`，输出去 `output/`。

| 参数 | 默认 | 说明 |
|---|---|---|
| `-i` / `-o` | `./input` / `./output` | 输入 / 输出目录 |
| `--align-mode` | `phrase` | `phrase` 分句约束（默认）；`global` 整曲对齐（备选） |
| `--raw` |  | 忽略已有 `{漢字\|よみ}` 标记重新自动注音 |
| `-v` | `1.0` | 对齐用的音频倍速，快歌设 0.5 降低难度 |
| `-t` | `3` | 尾音修正策略 1/2/3 |
| `-tl` / `-tp` / `-tr` | `0.8 / 10 / 0.1` | VAD 窗口 / 阈值百分位 / 阈值比例 |
| `-cl` | `0` | 输出每行最大字数（0 不限）|
| `-x` / `-n` | `0 / 1` | 是否拆分促音 / 拨音 |
| `--doctor` |  | 打印环境诊断信息（提 issue 时贴上最快定位）|

## 修正读音

自动注音偶尔会读错（比如把 "言う" 读成 `ゆう`，你想要 `いう`）。打开 `readings.txt`，加一行：

```
言う=いう
入り=いり
```

下次开始所有歌都按这个表来。重装不会覆盖这个文件。

## 常见问题

**注音错了几个字？**  
- 临时改：把"全自动模式"勾去掉，处理时会弹出预览给你改。  
- 一劳永逸：往 `readings.txt` 加一行。

**某几句时间飘得很远？**  
先看 `.qc.txt` 顶部"重点复查"找到行号。试不同的 `-t`，或者快歌 `-v 0.5`。

**有 NVIDIA 显卡但跑很慢？**  
cmd 跑 `nvidia-smi` 看驱动版本。RTX 50 系（Blackwell）要 ≥ 570；老卡 ≥ 525。低于这俩只能跑 CPU。换驱动后从开始菜单跑 **重新配置环境** 重装 torch。

**装到一半把那个黑窗口关了？**  
没事，下次启动 autoKara 时会自动检测并继续；也可以手动开始菜单 → **重新配置环境**。

**一首歌要处理多久？**  
4 分钟左右的歌：GPU 几十秒，CPU 2–10 分钟。

## 文件位置

| | |
|---|---|
| 应用目录 | `%LOCALAPPDATA%\Programs\autoKara\` |
| 自定义读音 | `应用目录\readings.txt` |
| 用户偏好 | `%LOCALAPPDATA%\autoKara\settings.json` |
| 每次运行日志 | `%LOCALAPPDATA%\autoKara\logs\` |
| MMS-FA 模型缓存 | `~\.cache\torch\hub\checkpoints\model.pt`（约 1.26 GB）|
| nltk 词典 | `%APPDATA%\nltk_data\` |

## 从源码跑（不走安装器）

```bash
# 按显卡选 torch wheel 源
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128   # RTX 50 系
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121   # 老 NVIDIA
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu     # 无显卡

pip install -r requirements.txt

python launcher.py   # GUI（推荐，启动时会做依赖健康检查）
# 或
python main.py       # CLI
```

## 目录结构

```
autoKara/
├── launcher.py        # GUI 启动器（依赖检查 + 日志旁路 + 崩溃兜底）
├── main.py            # 核心管线 + CLI 入口（含 --doctor）
├── gui.py             # Tkinter 图形界面
├── furigana.py        # 自动注音（读 readings.txt）
├── separate.py        # 人声分离
├── readings.txt       # 自定义读音覆盖表
├── requirements.txt
├── installer/
│   ├── installer.iss      # Inno Setup 脚本
│   ├── setup_env.bat      # 目标机分阶段配置环境
│   ├── build_installer.bat # 开发机用：编译出 setup.exe
│   └── autoKara.bat       # 装机后的入口（缺环境时自动补装）
├── input/             # 默认输入目录
└── output/            # 默认输出目录
```

## 技术栈

- **强制对齐**：[MMS-FA](https://arxiv.org/abs/2305.13516)（torchaudio）
- **人声分离**：[Demucs](https://github.com/facebookresearch/demucs)（htdemucs_ft）
- **日语分词/注音**：SudachiPy、Janome、pykakasi
- **音频处理**：librosa

## 参考资料

- [yohane](https://github.com/Japan7/yohane) — NicoKara 自动打轴
- [Forced-Alignment-For-NicoKara](https://github.com/oHEILIo/Forced-Alignment-For-NicoKara)
- [MMS-FA 论文](https://arxiv.org/abs/2305.13516) — Scaling Speech Technology to 1,000+ Languages
