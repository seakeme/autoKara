# autoKara — 基于强制对齐的卡拉OK字幕自动生成

主要参考了 [FA-Kara](https://github.com/moriwx/FA-Kara)，加上了自动注音、人声分离、分句约束对齐，ass 模板里也预排版好了 K1/K2 双层卡拉OK 效果。gui 顶部的 knm.png 出自 leaf 社的《To Heart2》，图是我自己设计的。MMS-FA 在有技巧的尾音 / 拖长音上仍不完美，所以输出里多附一份 '.qc.txt' 置信度报告——告诉你哪几句不太稳，便于人工复查。
## 流程概览

输入人声音频与日文歌词，经以下三个阶段生成逐字时间轴：

1. **文本处理** — 通过 SudachiPy、Janome、pykakasi 将歌词切分为音拍级发音序列。英文片段由 CMU Pronouncing Dictionary 与 Pyphen 处理音节划分。
2. **音频预处理** — 非人声轨道经 Demucs（htdemucs_ft）提取人声。静音检测标记非人声区间，供后续对齐约束。
3. **强制对齐** — 发音序列与音频通过 Meta 的 [MMS-FA](https://arxiv.org/abs/2305.13516) 模型对齐，得到 token 级时间戳，再映射回原文表面字形。

对齐模型不限语种，但文本处理管线针对日语优化。

## 安装

Windows 用户可从 [Releases](https://github.com/seakeme/autoKara/releases) 下载预编译安装包。
下载 `autoKara-setup.exe`，双击 → Next。无需先装 Python。

安装临近结尾会弹出 cmd 窗口，装 torch、下模型，**这一步大概 10–40 分钟，请保持窗口开着别关**（关了会留个半装状态，下次启动 autoKara 时会自动重新接着配）。

国内网络拉得慢的话，先开 PowerShell 走镜像：

```powershell
$env:AUTOKARA_MIRROR = "cn"
& "$env:LOCALAPPDATA\Programs\autoKara\setup_env.bat"
```

会切清华 + 阿里云 PyPI 镜像、HF-mirror。

从源码运行：

```bash
# PyTorch（按 CUDA 版本选择）
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128   # RTX 50 系
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121   # 旧款 NVIDIA
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu     # CPU

pip install -r requirements.txt
```

## 使用

### GUI（推荐，启动时会做依赖健康检查）

```bash
python launcher.py
```

选择音频文件，粘贴或导入歌词（每行一句）。对齐前可预览并修改自动注音结果。

### CLI

```bash
python main.py -i input/ -o output/
```

在输入目录放置一个音频文件（`.wav` / `.mp3` / `.flac` / `.ogg` / `.m4a`）和一个 `.txt` 歌词文件。

歌词可为纯日文（自动注音），或已标注振假名：

```
{私|わたし}は{明日|あした}{行|い}く
```

### 参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `-i` / `-o` | `./input` / `./output` | 输入 / 输出目录 |
| `--align-mode` | `phrase` | `phrase`：按行对齐，以静音间隙为约束；`global`：整曲对齐 |
| `--raw` | 关闭 | 忽略已有 `{漢字\|よみ}` 标记，强制重新注音 |
| `-v` | `1.0` | 对齐时的音频倍速（语速偏快时可降低） |
| `-t` | `3` | 尾音修正策略：1（韵尾圆整）、2（清辅音尾）、3（静音间隙截断） |
| `-tl` | `0.8` | 静音检测窗口（秒） |
| `-tp` | `10` | 静音阈值百分位数 |
| `-tr` | `0.1` | 静音阈值比例 |
| `-cl` | `0` | 输出每行最大字符数（0 = 不限） |
| `-x` / `-n` | `0` / `1` | 促音（っ）/ 拨音（ん）是否独立成 token |
| `--doctor` | 关闭 | 输出环境诊断信息 |

## 输出

- `{文件名}.ass` — 含 K1/K2 双层卡拉OK模板的 ASS 字幕，可用于 Aegisub
- `{文件名}.qc.txt` — 逐行对齐置信度报告，置信度最低的 ~10% 行被标记为需人工复查

## 自定义读音

在项目根目录放置 `readings.txt`，逐行指定读音覆盖：

```
言う=いう
入り=いり
```

左侧为表面字形，右侧为平假名读音，优先级高于 SudachiPy 输出。


## 文件位置

| | |
|---|---|
| 应用目录 | `%LOCALAPPDATA%\Programs\autoKara\` |
| 自定义读音 | `应用目录\readings.txt` |
| 用户偏好 | `%LOCALAPPDATA%\autoKara\settings.json` |
| 每次运行日志 | `%LOCALAPPDATA%\autoKara\logs\` |
| MMS-FA 模型缓存 | `~\.cache\torch\hub\checkpoints\model.pt`（约 1.26 GB）|
| nltk 词典 | `%APPDATA%\nltk_data\` |


## 目录结构

```
autoKara/
├── launcher.py           # GUI 入口，含依赖健康检查
├── main.py               # 核心管线与 CLI
├── gui.py                # Tkinter 图形界面
├── furigana.py           # 自动振假名注音（SudachiPy）
├── separate.py           # 人声分离（Demucs htdemucs_ft）
├── readings.txt          # 自定义读音覆盖表
├── requirements.txt
├── installer/            # Inno Setup 打包脚本
├── input/                # 默认输入目录
└── output/               # 默认输出目录
```

## 依赖

- **强制对齐** — [MMS-FA](https://arxiv.org/abs/2305.13516)（torchaudio）
- **人声分离** — [Demucs](https://github.com/facebookresearch/demucs)（htdemucs_ft）
- **日语 NLP** — SudachiPy、Janome、pykakasi
- **英语发音** — NLTK（CMU Pronouncing Dictionary）、Pyphen
- **音频分析** — librosa

## 已知局限

MMS-FA 在常规语音上对齐较可靠，但在颤音、假声转换、花腔等演唱技巧上精度下降。QC 文件标记了低置信度区域，建议结合实际听觉判断在 Aegisub 中微调。尾音修正策略可在一定程度上缓解拖长音被归入静音段的问题，但效果因演唱风格和录音质量而异。

## 参考资料

- [FA-Kara](https://github.com/moriwx/FA-Kara) — 主要参考实现
- [yohane](https://github.com/Japan7/yohane) — NicoKara 对齐工具
- [Forced-Alignment-For-NicoKara](https://github.com/oHEILIo/Forced-Alignment-For-NicoKara)
- Pratap, V., et al. "[Scaling Speech Technology to 1,000+ Languages](https://arxiv.org/abs/2305.13516)." arXiv, 2023.
