; ============================================================
;  autoKara 一键安装程序  (Inno Setup 脚本)
;
;  构建方式：
;    1) 安装免费的 Inno Setup 6  https://jrsoftware.org/isdl.php
;       (或 winget install JRSoftware.InnoSetup)
;    2) 双击运行同目录的 build_installer.bat
;       (或用 Inno Setup 打开本文件，菜单 Build -> Compile)
;  产物：  Output\autoKara-setup.exe
;
;  安装器在目标机器上做什么：
;    - 把源码装到 用户目录\Programs\autoKara （无需管理员）
;    - 运行 setup_env.bat：下载独立 Python、按显卡装 torch、装依赖、预下载模型
;    - 创建开始菜单 / 桌面快捷方式与卸载程序
;
;  说明：界面按钮为英文（Inno 默认随附）。若想要简体中文向导，
;  把 ChineseSimplified.isl 放进 Inno 的 Languages 目录后，
;  取消下面 [Languages] 注释即可。自定义提示文字本就是中文。
; ============================================================

#define MyAppName "autoKara"
#define MyAppVersion "1.0"

[Setup]
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=autoKara
DefaultDirName={localappdata}\Programs\autoKara
DefaultGroupName=autoKara
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=autoKara-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

; 若已放入中文语言文件，取消下面两行注释（默认用英文以保证可编译）：
; [Languages]
; Name: "chs"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Files]
; --- 应用源码（installer 的上一级即项目根目录） ---
Source: "..\main.py";          DestDir: "{app}"; Flags: ignoreversion
Source: "..\gui.py";           DestDir: "{app}"; Flags: ignoreversion
Source: "..\launcher.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\furigana.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\separate.py";      DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
; 自定义读音覆盖表（onlyifdoesntexist：重装时不覆盖用户已积累的纠正）
Source: "..\readings.txt";     DestDir: "{app}"; Flags: onlyifdoesntexist
Source: "..\knm.png";          DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\USER_GUIDE.md";    DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "..\LICENSE";          DestDir: "{app}"; Flags: ignoreversion
; --- 环境配置脚本（安装时运行，也可日后重跑） ---
Source: "setup_env.bat";       DestDir: "{app}"; Flags: ignoreversion
; --- 可选图标（build_installer.bat 会尝试由 knm.png 生成） ---
Source: "app.ico";             DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Dirs]
Name: "{app}\input"
Name: "{app}\output"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Run]
; 安装时配置环境：下载 Python + 依赖 + 模型。保留控制台窗口以显示进度。
Filename: "{cmd}"; Parameters: "/c ""{app}\setup_env.bat"""; \
    StatusMsg: "正在配置运行环境：下载独立 Python、依赖与模型（数 GB，耗时较长，请勿关闭弹出的窗口）..."; \
    Flags: waituntilterminated

[Icons]
; 主快捷方式指向 launcher.py（先做依赖健康检查，再启 GUI；崩溃会弹友好对话框而不是黑屏闪退）
Name: "{group}\autoKara";       Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"
Name: "{group}\重新配置环境";    Filename: "{app}\setup_env.bat"; WorkingDir: "{app}"
; 环境诊断（控制台显示，方便复制结果给作者排查）
Name: "{group}\环境诊断";        Filename: "{app}\python\python.exe"; Parameters: """{app}\main.py"" --doctor"; WorkingDir: "{app}"
; 打开日志目录（launcher 每次运行都会写一份日志到这里）
Name: "{group}\打开日志目录";    Filename: "{cmd}"; Parameters: "/c explorer ""%LOCALAPPDATA%\autoKara\logs"""; WorkingDir: "{app}"
; 用户指南
Name: "{group}\用户指南";        Filename: "{app}\USER_GUIDE.md"; WorkingDir: "{app}"
Name: "{group}\卸载 autoKara";   Filename: "{uninstallexe}"
Name: "{autodesktop}\autoKara"; Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\launcher.py"""; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"; Tasks: desktopicon

[UninstallDelete]
; 卸载时清理安装器装入的私有 Python / 缓存 / 标记 / 输出
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\.state"
Type: files;          Name: "{app}\.env_ready"
; 注：用户的 readings.txt（自定义读音）以及 ~/.cache/torch（模型缓存，1.6GB）、
; %APPDATA%\nltk_data、%LOCALAPPDATA%\autoKara\logs 这些"用户数据"不会自动清理，
; 避免误删；用户如需彻底清理可手动删除上述路径。
