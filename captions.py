import argparse
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from caption_buffer import Caption, CaptionBuffer
from deepseek_key import KEY_ENV, configure_key, restore_key
from deepseek_translation import DeepSeekTranslator, DeepSeekUnavailable
from local_translation import LocalTranslator, LocalTranslationUnavailable
from model_settings import (
    ACCENT,
    BACKGROUND,
    BORDER,
    FONT,
    PANEL,
    SECONDARY,
    TEXT,
    ModelSettings,
    action_button,
    load_models,
)

HERE = Path(__file__).resolve().parent
LOCAL_INPUTS = {
    "nemotron": ("Nemotron CUDA", "nemotron_input.py"),
    "parakeet": ("Parakeet JA CUDA", "parakeet_input.py"),
}
SUMMARY_INTERVAL_MS = 60_000
HISTORY_LENGTH = 5
JST = timezone(timedelta(hours=9))
EXAMPLES = (
    "受付は明日の午前9時からです。",
    "開始は10時半です。",
    "提出する前に、担当者の田中さんに確認してください。",
    "会場では飲食できますが、持ち込みは禁止です。",
    "まだ決まっていないので、今日の午後3時までに連絡します。",
    "次の発表は、2階のB会議室で行います。",
    "明日は撮影できません。",
    "17時までに登録しないと、参加できません。",
)


