import argparse
import ctypes
import json
import os
import wave
from collections import deque
from contextlib import ExitStack, closing, contextmanager
from itertools import chain

from local_translation import RUNTIME, cuda_directories

MODEL = RUNTIME / "models" / "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"


@contextmanager
def cuda_runtime():
    with ExitStack() as dlls:
        directories = cuda_directories()
        os.environ["PATH"] = os.pathsep.join([*(str(p) for p in directories), os.environ["PATH"]])
        for path in directories:
            dlls.enter_context(os.add_dll_directory(str(path)))
        ctypes.WinDLL("cudnn64_9.dll")
        yield


def require_cuda_provider(model_name):
    loaded_module = ctypes.windll.kernel32.GetModuleHandleW
    loaded_module.argtypes = [ctypes.c_wchar_p]
    loaded_module.restype = ctypes.c_void_p
    if not loaded_module("onnxruntime_providers_cuda.dll"):
        raise RuntimeError(f"{model_name} 未能加载 CUDA 推理后端。")


def create_recognizer():
    if not MODEL.is_dir():
        raise RuntimeError("请先安装 Nemotron 3.5 的 560 ms INT8 权重。")
    with cuda_runtime():
        import sherpa_onnx
        recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(MODEL / "tokens.txt"),
            encoder=str(MODEL / "encoder.int8.onnx"),
            decoder=str(MODEL / "decoder.int8.onnx"),
            joiner=str(MODEL / "joiner.int8.onnx"),
            provider="cuda",
            enable_endpoint_detection=True,
            rule2_min_trailing_silence=1.2,
            rule3_min_utterance_length=20,
            cudnn_conv_algo_search=1,
        )
        require_cuda_provider("Nemotron")
    return recognizer


@contextmanager
def system_audio():
    import numpy as np
    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as audio:
        device = audio.get_default_wasapi_loopback()
        rate = int(device["defaultSampleRate"])
        channels = int(device["maxInputChannels"])
        frames = max(1, rate // 10)
        with audio.open(
            format=pyaudio.paFloat32, channels=channels, rate=rate,
            input=True, input_device_index=device["index"], frames_per_buffer=frames,
        ) as stream:
            def chunks():
                while True:
                    data = stream.read(frames, exception_on_overflow=False)
                    yield rate, np.frombuffer(data, dtype="<f4").reshape(-1, channels).mean(axis=1)

            yield chunks()


def wave_audio(path):
    import numpy as np

    with wave.open(path) as source:
        rate = source.getframerate()
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError("测试文件须为单声道 16 位 PCM WAV。")
        while data := source.readframes(rate // 10):
            yield rate, np.frombuffer(data, dtype="<i2").astype("float32") / 32768
        yield rate, np.zeros(rate, dtype="float32")


def recognition_events(recognizer, audio):
    stream = recognizer.create_stream()
    stream.set_option("language", "ja")
    completed = deque(maxlen=7)
    partial = ""
    for rate, samples in audio:
        stream.accept_waveform(rate, samples)
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        text = recognizer.get_result(stream).strip()
        if recognizer.is_endpoint(stream):
            if text:
                completed.append(text)
                yield {"state": "connected", "text": "\n".join(completed), "final": True}
            recognizer.reset(stream)
            partial = ""
        elif text and text != partial:
            partial = text
            yield {"state": "connected", "text": "\n".join((*completed, text))}
    if partial:
        completed.append(partial)
        yield {"state": "connected", "text": "\n".join(completed), "final": True}


def run_input(create_backend, make_events):
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav")
    args = parser.parse_args()
    try:
        recognizer = create_backend()
        with (closing(wave_audio(args.wav)) if args.wav else system_audio()) as audio:
            if args.wav:
                audio = chain((next(audio),), audio)
            print(json.dumps({"state": "connected", "text": ""}), flush=True)
            for event in make_events(recognizer, audio):
                print(json.dumps(event), flush=True)
    except (ImportError, OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"state": "error", "text": "", "error": str(error)}), flush=True)
        return 1
    return 0


def main():
    return run_input(create_recognizer, recognition_events)


if __name__ == "__main__":
    raise SystemExit(main())
