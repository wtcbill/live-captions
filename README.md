# Windows 日语实时字幕转简体中文

Windows 实时字幕负责收音和日语识别。中文字幕窗口默认置顶，可拖动和缩放，分为两块。

- **短句字幕**显示当前中文、对应日文和上一句译文。Windows 开始新行或出现句末标点时，上一段立即进入翻译。停顿或持续讲话时，可先显示「临时译文」，稳定后更新为完整译文。
- **分钟摘要**每 60 秒整理累计的日文，显示 2–3 条中文要点和对应时间段。跨分钟的未完成片段等稳定后纳入下一段，短句翻译在摘要生成期间继续更新。安静的一分钟会保留上次摘要的时间标签。

打开中文字幕窗口会将新出现的日文字幕发送到 `api.deepseek.com`。麦克风音频由 Windows 在本机处理，工具不保存字幕记录。

## 现场使用

1. 按 `Win + Ctrl + L` 打开 Windows 实时字幕，把识别语言选为**日语**。
2. 在 Windows 实时字幕的齿轮菜单中，打开**首选项 → 包括麦克风音频**。每次重新打开 Windows 实时字幕都需要重新打开这个开关。
3. 双击同目录的 `Start.cmd`，等中文字幕窗口显示已连接。把两个窗口移到方便看的位置，保留 Windows 实时字幕窗口打开。
4. 中文翻译需要联网。遇到超时或连接错误时，窗口会显示原因。Windows 日文字幕可继续显示，之后有新字幕时工具会再尝试翻译。

人名、时间和限制条件以日文字幕及现场原话为准。

## 首次准备或换电脑

需要支持实时字幕的 Windows 11、日语语音识别包和带 Tkinter 的 Python 3.12。第一次在新电脑配置时，在本目录运行 `py -3.12 -m venv .venv`，再用 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` 安装依赖。翻译使用 `deepseek-flash` 的非思考模式。

首次用 `Start.cmd` 启动时，可在提示里输入 DeepSeek API 密钥。启动器用 Windows 当前用户加密并保存在 `.runtime`，也支持使用 `DEEPSEEK_API_KEY` 环境变量。要更换已保存的密钥，在 PowerShell 运行 `.\Launch.ps1 -Configure`。换 Windows 用户或电脑时重新录入。

模型检查可运行 `.\Launch.ps1 -Check`，仅发送内置测试句。连接检查可运行 `.\.venv\Scripts\python.exe -B captions.py --probe 5`，报告字幕连接状态和非空更新次数。
