# autoKara 使用指南

> 写给完全不懂 Python 的卡拉 OK 制作者。3 分钟看完，半小时出第一份带轴字幕。

---

## 一、autoKara 是什么 / 适合谁用

**autoKara** 是一款 Windows 下的 **日文卡拉 OK 字幕自动打轴 + 自动注音工具**。给它两样东西，它产出可以直接用的 ASS 字幕：

```
输入                                       输出
─────────────────────────────────          ──────────────────────────────────
音频文件 (mp3/wav/flac/ogg/m4a)      →     {歌曲名}.ass     ← 拖进 Aegisub 直接用
日文歌词 (纯汉字或已带振假名)               {歌曲名}.qc.txt  ← 哪几行需要复查
```

内部流程：自动加振假名（汉字→平假名） → 自动分离人声（非 .wav 输入） → MMS-FA 模型把每个假名贴到音频时间轴上（**分句约束对齐**，避免整曲漂移） → 输出带 K1/K2 双层卡拉 OK 模板的 ASS 字幕 + 置信度报告。

**适合谁**：B 站 / NicoNico 字幕组、字幕组打轴位、个人卡拉 OK 爱好者。**完全不需要懂 Python 也能用**——下面"一键装机"那条路。

---

## 二、安装：用 `autoKara-setup.exe` 一键装机（推荐）

适用绝大多数用户。**全程双击下一步，无需自己装 Python。**

1. 从发布页下载 **`autoKara-setup.exe`**。
2. 双击运行，一路 **Next → Install**（默认装到 `C:\Users\<你的用户名>\AppData\Local\Programs\autoKara`，**无需管理员权限**）。
3. 安装最后一步会自动弹出一个**黑色控制台窗口**配置运行环境：
   - 私有 Python 3.11（独立环境，**不污染你系统的 Python**）
   - PyTorch + torchaudio（**自动按显卡选**：RTX 50/40/30 系 → cu128，老 RTX 20 系等 → cu121，没 NVIDIA 卡 → CPU 版）
   - 其余依赖 + MMS-FA 对齐模型 + Demucs 人声分离模型 + nltk 词典
   - **总下载量约 3-5 GB，耗时 10-40 分钟**（取决于网速）。**请勿关闭这个黑窗口！**
4. 看到 `环境配置完成！` 字样，桌面出现 **autoKara** 快捷方式，开始菜单出现 **autoKara** 文件夹，即装好。

### 中国大陆网络慢/挂？启用国内镜像
关闭那个黑窗口（如还没装好），开始菜单找到 **autoKara → 重新配置环境**，**右键 → 属性 → 在"目标"前面加一段**：
```
cmd /c "set AUTOKARA_MIRROR=cn && setup_env.bat"
```
或者更简单：开个 PowerShell：
```powershell
cd "$env:LOCALAPPDATA\Programs\autoKara"
$env:AUTOKARA_MIRROR="cn"
.\setup_env.bat
```
这会切到 **清华 TUNA + 阿里云 PyPI 镜像 + HF-mirror**，速度通常会快很多。

