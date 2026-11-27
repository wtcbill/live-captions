# Windows 日语实时字幕转简体中文

中文字幕窗口默认置顶，可拖动和缩放。点击右上角的**模型设置**，在**转写模型**与**翻译模型**选项卡选择后保存，再重新打开字幕窗口。

- **短句字幕**显示当前中文、对应日文和最近 5 段已定稿的译文。前文可滚动查看。停顿或持续讲话时，可先显示「临时译文」，稳定后更新为完整译文。
- **分钟摘要**每 60 秒将本分钟定稿的日文和中文成对发送给 DeepSeek Flash，无论选用哪个翻译模型。界面显示中文要点和对应时间段。跨分钟才定稿的片段纳入下一次请求。安静的一分钟会保留上次摘要。

日文原句和中文定稿译文按分钟保存在 `.runtime/transcripts`。关闭窗口时也会保存该分钟内已定稿的句子。实时翻译和分钟摘要分别运行。Hy-MT3 翻译在本机完成，配置密钥后，分钟摘要会把中日文本发送到 `api.deepseek.com`。

## Windows 自带实时字幕

1. 按 `Win + Ctrl + L` 打开 Windows 实时字幕，把识别语言选为**日语**。
2. 在 Windows 实时字幕的齿轮菜单中，打开**首选项 → 包括麦克风音频**。每次重新打开 Windows 实时字幕都需要重新打开这个开关。
3. 双击同目录的 `Start.cmd`，等中文字幕窗口显示已连接。把两个窗口移到方便看的位置，保留 Windows 实时字幕窗口打开。

## NVIDIA 本地字幕

默认转写模型为 **Parakeet JA 0.6B CTC INT8**。部署下面的权重后，双击 `Start.cmd`，字幕窗口会从 Windows 默认播放设备采集声音，用 CUDA 转写日语。选择 **Hy-MT3-7B Q4_K_M** 可在本机翻译。

Parakeet 用 Silero VAD 检测停顿，约每 1.25 秒更新临时字幕。也可以在模型设置里切换到 **Nemotron 3.5 ASR 0.6B**，它使用 1.2 秒停顿和 20 秒最长段的配置。

在其他电脑部署本地模型时，使用 Python 3.12 x64，并在现有虚拟环境中安装 `requirements-local.txt`。运行需要 CUDA 12.8 和 cuDNN 9。可以在 `.runtime/cudnn` 放置对应的 cuDNN DLL。

- 将 [Parakeet JA CTC INT8](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-ctc/nemo/japanese.html) 的压缩包解包到 `.runtime/models`，保留包内目录名，再将 [silero_vad.onnx](https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx) 放入同一模型文件夹。
- 使用 Nemotron 时，将 [Nemotron 多语 560 ms INT8](https://k2-fsa.github.io/sherpa/onnx/nemo/nemotron-streaming.html) 的压缩包解包到 `.runtime/models`，保留包内目录名。
- 将 [Hy-MT3-7B Q4_K_M](https://huggingface.co/tencent/Hy-MT3-7B-GGUF) 权重放到 `.runtime/models/Hy-MT3-7B-Q4_K_M.gguf`。
- 将 [llama.cpp Windows CUDA 版](https://github.com/ggml-org/llama.cpp/releases/tag/b12676) 的 `llama-server.exe` 与配套 DLL 放到 `.runtime/llama`。

人名、时间和限制条件以日文字幕及现场原话为准。

## 首次准备或换电脑

第一次在新电脑配置时，使用带 Tkinter 的 Python 3.12，在本目录运行 `py -3.12 -m venv .venv`，再用 `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` 安装依赖。使用 Windows 自带实时字幕还需要支持该功能的 Windows 11 和日语语音识别包。

点击字幕窗口右上角的 **API 密钥**，输入并保存 DeepSeek 密钥。密钥由当前 Windows 账户加密后保存在 `.runtime`。也支持 `DEEPSEEK_API_KEY` 环境变量和 PowerShell 命令 `.\Launch.ps1 -Configure`。换 Windows 用户或电脑时重新录入。

模型检查可运行 `.\Launch.ps1 -Check`，仅发送内置测试句。连接检查可运行 `.\.venv\Scripts\python.exe -B captions.py --probe 5`，报告字幕连接状态和非空更新次数。
