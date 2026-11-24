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
from deepseek_translation import DeepSeekTranslator, DeepSeekUnavailable

HERE = Path(__file__).resolve().parent
SUMMARY_INTERVAL_MS = 60_000
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


class SubtitleWindow:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("日语 → 中文字幕")
        self.root.geometry("820x415")
        self.root.minsize(470, 400)
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#151c28")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        bar = tk.Frame(root, bg="#151c28")
        bar.pack(fill="x", padx=20, pady=(12, 5))
        self.status = tk.StringVar(value="正在连接 Windows 实时字幕…")
        tk.Label(
            bar,
            textvariable=self.status,
            font=("Microsoft YaHei UI", 10),
            fg="#b3c4da",
            bg="#151c28",
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        self.previous = tk.StringVar()
        self.translation = tk.StringVar()
        self.source = tk.StringVar()
        self.summary_status = tk.StringVar(value="分钟摘要 · 等待第一分钟")
        self.summary_text = tk.StringVar()
        summary_panel = tk.Frame(root, bg="#202c3e")
        summary_panel.pack(side="bottom", fill="x", padx=16, pady=12)
        tk.Label(
            summary_panel,
            textvariable=self.summary_status,
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="#202c3e",
            fg="#c4d4eb",
            anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 0))
        summary_label = tk.Label(
            summary_panel,
            textvariable=self.summary_text,
            font=("Microsoft YaHei UI", 14),
            bg="#202c3e",
            fg="#f5f8fc",
            justify="left",
            anchor="w",
            wraplength=768,
        )
        summary_label.pack(fill="x", padx=10, pady=(2, 8))
        self.labels = [summary_label]
        for variable, font_size, color in (
            (self.previous, 14, "#b3c4da"),
            (self.translation, 23, "#f5f8fc"),
            (self.source, 12, "#b3c4da"),
        ):
            label = tk.Label(
                root,
                textvariable=variable,
                font=("Microsoft YaHei UI", font_size),
                bg="#151c28",
                fg=color,
                justify="left",
                anchor="w",
                wraplength=780,
            )
            label.pack(fill="x", padx=20, pady=4)
            self.labels.append(label)
        root.bind("<Configure>", self.resize)

        self.caption_buffer = CaptionBuffer()
        self.events = queue.SimpleQueue()
        self.waiting = deque(maxlen=4)
        self.minute_segments: dict[Caption, None] = {}
        self.source_state = "waiting"
        self.last_final = ""
        self.final_segment = None
        self.preview_visible = False
        self.context = ""
        self.last_error = ""
        self.retry_after = 0.0
        self.generation = 0
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.summary_executor = ThreadPoolExecutor(max_workers=1)
        self.translator = None
        self.job = None
        self.summary_job = None
        self.reader = start_reader()
        threading.Thread(
            target=read_events, args=(self.reader, self.events), daemon=True
        ).start()
        root.after(50, self.tick)
        root.after(SUMMARY_INTERVAL_MS, self.summarize_minute)

    def resize(self, event):
        if event.widget is self.root:
            summary_label, *short_labels = self.labels
            compact = event.width < 640
            summary_label.configure(
                wraplength=max(400, event.width - 52),
                font=("Microsoft YaHei UI", 12 if compact else 14),
            )
            short_labels[1].configure(
                font=("Microsoft YaHei UI", 18 if compact else 23)
            )
            for label in short_labels:
                label.configure(wraplength=max(400, event.width - 40))

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
                if self.final_segment in invalidated:
                    if self.source.get() == self.final_segment.text:
                        self.translation.set("")
                        self.source.set("")
                    self.previous.set("")
                    self.last_final = ""
                    self.context = ""
                    self.final_segment = None
                if self.job is not None and (
                    self.job[2] is None or self.job[2] in invalidated
                ):
                    self.generation += 1
            self.record_finished(finished, now)

        paused = self.caption_buffer.finish_on_pause(now)
        if paused is not None:
            self.record_finished([paused], now)
        self.update_translation(now)
        self.update_summary()
        self.root.after(50, self.tick)

    def record_finished(self, pieces: list[Caption], now: float):
        for piece in pieces:
            self.waiting.append((piece, now))
            self.minute_segments[piece] = None

    def next_event(self):
        try:
            return self.events.get_nowait()
        except queue.Empty:
            return None

    def translate(self, text: str, previous: str) -> str:
        try:
            if self.translator is None:
                self.translator = DeepSeekTranslator()
            return self.translator.translate(text, previous)
        except DeepSeekUnavailable:
            self.close_translator()
            raise

    def close_translator(self):
        if self.translator is not None:
            self.translator.close()
            self.translator = None

    def update_translation(self, now):
        if self.job is not None and self.job[0].done():
            future, text, piece, generation = self.job
            self.job = None
            try:
                translated = future.result()
                self.last_error = ""
            except DeepSeekUnavailable as exc:
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
                self.previous.set(self.last_final)
                self.translation.set(translated)
                self.source.set(text)
                self.preview_visible = piece is None
                if piece is not None:
                    self.last_final = translated
                    self.context = text
                    self.final_segment = piece

        if (
            self.job is None
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
                    self.last_final = self.translation.get()
                    self.context = text
                    self.final_segment = piece
                    self.preview_visible = False
                else:
                    self.job = (
                        self.executor.submit(self.translate, text, self.context),
                        text,
                        piece,
                        self.generation,
                    )

        if self.source_state == "stopped":
            status = "字幕读取已停止，请重新打开中文字幕窗口。"
        elif self.source_state != "connected":
            status = "等待 Windows 实时字幕。按 Win + Ctrl + L 打开，选择日语。"
        elif self.last_error:
            status = f"{self.last_error} 日文请看 Windows 实时字幕。"
        elif self.job is not None:
            status = (
                "短句翻译中 · 当前为临时译文"
                if self.preview_visible
                else "DeepSeek 正在翻译短句…"
            )
        elif self.preview_visible:
            status = "短句 · 临时译文"
        else:
            status = "短句 · DeepSeek 在线翻译"
        if status != self.status.get():
            self.status.set(status)

    def summarize_minute(self):
        self.root.after(SUMMARY_INTERVAL_MS, self.summarize_minute)
        self.update_summary()
        transcript = "\n".join(piece.text for piece in self.minute_segments)
        self.minute_segments.clear()
        if not transcript or self.summary_job is not None:
            return
        end = datetime.now(JST)
        start = end - timedelta(milliseconds=SUMMARY_INTERVAL_MS)
        period = f"{start:%H:%M}–{end:%H:%M}"
        if not self.summary_text.get():
            self.summary_status.set(f"分钟摘要 · 正在整理 {period}")
        self.summary_job = (
            self.summary_executor.submit(self.summarize, transcript),
            period,
        )

    @staticmethod
    def summarize(transcript: str) -> str:
        translator = DeepSeekTranslator()
        try:
            return translator.summarize(transcript)
        finally:
            translator.close()

    def update_summary(self):
        if self.summary_job is None or not self.summary_job[0].done():
            return
        future, period = self.summary_job
        self.summary_job = None
        try:
            self.summary_text.set(future.result())
            self.summary_status.set(f"分钟摘要 · {period}")
        except DeepSeekUnavailable as exc:
            self.summary_text.set(str(exc))
            self.summary_status.set(f"分钟摘要 · {period} 暂无法生成")

    def close(self):
        self.reader.terminate()
        self.executor.submit(self.close_translator)
        self.executor.shutdown(wait=False)
        self.summary_executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Translate Japanese Windows Live Captions to Chinese with DeepSeek."
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
