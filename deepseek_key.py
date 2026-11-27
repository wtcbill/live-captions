import ctypes
import os
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from tempfile import NamedTemporaryFile

from model_settings import BACKGROUND, FONT, SECONDARY, TEXT, action_button


KEY_FILE = Path(__file__).resolve().parent / ".runtime" / "deepseek-key.dpapi"
CRYPTPROTECT_UI_FORBIDDEN = 1
KEY_ENV = "DEEPSEEK_API_KEY"


class DataBlob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.c_void_p)]


def dpapi(data: bytes, decrypt: bool = False) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    operation.argtypes = [
        ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
    ]
    operation.restype = wintypes.BOOL
    buffer = ctypes.create_string_buffer(data)
    source = DataBlob(len(data), ctypes.addressof(buffer))
    result = DataBlob()
    try:
        if not operation(
            ctypes.byref(source), None, None, None, None,
            CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(result),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(result.data, result.size)
    finally:
        if result.data:
            if decrypt:
                ctypes.memset(result.data, 0, result.size)
            kernel32.LocalFree(result.data)
        ctypes.memset(ctypes.addressof(buffer), 0, ctypes.sizeof(buffer))


def restore_key():
    if os.getenv(KEY_ENV, "").strip():
        return
    try:
        encrypted = bytes.fromhex(KEY_FILE.read_text(encoding="ascii"))
        key = dpapi(encrypted, decrypt=True).decode("utf-16-le").strip()
    except (OSError, ValueError):
        return
    if key:
        os.environ[KEY_ENV] = key


def save_key(key: str):
    # Keep the current user's existing PowerShell SecureString file format.
    encrypted = dpapi(key.encode("utf-16-le")).hex()
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=KEY_FILE.parent, mode="w", encoding="ascii", delete=False) as file:
        temporary = Path(file.name)
        file.write(encrypted)
    try:
        temporary.replace(KEY_FILE)
    finally:
        temporary.unlink(missing_ok=True)
    os.environ[KEY_ENV] = key


def configure_key(parent=None, on_saved=None):
    window = tk.Toplevel(parent) if parent is not None else tk.Tk()
    window.title("DeepSeek API 密钥")
    window.configure(bg=BACKGROUND, padx=22, pady=18)
    window.resizable(False, False)
    if parent is not None:
        window.transient(parent)
    tk.Label(
        window, text="DeepSeek API 密钥", font=(FONT, 15, "bold"), bg=BACKGROUND, fg=TEXT,
    ).pack(anchor="w")
    key = tk.Entry(window, show="*", width=46, font=(FONT, 11))
    key.pack(fill="x", pady=(12, 6))
    status = tk.StringVar(master=window, value="密钥会用当前 Windows 账户加密保存。")
    tk.Label(
        window, textvariable=status, font=(FONT, 9), bg=BACKGROUND, fg=SECONDARY,
    ).pack(anchor="w", pady=(0, 12))

    def save():
        value = key.get().strip()
        if not value or any(not char.isascii() or char.isspace() for char in value):
            status.set("请输入有效的 DeepSeek API 密钥。")
            return
        try:
            save_key(value)
        except OSError:
            status.set("密钥保存失败，请重试。")
            return
        key.delete(0, tk.END)
        window.destroy()
        if on_saved is not None:
            on_saved()

    action_button(window, "保存密钥", save).pack(anchor="e")
    key.bind("<Return>", lambda _: save())
    key.focus_set()
    return window


if __name__ == "__main__":
    configure_key().mainloop()
