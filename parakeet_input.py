from collections import deque

from local_translation import RUNTIME
from nemotron_input import cuda_runtime, require_cuda_provider, run_input


MODEL = RUNTIME / "models" / "sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8"
SAMPLE_RATE = 16000
PREVIEW_INTERVAL = 1.25


def create_backend():
    model, tokens, vad_model = (MODEL / name for name in ("model.int8.onnx", "tokens.txt", "silero_vad.onnx"))
    if not all(path.is_file() for path in (model, tokens, vad_model)):
        raise RuntimeError("请先安装 Parakeet JA INT8 权重和 Silero VAD。")
    with cuda_runtime():
        import sherpa_onnx

        recognizer = sherpa_onnx.OfflineRecognizer.from_nemo_ctc(
            model=str(model), tokens=str(tokens), provider="cuda", num_threads=2,
        )
        require_cuda_provider("Parakeet JA")
        config = sherpa_onnx.VadModelConfig()
        config.sample_rate = SAMPLE_RATE
        config.num_threads = 1
        config.silero_vad.model = str(vad_model)
        config.silero_vad.threshold = 0.45
        config.silero_vad.min_speech_duration = 0.22
        config.silero_vad.min_silence_duration = 0.65
        config.silero_vad.max_speech_duration = 16
        detector = sherpa_onnx.VoiceActivityDetector(config, 30)
    return recognizer, detector


def samples_at_model_rate(audio):
    import numpy as np
    from soxr import ResampleStream

    input_rate = None
    resampler = None
    for rate, samples in audio:
        if rate != input_rate:
            if resampler is not None:
                yield resampler.resample_chunk(np.empty(0, dtype=np.float32), last=True)
            input_rate = rate
            resampler = ResampleStream(rate, SAMPLE_RATE, 1) if rate != SAMPLE_RATE else None
        yield samples if resampler is None else resampler.resample_chunk(samples)
    if resampler is not None:
        yield resampler.resample_chunk(np.empty(0, dtype=np.float32), last=True)


def recognition_events(backend, audio):
    import numpy as np

    recognizer, vad = backend
    completed = deque(maxlen=7)
    preview = ""
    cursor = 0
    preview_step = int(PREVIEW_INTERVAL * SAMPLE_RATE)
    next_preview = preview_step

    def recognize(samples):
        stream = recognizer.create_stream()
        stream.accept_waveform(SAMPLE_RATE, np.asarray(samples, dtype=np.float32))
        recognizer.decode_stream(stream)
        return stream.result.text.strip()

    def finalize():
        while not vad.empty():
            text = recognize(vad.front.samples)
            vad.pop()
            if text:
                completed.append(text)
                yield {"state": "connected", "text": "\n".join(completed), "final": True}

    for samples in samples_at_model_rate(audio):
        if not len(samples):
            continue
        cursor += len(samples)
        vad.accept_waveform(samples)
        if not vad.empty():
            yield from finalize()
            next_preview = cursor + preview_step
            preview = ""
        if cursor >= next_preview:
            next_preview = cursor + preview_step
            if vad.is_speech_detected():
                current = vad.current_segment.samples
                if len(current) >= int(0.45 * SAMPLE_RATE):
                    text = recognize(current)
                    if text and text != preview:
                        preview = text
                        yield {"state": "connected", "text": "\n".join((*completed, text))}

    vad.flush()
    yield from finalize()


if __name__ == "__main__":
    raise SystemExit(run_input(create_backend, recognition_events))
