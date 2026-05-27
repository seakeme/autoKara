# autoKara — 基于MMS-FA的卡拉OK字幕自动注音自动打轴工具

主要参考了 [FA-Kara](https://github.com/moriwx/FA-Kara)，加上自动注音和人声分离，同时ass文件里还有预先留好的效果和字幕自动分层交错出现。gui顶部的knm.png出自leaf社的视觉小说《to heart2》，图是我自己设计的。另外MMS-FA生成的轴还是有很多缺陷的，譬如有技巧的尾音就很难处理，还是需要人工队的努力。

从**人声音频 + 日文歌词**自动生成带注音和时间轴的卡拉OK字幕文件（ASS） 格式。

核心思路：将歌词文本转换为音素序列，再通过 Meta 的 [MMS-FA](https://arxiv.org/abs/2305.13516) 强制对齐模型将音素与音频对齐，得到每个字/音节的时间戳。


## 功能概览

- **自动注音** — 纯日文汉字/片假名歌词自动添加 `{漢字|よみ}` 注音标记（SudachiPy）
- **人声分离** — 非 WAV 格式音频自动调用 Demucs 分离人声
- **强制对齐** — 基于 MMS-FA 的字级时间轴生成
- **尾音修正** — 多种策略修正句尾拖长音的时间边界
- **双语混排** — 支持日语中混入英文单词和数字的发音处理
- **双界面** — 命令行（CLI）与图形界面（GUI）均可使用

## 快速开始

### 环境配置

1. 安装 [Python](https://www.python.org/)（推荐 3.11+）
2. 根据你的 CUDA 版本安装 PyTorch：
   ```bash
   # CUDA 12.x
   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
   ```
3. 安装其余依赖：
   ```bash
   pip install -r requirements.txt
   ```

### GUI 方式

```bash
python gui.py
```

拖入音频文件、粘贴歌词，点击"开始处理"即可。
注意注音不一定完全准确，可以自行确认修改。

### CLI 方式

在 `input/` 目录下放入：
- 一个音频文件（`.wav` / `.mp3` / `.flac` / `.ogg` / `.m4a`）
- 一个歌词文件（`.txt`）

```bash
python main.py
```

歌词可以是纯日文（会自动注音），也可以是已标注振假名的格式：
```
{私|わたし}は{明日|あした}{行|い}く
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-i` | `./input` | 输入文件夹 |
| `-o` | `./output` | 输出文件夹 |
| `--raw` | 关闭 | 强制重新自动注音，忽略已有的 `{漢字\|よみ}` 标记 |
| `-v` | `1.0` | 对齐时使用的音频倍速。语速偏快时可设为 0.5 降低推理难度 |
| `-t` | `3` | 尾音修正策略（1/2/3，推荐 3） |
| `-tl` | `0.8` | 静音检测窗口时长（秒），用于乐句切割 |
| `-tp` | `10` | 静音检测阈值百分位数 |
| `-tr` | `0.1` | 静音检测阈值比例 |
| `-cl` | `0` | 输出每行最大字数（0=不限制） |
| `-x` | `0` | 是否拆分促音 |
| `-n` | `1` | 是否拆分拨音 |


## 输出文件

处理后会在输出目录生成 `{音频文件名}.ass`，这是一个带卡拉OK模板的 ASS 字幕文件，可直接在 Aegisub 中打开编辑，配合模板实现逐字高亮效果。

## 目录结构

```
autoKara/
├── main.py          # 核心管线：文本解析 → 强制对齐 → 字幕生成
├── gui.py           # Tkinter 图形界面
├── furigana.py      # 自动注音模块（SudachiPy）
├── separate.py      # 人声分离模块（Demucs）
├── requirements.txt
├── input/           # 默认输入目录
└── output/          # 默认输出目录
```

## 技术栈

- **k轴对齐**: [MMS-FA](https://arxiv.org/abs/2305.13516) (torchaudio)
- **人声分离**: [Demucs](https://github.com/facebookresearch/demucs) (htdemucs_ft)
- **日语分词/注音**: SudachiPy、Janome、pykakasi
- **音频处理**: librosa

## 参考资料


- [yohane](https://github.com/Japan7/yohane) — NicoKara 自动打轴工具
- [Forced-Alignment-For-NicoKara](https://github.com/oHEILIo/Forced-Alignment-For-NicoKara)
- [MMS-FA 论文](https://arxiv.org/abs/2305.13516) — Scaling Speech Technology to 1,000+ Languages