### 安装失败 / 中途断网了怎么办？
**不需要重装**。脚本是**分阶段、可断点续装**的：
- 开始菜单 → **autoKara → 重新配置环境**，双击它，已下载好的步骤会自动跳过，只续传未完成的部分。
- 想单步重试某一阶段：删掉 `安装目录\.state\` 里对应的 `python.ok` / `pip.ok` / `torch.ok` / `deps.ok` / `models.ok` 然后再跑。

### 手动安装（开发者路线）
见文末"附"。

---

## 三、第一次跑（GUI 路线）

1. **启动**：双击桌面 **autoKara** 图标。
   - 启动器先做**环境健康检查**：缺什么依赖会直接弹友好对话框告诉你，并提供"一键重新配置环境"按钮 —— 不会像以前一样黑屏闪退。
   - 一切正常约 5-10 秒进入主窗口。
2. **导入音频**：把音频文件**直接拖进**窗口顶部"音频文件"灰底区域（也可点 **浏览...** 选）。看到下方变成绿色 `✓ 已选择: xxx.mp3` 即成功。
3. **输入歌词**：点 **输入歌词...**，弹窗里粘贴日文歌词（**每行一句**），点 **确认**。
   - 已有 `.txt`？点 **从文件导入...** 后在弹窗确认。
   - 歌词可以是纯日文（含汉字），autoKara 会自动注音；也可以是已标好的格式：`{私|わたし}は{明日|あした}{行|い}く`。
4. **选输出目录**：默认是桌面。要换地方点 **浏览...**。
5. **核对选项**（保持默认即可）：
   - **自动注音** — 默认勾，纯日文歌词自动加 `{漢字|よみ}`。
   - **全自动模式** — 默认勾，**跳过注音确认弹窗**全程无人工。若想亲眼看一眼注音结果并校正，**把这一项的勾去掉**。
6. **(可选) 高级参数** — 点 `▶ 高级参数` 展开：
   - **尾音修正** — 1/2/3，默认 **3**（最准）。
   - **音频倍速** — 默认 1.0。语速极快的 rap / ANISON 可设 **0.5**（让模型听得更清楚）。允许范围 0.25-4.0。
   - **每行字数限制** — 默认 0（不限）。需避免单行过长可设 20 左右。
   - 注：输入非法值或超界会被自动钳位并在日志里提醒，**不会再静默吞掉你的输入**。
7. **点 `▶ 开始处理`**。下方实时显示：
   - **当前阶段**：人声分离中 → 打轴对齐中 → 完成
   - **处理日志**：实时输出
   - **耗时**：3-5 分钟的歌大约 **30 秒~1 分钟**（GPU）或 **2-10 分钟**（CPU）。
8. **完成弹窗**：处理结束自动弹出"处理完成"面板，包含：
   - 顶部彩色状态条：**整曲质量评分**（≥0.35 绿、0.2-0.35 橙、<0.2 红）
   - **重点复查行号**：建议在 Aegisub 中确认这几行
   - 按钮：**打开输出文件夹** / **复制 ASS 路径** / **打开 ASS** / **查看 QC 报告**

---

## 四、输出文件怎么用

输出目录里会有两个文件：

### 1. `{歌曲名}.ass` — 卡拉 OK 字幕成品

用 **Aegisub** 打开（[官网](https://aegisub.org/)）。文件已包含：
- **K1 / K2 双层交错样式**：奇偶行分别走 K1 / K2 模板，自带逐字高亮与渐变效果。
- 振假名（furigana）已挂在每个汉字头上。
- 你在 Aegisub 中可直接调时间、改文字、换字体，再导出压制。

### 2. `{歌曲名}.qc.txt` — 对齐质量报告（**强烈建议看一眼**）

记事本打开即可。结构：

```
autoKara — 对齐置信度报告
============================================================
整曲平均置信度: 0.842    歌词行数: 42
重点复查（最低 4 行，标 !）: L07, L18, L23, L31

L01 0.901   君がくれた夏の日
L02 0.876   忘れられない約束
...
L07 0.412 ! 走り出した未来へ
         低分: hashi=0.103  ri=0.187  da=0.221
