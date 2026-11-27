import os
import re
import secrets
import socket
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep

import httpx

RUNTIME = Path(__file__).resolve().parent / ".runtime"
MODEL = RUNTIME / "models" / "Hy-MT3-7B-Q4_K_M.gguf"
SERVER = RUNTIME / "llama" / "llama-server.exe"


class LocalTranslationUnavailable(RuntimeError):
    pass


def cuda_directories():
    cuda = os.environ.get("CUDA_PATH")
    directories = [RUNTIME / "cudnn"]
    if cuda:
        directories.append(Path(cuda) / "bin")
    return [directory for directory in directories if directory.is_dir()]


class LocalTranslator:
    def __init__(self):
        if not SERVER.is_file() or not MODEL.is_file():
            raise LocalTranslationUnavailable("请先安装 Hy-MT3-7B 权重和 llama.cpp CUDA 运行库。")

        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        key = secrets.token_urlsafe(24)
        env = os.environ.copy()
        env.pop("DEEPSEEK_API_KEY", None)
        env["PATH"] = os.pathsep.join([*(str(p) for p in cuda_directories()), env["PATH"]])
        env["LLAMA_API_KEY"] = key
        self.client = httpx.Client(
            base_url=f"http://127.0.0.1:{port}",
            headers={"Authorization": f"Bearer {key}"},
            timeout=8,
            trust_env=False,
        )
        self.directory = TemporaryDirectory(prefix="llama-", dir=RUNTIME)
        log_path = Path(self.directory.name) / "server.log"
        self.process = None
        try:
            log_path.touch()
            self.process = subprocess.Popen(
                [
                    str(SERVER), "--model", str(MODEL),
                    "--host", "127.0.0.1", "--port", str(port),
                    "--device", "CUDA0", "--n-gpu-layers", "all", "--fit", "off",
                    "--ctx-size", "2048", "--parallel", "1", "--jinja", "--no-webui",
                    "--log-file", str(log_path), "--log-colors", "off", "--log-verbosity", "4",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.gpu_layers = self.wait_until_ready(log_path)
        except (OSError, LocalTranslationUnavailable):
            self.close()
            raise

    def wait_until_ready(self, log_path):
        deadline = monotonic() + 90
        while monotonic() < deadline and self.process.poll() is None:
            try:
                ready = self.client.get("/health", timeout=0.5).status_code == 200
            except httpx.RequestError:
                ready = False
            if ready:
                log = log_path.read_text(encoding="utf-8", errors="replace")
                layers = re.search(r"offloaded (\d+)/(\d+) layers to GPU", log)
                if layers and layers[1] == layers[2] and int(layers[1]) > 0:
                    return int(layers[1])
                raise LocalTranslationUnavailable("Hy-MT3 未能将全部模型层加载到 CUDA。")
            sleep(0.2)
        log = log_path.read_text(encoding="utf-8", errors="replace")
        detail = next((line.strip() for line in reversed(log.splitlines())
                       if any(word in line.lower() for word in ("error", "failed", "out of memory"))), "")
        raise LocalTranslationUnavailable(f"Hy-MT3 启动失败。{detail[:220]}")

    def translate(self, text, previous=""):
        context = f"〖背景信息〗\n{previous}\n\n" if previous else ""
        instruction = "请结合背景信息将" if previous else "请将"
        prompt = (
            f"{context}{instruction}以下日语片段翻译为简体中文。"
            "只输出当前片段的译文，保留人名、数字和否定。"
            "如果原文没说完，只翻译已表达的部分。\n"
            f"〖待翻译文本〗\n{text}"
        )
        try:
            response = self.client.post(
                "/v1/chat/completions",
                json={
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 160,
                },
            )
            response.raise_for_status()
            result = response.json()["choices"][0]
            translation = result["message"]["content"].strip()
        except (httpx.HTTPError, ValueError, KeyError, IndexError, AttributeError) as error:
            raise LocalTranslationUnavailable("Hy-MT3 本地翻译请求失败。") from error
        if result.get("finish_reason") != "stop" or not translation:
            raise LocalTranslationUnavailable("Hy-MT3 未返回完整译文。")
        return translation

    def close(self):
        self.client.close()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.directory.cleanup()
