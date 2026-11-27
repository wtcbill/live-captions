import json
import tkinter as tk
from pathlib import Path
from tempfile import NamedTemporaryFile
from tkinter import messagebox, ttk

from local_translation import RUNTIME

SETTINGS = RUNTIME / "settings.json"
DEFAULT_MODELS = {"transcription": "parakeet", "translation": "deepseek"}


MODEL_GROUPS = (
    (
        "transcription", "转写模型",
        (
            ("windows", "Windows 自带实时字幕", "读取日语字幕，需要系统支持实时字幕。", True),
            ("nemotron", "Nemotron 3.5 ASR 0.6B", "CUDA 日语转写，监听默认播放设备。", True),
            ("parakeet", "Parakeet JA 0.6B CTC INT8", "CUDA 日语转写，显示临时字幕和停顿后的定稿。", True),
            ("whisper", "Whisper large-v3", "本地日语转写，按语音片段处理。", False),
        ),
    ),
    (
        "translation", "翻译模型",
        (
            ("deepseek", "DeepSeek Flash", "在线日译中和分钟摘要，需要 API 密钥。", True),
            ("hy-mt3", "Hy-MT3-7B Q4_K_M", "CUDA 本地短句翻译。", True),
            ("plamo", "PLaMo 3 Translate 8B", "本地日译中，支持翻译历史和术语表。", False),
        ),
    ),
)

BACKGROUND = "#101827"
PANEL = "#19263a"
CONTROL = "#263d58"
BORDER = "#2b3c53"
TEXT = "#f4f7fc"
SECONDARY = "#b4c3d7"
ACCENT = "#97d9fb"
FONT = "Microsoft YaHei UI"


def load_models():
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_MODELS.copy()
    selected = DEFAULT_MODELS.copy()
    if isinstance(data, dict):
        for key, _, models in MODEL_GROUPS:
            if any(ready and data.get(key) == model for model, _, _, ready in models):
                selected[key] = data[key]
    return selected


def save_models(selected):
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=SETTINGS.parent, delete=False
    ) as pending:
        json.dump(selected, pending, indent=2)
    path = Path(pending.name)
    try:
        path.replace(SETTINGS)
    finally:
        path.unlink(missing_ok=True)


def action_button(parent, text, command):
    return tk.Button(
        parent,
        text=text,
        command=command,
        font=(FONT, 10, "bold"),
        bg=CONTROL,
        fg=TEXT,
        activebackground="#345574",
        activeforeground=TEXT,
        borderwidth=0,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        relief="flat",
        padx=14,
        pady=5,
        cursor="hand2",
    )


class ModelSettings(tk.Toplevel):
    def __init__(self, parent, active):
        super().__init__(parent)
        self.withdraw()
        self.title("模型设置")
        self.transient(parent)
        self.configure(bg=BACKGROUND)
        self.bind("<Escape>", lambda event: self.destroy())

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "ModelSettings.TNotebook",
            background=BACKGROUND,
            borderwidth=0,
            bordercolor=BACKGROUND,
            lightcolor=BACKGROUND,
            darkcolor=BACKGROUND,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "ModelSettings.TNotebook.Tab",
            padding=(24, 10),
            font=(FONT, 11, "bold"),
            lightcolor=BACKGROUND,
            darkcolor=BACKGROUND,
            bordercolor=BACKGROUND,
        )
        style.map(
            "ModelSettings.TNotebook.Tab",
            background=[("selected", CONTROL), ("active", CONTROL), ("!selected", PANEL)],
            foreground=[("selected", ACCENT), ("!selected", SECONDARY)],
            padding=[("selected", (24, 10)), ("!selected", (24, 10))],
        )
        style.configure(
            "ModelSettings.TRadiobutton",
            background=PANEL,
            foreground=TEXT,
            font=(FONT, 11, "bold"),
        )
        style.map(
            "ModelSettings.TRadiobutton",
            background=[("active", PANEL)],
            foreground=[("disabled", SECONDARY)],
        )

        footer = tk.Frame(self, bg=BACKGROUND)
        footer.pack(side="bottom", fill="x", padx=20, pady=(0, 14))
        action_button(footer, "保存", self.save).pack(side="right")
        action_button(footer, "关闭", self.destroy).pack(side="right", padx=(0, 8))
        tk.Label(footer, text="保存后重新打开字幕窗口生效", bg=BACKGROUND, fg=SECONDARY,
                 font=(FONT, 9)).pack(side="left")

        self.notebook = ttk.Notebook(self, style="ModelSettings.TNotebook")
        self.notebook.pack(fill="both", expand=True, padx=20, pady=(16, 8))
        self.notebook.enable_traversal()

        self.selections = {}
        configured = load_models()
        for key, title, models in MODEL_GROUPS:
            page = tk.Frame(self.notebook, bg=BACKGROUND)
            self.notebook.add(page, text=title)
            selection = tk.StringVar(page, value=configured[key])
            self.selections[key] = selection
            for model, name, description, available in models:
                self.add_model(page, selection, model, name, description, available, active[key] == model)
        self.update_idletasks()
        minimum_height = self.winfo_reqheight()
        self.minsize(440, minimum_height)
        self.geometry(f"520x{max(450, minimum_height)}")
        self.deiconify()

    def save(self):
        try:
            save_models({key: selection.get() for key, selection in self.selections.items()})
        except OSError as error:
            messagebox.showerror("无法保存模型选择", str(error), parent=self)
            return
        self.destroy()

    @staticmethod
    def add_model(page, selection, model, name, description, available, active):
        row = tk.Frame(
            page,
            bg=PANEL,
            padx=13,
            pady=8,
            highlightthickness=1,
            highlightbackground="#497b99" if active else BORDER,
        )
        row.pack(fill="x", pady=(8, 0))
        row.columnconfigure(0, weight=1)
        option = ttk.Radiobutton(
            row,
            text=name,
            value=model,
            variable=selection,
            style="ModelSettings.TRadiobutton",
        )
        option.grid(row=0, column=0, sticky="w")
        if not available:
            option.state(["disabled"])
        tk.Label(
            row,
            text="当前选用" if active else "可选" if available else "待接入",
            font=(FONT, 9),
            bg=CONTROL if active else BACKGROUND,
            fg=ACCENT if active else SECONDARY,
            padx=7,
            pady=2,
        ).grid(row=0, column=1, sticky="e")
        tk.Label(
            row,
            text=description,
            font=(FONT, 9),
            bg=PANEL,
            fg=SECONDARY,
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=(19, 0), pady=(2, 0))