```

- **顶部"重点复查"**：列出全曲置信度最低的约 10% 行号。**优先去 Aegisub 里听这几行**。
- **每行分数**：≥ 0.7 一般稳；< 0.2 + 行末 `!` 标记的建议确认。
- **低分 token**：告诉你这一行里哪几个假名最可疑——通常是注音错了或尾音被模型拖飘了。

---

## 五、进阶用法

### 5.1 命令行（CLI）— 批量 / 脚本化

PowerShell 进入项目目录：

```powershell
python main.py -i input/ -o output/ --align-mode phrase
```

把音频和 `任意名.txt` 歌词丢到 `input/`（**各一个**），结果出在 `output/`。常用参数：

| 参数 | 作用 |
|---|---|
| `-i <dir>` / `-o <dir>` | 输入 / 输出目录 |
| `--align-mode phrase` | **默认**，分句约束对齐；实测置信度 +125%，并跟整曲对齐比对自动择优 |
| `--align-mode global` | 整曲对齐（旧路径，作回退备选） |
| `-v 0.5` | 音频降到 0.5×，对付快歌 |
| `-t 3` | 尾音修正策略，默认 3 |
| `-cl 20` | 每行最多 20 字 |
| `--raw` | 忽略已有注音标记，强制重新注音 |
| `--doctor` | **打印环境诊断报告**（提 issue 时贴上） |

### 5.2 `readings.txt` — 一次纠音，永久生效

自动注音偶尔会读错（比如把"言う"读成 `ゆう`，你想要 `いう`）。不必每次手动改：

1. 打开 **`%LOCALAPPDATA%\Programs\autoKara\readings.txt`**（资源管理器地址栏粘贴可直达）。手动安装版：项目根 `readings.txt`。
2. 加一行 `表层=读音（平假名）`：
   ```
   言う=いう
   入り=いり
   私=わたし
   # 用 # 开头是注释
   ```
3. 保存。**下次跑任何歌**都按你这表来处理。**重装 autoKara 不会覆盖**此文件。

### 5.3 环境诊断 — `--doctor` / 开始菜单"环境诊断"

```powershell
python main.py --doctor
```
输出 Python / torch / CUDA / GPU / 模型 / 词典等版本，一行复制贴给作者排查最快。

---

## 六、常见问题（FAQ）

**Q1：注音错了一些字？**
- 临时一次性改：去掉"全自动模式"，处理时会弹出注音预览，直接在窗口里改→确认。
- 永久改：把那个词加进 `readings.txt`（见 5.2），以后所有歌都不会再错。

**Q2：某几句拖音飘得很离谱？**
- 先看 `.qc.txt` 顶部"重点复查"定位是哪几行。
- 试试把"尾音修正"从 3 切到 1 或 2 重跑。
- 快歌可把"音频倍速"设 **0.5**。
- 默认 phrase 模式已能消除大部分"前几句飘 20 秒"的情况；若仍有飘，CLI 加 `--align-mode global` 对比一下。

**Q3：装了 NVIDIA 显卡但好像没用上 GPU（跑得很慢）？**
- 命令提示符运行 `nvidia-smi` 看驱动是否正常 + 看 `CUDA Version` 那一格。
- RTX 50 系 (Blackwell) 需要驱动版本 ≥ 570（cu128）；老卡需要 ≥ 525（cu121）。低于这俩就跑 CPU。
- 已装完后想换 GPU 版：删 `安装目录\.state\torch.ok`，再开始菜单"重新配置环境"。

**Q4：歌曲文件名 / 路径里有中文，会不会出问题？**
- 一般不会。但若处理失败且日志里见编码错误，把文件改成纯英数名再试。

**Q5：要批量处理多首歌？**
- GUI 单首处理。批量请走 CLI：每首歌一个子文件夹（各放 `xxx.mp3` 和 `xxx.txt`），用 PowerShell 循环：
  ```powershell
  Get-ChildItem songs/ -Directory | ForEach-Object {
      python main.py -i $_.FullName -o output/$($_.Name) --align-mode phrase
  }
  ```

**Q6：装坏了 / 想重置？**
- 删 `%LOCALAPPDATA%\Programs\autoKara\.env_ready` + `.state\` → 开始菜单"重新配置环境" → 等它跑完。
- 或控制面板卸载 autoKara 后重装（**`readings.txt` 会保留**，模型缓存不会自动删）。

**Q7：第一次启动很慢，第二次就快了？**
- 正常。MMS-FA 模型 ~1GB 首次加载入内存要十几秒。GUI 同一会话内连续处理多首歌时模型只加载一次。

**Q8：怎么看本次运行日志？**
- 开始菜单 → **autoKara → 打开日志目录**，里面按时间戳排列。提 issue 时把对应那份附上能帮作者快速定位。

**Q9：处理到一半我关窗口了？**
- 会有"处理中，确认关闭？"二次确认。强关后下次重新点"开始处理"即可，不会损坏环境。

---

## 七、附：文件位置 / 手动安装参考

**一键安装版关键路径**：

| 用途 | 路径 |
|---|---|
| 应用主目录 | `%LOCALAPPDATA%\Programs\autoKara\` |
| 私有 Python | `%LOCALAPPDATA%\Programs\autoKara\python\` |
| 自定义读音表 | `%LOCALAPPDATA%\Programs\autoKara\readings.txt` |
| 用户偏好（设置） | `%LOCALAPPDATA%\autoKara\settings.json` |
| 每次运行日志 | `%LOCALAPPDATA%\autoKara\logs\YYYYMMDD-HHMMSS.log` |
| 分阶段安装标记 | `%LOCALAPPDATA%\Programs\autoKara\.state\*.ok` |
| 环境就绪总标记 | `%LOCALAPPDATA%\Programs\autoKara\.env_ready` |
| 默认输出位置 | 桌面（GUI 中可改） |
| MMS-FA 模型缓存 | `%USERPROFILE%\.cache\torch\hub\checkpoints\model.pt` (~1.26GB) |
| Demucs 模型缓存 | `%USERPROFILE%\.cache\torch\hub\` |
| nltk 词典 | `%APPDATA%\nltk_data\` |

**手动安装（开发者 / 不想要私有 Python）**：

1. 装 [Python 3.11+](https://www.python.org/downloads/)。
2. clone 仓库 / 解压源码到任一目录。
3. PowerShell 进入该目录：
   ```powershell
   # 有 NVIDIA + 新驱动 (≥570，Blackwell)：
   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
   # 或老驱动 (≥525)：
   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
   # 没显卡：
   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

   pip install -r requirements.txt
   ```
4. 启动 GUI：`python launcher.py`（推荐，带环境检查）；或 `python gui.py`；或 CLI：`python main.py`。

**主要源码文件**（排查参考）：
- `launcher.py` — 启动器（环境健康检查 + 崩溃兜底 + 日志旁路）
- `gui.py` — 图形界面
- `main.py` — 核心管线 + CLI 入口（含 `--doctor`）
- `furigana.py` — 自动注音（读 `readings.txt`）
- `separate.py` — 人声分离
- `installer/setup_env.bat` — 分阶段环境配置脚本

---

遇到指南未覆盖的问题：把 **`%LOCALAPPDATA%\autoKara\logs\` 里最新一份日志 + 对应的 `.qc.txt` + `python main.py --doctor` 的输出**一起发到项目 Issue 区，作者能最快定位。祝打轴愉快！