def start_reader() -> subprocess.Popen:
    powershell = (
        Path(os.environ["SystemRoot"])
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    return subprocess.Popen(
        [
            str(powershell),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(HERE / "read_windows_captions.ps1"),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def read_events(reader: subprocess.Popen, events: queue.SimpleQueue):
    try:
        with reader.stdout as stdout:
            for line in stdout:
                if line.startswith("{"):
                    events.put(json.loads(line))
    finally:
        events.put({"state": "stopped", "text": ""})


def probe_reader(seconds: int) -> int:
    events = queue.SimpleQueue()
    reader = start_reader()
    threading.Thread(target=read_events, args=(reader, events), daemon=True).start()
    connected = False
    snapshots = 0
    stop = time.monotonic() + seconds
    try:
        while time.monotonic() < stop:
            try:
                event = events.get(timeout=max(0.0, stop - time.monotonic()))
            except queue.Empty:
                break
            if event["state"] == "stopped":
                break
            connected |= event["state"] == "connected"
            snapshots += bool(event["text"])
    finally:
        reader.terminate()
        reader.wait(timeout=5)
    print(f"Caption control connected: {connected}, nonempty snapshots: {snapshots}")
    return 0 if connected else 1


def display_text(parent, variable, font, color, placeholder):
    label = tk.Label(
        parent,
        font=font,
        fg=color,
        bg=PANEL,
        anchor="w",
        justify="left",
        padx=0,
        pady=0,
    )
    def update(*_):
        value = variable.get()
        label.configure(text=value or placeholder, fg=color if value else SECONDARY)

    variable.trace_add("write", update)
    update()
    return label


class SubtitleWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("实时字幕 · 日语 → 简体中文")
        self.root.geometry("820x560")
        self.root.minsize(470, 510)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.model_settings = None
        self.key_settings = None
        self.windows_captions_supported = sys.getwindowsversion().build >= 22621
        self.models = load_models()
        self.transcription_name, input_script = LOCAL_INPUTS.get(
            self.models["transcription"], ("Windows 实时字幕", None),
        )
        self.local_input = input_script is not None
        self.local_translation = self.models["translation"] == "hy-mt3"
        self.status = tk.StringVar(value="正在启动字幕…")
        self.translation = tk.StringVar()
        self.source = tk.StringVar()
        self.summary_status = tk.StringVar()
        self.summary_text = tk.StringVar()
        self.show_summary_readiness()
        self.build_layout()

        self.caption_buffer = CaptionBuffer()
        self.events = queue.SimpleQueue()
        self.waiting = deque(maxlen=4)
        self.history: deque[tuple[Caption, str]] = deque(maxlen=HISTORY_LENGTH + 1)
        self.minute_segments: dict[Caption, str] = {}
        self.minute_start = datetime.now(JST)
        self.archive_dir = HERE / ".runtime" / "transcripts" / (
            f"{self.minute_start:%Y%m%d-%H%M%S-%f}-{os.getpid()}"
        )
        self.pending_archives = deque()
        self.source_state = "waiting"
        self.source_error = ""
        self.preview_visible = False
        self.context = ""
        self.last_error = ""
        self.retry_after = 0.0
        self.generation = 0
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.summary_executor = ThreadPoolExecutor(max_workers=1)
        self.translator = None
        self.job = None
        self.summary_jobs = deque()
        if input_script is not None:
            python = Path(sys.executable)
            if python.name.lower() == "pythonw.exe":
                python = python.with_name("python.exe")
            self.reader = subprocess.Popen(
                [str(python), "-B", str(HERE / input_script)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            self.reader = start_reader()
        self.warmup = self.executor.submit(self.prepare_translation) if self.local_translation else None
        threading.Thread(
            target=read_events, args=(self.reader, self.events), daemon=True
        ).start()
        root.after(50, self.tick)
        root.after(SUMMARY_INTERVAL_MS, self.summarize_minute)

    def build_layout(self):
        outer = tk.Frame(self.root, bg=BACKGROUND)
        outer.pack(fill="both", expand=True, padx=20, pady=16)

        header = tk.Frame(outer, bg=BACKGROUND)
        header.pack(fill="x")
        action_button(header, "模型设置", self.open_model_settings).pack(side="right")
        action_button(header, "API 密钥", self.open_key_settings).pack(side="right", padx=(0, 8))
        tk.Label(
            header, text="实时字幕", font=(FONT, 18, "bold"), bg=BACKGROUND, fg=TEXT
        ).pack(side="left")
        tk.Label(
            header,
            text="日语 → 简体中文",
            font=(FONT, 10),
            bg=BACKGROUND,
            fg=SECONDARY,
        ).pack(side="left", padx=(12, 0), pady=(5, 0))

        status_bar = tk.Frame(outer, bg=BACKGROUND)
        status_bar.pack(fill="x", pady=(6, 12))
        self.status_dot = tk.Label(
            status_bar, text="●", font=(FONT, 8), bg=BACKGROUND, fg="#e4b275"
        )
        self.status_dot.pack(side="left", anchor="n", pady=(2, 0))
        self.status_label = tk.Label(
            status_bar,
            textvariable=self.status,
            font=(FONT, 10),
            fg=SECONDARY,
            bg=BACKGROUND,
            anchor="w",
            justify="left",
        )
        self.status_label.pack(side="left", fill="x", expand=True, padx=(6, 0))

        summary_panel = tk.Frame(outer, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        summary_panel.pack(side="bottom", fill="x")
        summary_header = tk.Frame(summary_panel, bg=PANEL)
        summary_header.pack(fill="x", padx=16, pady=(9, 3))
        tk.Label(
            summary_header, text="分钟摘要", font=(FONT, 11, "bold"), bg=PANEL, fg=ACCENT
        ).pack(side="left")
        tk.Label(
            summary_header, textvariable=self.summary_status, font=(FONT, 9), bg=PANEL, fg=SECONDARY
        ).pack(side="right")
        self.summary_label = display_text(
            summary_panel, self.summary_text, (FONT, 12), TEXT, "暂无摘要"
        )
        self.summary_label.pack(fill="x", padx=16, pady=(0, 12))

        caption_panel = tk.Frame(outer, bg=PANEL, highlightthickness=1, highlightbackground=BORDER)
        caption_panel.pack(fill="both", expand=True, pady=(0, 12))
        tk.Label(
            caption_panel, text="中文字幕", font=(FONT, 11, "bold"), bg=PANEL, fg=ACCENT
        ).pack(anchor="w", padx=16, pady=(9, 0))
        self.translation_label = display_text(
            caption_panel, self.translation, (FONT, 24, "bold"), TEXT, "等待译文"
        )
        self.translation_label.pack(fill="x", padx=16, pady=(8, 10))
        tk.Frame(caption_panel, bg=BORDER, height=1).pack(fill="x", padx=16, pady=(0, 5))

        source_row = tk.Frame(caption_panel, bg=PANEL)
        source_row.pack(fill="x", padx=16, pady=(2, 10))
        tk.Label(
            source_row, text="日文原文", width=8, anchor="nw", bg=PANEL, fg=ACCENT, font=(FONT, 9)
        ).pack(side="left", anchor="n")
        self.source_label = display_text(source_row, self.source, (FONT, 10), SECONDARY, "—")
        self.source_label.pack(side="left", fill="x", expand=True)

        history_panel = tk.Frame(caption_panel, bg=PANEL)
        history_panel.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        tk.Label(
            history_panel, text="前文 · 最近 5 段", bg=PANEL, fg=ACCENT, font=(FONT, 9, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        scrollbar = tk.Scrollbar(history_panel)
        scrollbar.pack(side="right", fill="y")
        self.history_text = tk.Text(
            history_panel, height=HISTORY_LENGTH, width=1, wrap="char",
            bg=PANEL, fg=SECONDARY, font=(FONT, 11), relief="flat", borderwidth=0,
            highlightthickness=0, padx=0, spacing3=2, state="disabled",
            yscrollcommand=scrollbar.set,
        )
        self.history_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self.history_text.yview)
        self.root.bind("<Configure>", self.resize)

    def open_model_settings(self):
        if self.model_settings is None or not self.model_settings.winfo_exists():
            self.model_settings = ModelSettings(self.root, self.models)
        self.model_settings.lift()
        self.model_settings.focus_set()

    def open_key_settings(self):
        if self.key_settings is None or not self.key_settings.winfo_exists():
            self.key_settings = configure_key(self.root, self.key_saved)
        self.key_settings.lift()

    def key_saved(self):
        self.show_summary_readiness()
        if not self.local_translation:
            self.executor.submit(self.close_translator)
            self.retry_after = 0

    def show_summary_readiness(self):
        if os.getenv(KEY_ENV, "").strip():
            self.summary_status.set("等待本分钟定稿")
            self.summary_text.set("")
        else:
            self.summary_status.set("需要 API 密钥")
            self.summary_text.set("点击右上角「API 密钥」启用分钟摘要。")

    def refresh_history(self):
        entries = list(self.history)
        previous = entries if self.preview_visible else entries[:-1]
        text = "\n".join(translated for _, translated in previous[-HISTORY_LENGTH:])
        self.history_text.configure(state="normal")
        self.history_text.delete("1.0", "end")
        self.history_text.insert("1.0", text)
        self.history_text.configure(state="disabled")
        self.history_text.see("end")

    def finish_translation(self, piece: Caption, translated: str):
        self.history.append((piece, translated))
        self.minute_segments[piece] = translated
        self.context = piece.text
        self.preview_visible = False

    def resize(self, event):
        if event.widget is self.root:
            compact = event.width < 640
            self.status_label.configure(wraplength=event.width - 70)
            self.summary_label.configure(wraplength=event.width - 76)
            self.translation_label.configure(
                wraplength=event.width - 76, font=(FONT, 19 if compact else 24, "bold")
            )
            self.source_label.configure(wraplength=event.width - 146)

    def tick(self):
        now = time.monotonic()
        for event in iter(self.next_event, None):
            state = event["state"]
            first_connection = state == "connected" and self.source_state != state
            if state != self.source_state and state != "connected":
                self.caption_buffer = CaptionBuffer()
                self.waiting.clear()
                self.context = ""
                self.generation += 1
            self.source_state = state
            if state == "error":
                self.source_error = event["error"]
            if first_connection:
                self.caption_buffer.prime(event["text"])
                continue
            finished = self.caption_buffer.update(event["text"], now)
            invalidated = self.caption_buffer.invalidated
            if invalidated:
                self.waiting = deque(
                    (item for item in self.waiting if item[0] not in invalidated),
                    maxlen=self.waiting.maxlen,
                )
                for piece in invalidated:
                    self.minute_segments.pop(piece, None)
                latest = self.history[-1][0] if self.history else None
                self.history = deque(
                    (entry for entry in self.history if entry[0] not in invalidated),
                    maxlen=self.history.maxlen,
                )
                if latest in invalidated:
                    piece, translated = self.history[-1] if self.history else (None, "")
                    self.context = piece.text if piece else ""
                    if not self.preview_visible:
                        self.translation.set(translated)
                        self.source.set(self.context)
                self.refresh_history()
                if self.job is not None and (
                    self.job[2] is None or self.job[2] in invalidated
                ):
                    self.generation += 1
            if event.get("final"):
                final = self.caption_buffer.finish_pending()
                if final is not None:
                    finished.append(final)
            self.record_finished(finished, now)

        paused = None if self.local_input else self.caption_buffer.finish_on_pause(now)
        if paused is not None:
            self.record_finished([paused], now)
        self.update_translation(now)
        self.update_summary()
        self.root.after(50, self.tick)

    def record_finished(self, pieces: list[Caption], now: float):
        for piece in pieces:
            self.waiting.append((piece, now))

    def next_event(self):
        try:
            return self.events.get_nowait()
        except queue.Empty:
            return None

    def prepare_translation(self):
        if self.translator is None:
            self.translator = LocalTranslator() if self.local_translation else DeepSeekTranslator()

    def translate(self, text: str, previous: str) -> str:
        try:
            self.prepare_translation()
            return self.translator.translate(text, previous)
        except (DeepSeekUnavailable, LocalTranslationUnavailable, OSError):
            self.close_translator()
            raise

    def close_translator(self):
        if self.translator is not None:
            self.translator.close()
            self.translator = None

    def update_translation(self, now):
        if self.warmup is not None and self.warmup.done():
            try:
                self.warmup.result()
            except (LocalTranslationUnavailable, OSError) as error:
                self.last_error = str(error)
                self.retry_after = now + 4
            self.warmup = None
        if self.job is not None and self.job[0].done():
            future, text, piece, generation = self.job
            self.job = None
            try:
                translated = future.result()
                self.last_error = ""
            except (DeepSeekUnavailable, LocalTranslationUnavailable, OSError) as exc:
                translated = ""
                self.last_error = str(exc)
                self.retry_after = now + 2
            next_piece = self.waiting[0][0] if self.waiting else None
            active = next_piece.text if next_piece else self.caption_buffer.pending
            if (
                translated
                and self.source_state == "connected"
                and generation == self.generation
                and (piece is not None or active.startswith(text))
            ):
                if piece is None and next_piece and next_piece.text == text:
                    piece = self.waiting.popleft()[0]
                self.translation.set(translated)
                self.source.set(text)
                refresh_history = not self.preview_visible or piece is not None
                self.preview_visible = piece is None
                if piece is not None:
                    self.finish_translation(piece, translated)
                if refresh_history:
                    self.refresh_history()

        if (
            self.job is None
            and self.warmup is None
            and self.source_state == "connected"
            and now >= self.retry_after
        ):
            while self.waiting and now - self.waiting[0][1] > 4:
                self.waiting.popleft()
            piece = self.waiting.popleft()[0] if self.waiting else None
            text = piece.text if piece else self.caption_buffer.preview(now)
            if text:
                if (
                    piece is not None
                    and self.preview_visible
                    and self.source.get() == text
                ):
                    self.finish_translation(piece, self.translation.get())
                    self.refresh_history()
                else:
                    self.job = (
                        self.executor.submit(self.translate, text, self.context),
                        text,
                        piece,
                        self.generation,
                    )

        if self.source_error:
            status = f"{self.transcription_name} 转写失败。{self.source_error}"
        elif self.source_state == "stopped":
            status = "字幕读取已停止，请重新打开中文字幕窗口。"
        elif self.local_input and self.source_state != "connected":
            status = f"正在加载 {self.transcription_name} 转写…"
        elif self.source_state != "connected":
            status = (
                "等待 Windows 实时字幕。按 Win + Ctrl + L 打开，选择日语。"
                if self.windows_captions_supported
                else "当前 Windows 版本缺少实时字幕。模型设置可以先查看。"
            )
        elif self.last_error:
            status = self.last_error
        elif self.warmup is not None:
            status = "正在加载 Hy-MT3 CUDA 翻译…"
        elif self.job is not None:
            status = (
                "短句翻译中 · 当前为临时译文"
                if self.preview_visible
                else "正在翻译短句…"
            )
        elif self.preview_visible:
            status = "短句 · 临时译文"
        else:
            translation = "Hy-MT3 CUDA" if self.local_translation else "DeepSeek 在线翻译"
            status = f"{self.transcription_name} · {translation}"
        if status != self.status.get():
            self.status.set(status)
            connected = self.source_state == "connected" and not self.last_error
            self.status_dot.configure(fg="#7be3b6" if connected else "#e4b275")

    def summarize_minute(self):
        self.root.after(SUMMARY_INTERVAL_MS, self.summarize_minute)
        self.update_summary()
        start = self.minute_start
        end = datetime.now(JST)
        sentences = self.archive_minute(end)
        if not sentences or not os.getenv(KEY_ENV, "").strip():
            return
        period = f"{start:%H:%M}–{end:%H:%M}"
        self.summary_status.set(f"正在整理 {period}")
        self.summary_jobs.append((self.summary_executor.submit(self.summarize, sentences), period))

    def archive_minute(self, end: datetime) -> list[dict[str, str]]:
        start = self.minute_start
        self.minute_start = end
        sentences = [
            {"ja": piece.text, "zh": translated}
            for piece, translated in self.minute_segments.items()
        ]
        self.minute_segments.clear()
        if sentences:
            self.pending_archives.append((
                self.archive_dir / f"{start:%H%M%S-%f}.json",
                {"from": start.isoformat(), "to": end.isoformat(), "sentences": sentences},
            ))
        while self.pending_archives:
            path, record = self.pending_archives[0]
            temporary = path.with_suffix(".tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(path)
            except OSError:
                self.summary_status.set("字幕存档失败")
                break
            self.pending_archives.popleft()
        return sentences

    @staticmethod
    def summarize(sentences: list[dict[str, str]]) -> str:
        translator = DeepSeekTranslator()
        try:
            return translator.summarize(sentences)
        finally:
            translator.close()

    def update_summary(self):
        while self.summary_jobs and self.summary_jobs[0][0].done():
            future, period = self.summary_jobs.popleft()
            try:
                self.summary_text.set(future.result())
                self.summary_status.set(period)
            except DeepSeekUnavailable as exc:
                self.summary_text.set(str(exc))
                self.summary_status.set(f"{period} 暂无法生成")

    def close(self):
        self.archive_minute(datetime.now(JST))
        self.reader.terminate()
        self.executor.submit(self.close_translator)
        self.executor.shutdown(wait=False)
        self.summary_executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show Japanese speech and Simplified Chinese translations as live captions."
    )
    parser.add_argument(
        "--check-deepseek",
        action="store_true",
        help="translate built-in test phrases using DeepSeek",
    )
    parser.add_argument(
        "--probe",
        type=int,
        metavar="SECONDS",
        help="test the caption reader without logging text",
    )
    args = parser.parse_args()
    if args.probe is not None:
        return probe_reader(args.probe)
    restore_key()
    if args.check_deepseek:
        try:
            deepseek = DeepSeekTranslator()
            try:
                for sample in EXAMPLES[:3]:
                    started = time.perf_counter()
                    result = deepseek.translate(sample)
                    print(
                        f"{1000 * (time.perf_counter() - started):.0f} ms  {sample} → {result}"
                    )
            finally:
                deepseek.close()
        except DeepSeekUnavailable as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    root = tk.Tk()
    SubtitleWindow(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
