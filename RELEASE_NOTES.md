## autoKara v1.0

带安装包的首个版本，双击 setup.exe 即可安装，不需要先装 Python。

### 安装

下载附件 `autoKara-setup.exe`，双击，一路 Next。最后会弹出一个黑色窗口配置环境（私有 Python、torch、模型，约 10–40 分钟），别关它，看到“环境配置完成”即可。中途关掉也没关系，下次启动会自动接着装。

国内网络慢可用镜像，命令见 README。

### conda 里看不到 autoKara？

正常的。安装包用的是一份独立 Python（装在 `安装目录\python\`），和系统的 Python / conda 互不干扰，所以 conda 里看不到，依赖都在 `安装目录\python\Lib\site-packages\`。直接用快捷方式启动即可，不用管 conda。

### 主要内容

- 默认采用分句对齐（按行切歌词、按静音切音频，逐句对齐），减少整曲时间轴漂移；同时跑整曲对齐做对照取较优。
- 自动注音，支持 `readings.txt` 自定义读音覆盖（一行一条，如 `言う=いう`，重装保留）。
- 每次输出附一份 `*.qc.txt`，列出置信度最低的约 10% 行，方便在 Aegisub 里重点检查。
- GUI 全自动模式、处理完成面板、参数校验、设置记忆。
- 装机按 NVIDIA 驱动版本选 torch（≥570 用 cu128，≥525 用 cu121，否则 CPU），分阶段可断点续装。
- `python main.py --doctor` 输出环境诊断。

### 修复

- cmudict 改为内置，不再从 GitHub 下载（之前国内会卡住或失败）。
- 人声分离的音频读写改用 soundfile/librosa，不再需要 torchcodec（之前报 `TorchCodec is required`）。
- 安装脚本改为纯 ASCII，修复中文 bat 在部分机器上闪退的问题。

### 已知问题

- MMS-FA 在拖长音、花腔等演唱技巧上对齐精度有限，qc.txt 标记的低分行建议人工核对。
- 模型缓存约 1.6 GB，存在 `~\.cache\torch\hub\`。
