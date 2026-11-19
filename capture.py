# -*- coding: utf-8 -*-
"""
영역 캡쳐 프로그램
 - 단축키(기본 Ctrl+Shift+A) : 화면에서 드래그로 영역 지정 → 캡쳐
 - 사진\\캡쳐 폴더에 저장 (고화질 PNG / 저용량 JPG)
 - 클립보드에 '파일'과 '이미지'를 동시에 복사
     · 탐색기/바탕화면에서 Ctrl+V 또는 우클릭→붙여넣기 : 파일로 들어감
     · 카톡/메신저/그림판/엑셀에서 Ctrl+V : 이미지로 들어감
 - 캡쳐 화면 : Esc/우클릭 = 취소, 1 = 고화질, 2 = 저용량
 - 화면 오른쪽 아래 트레이 아이콘 우클릭 : 캡쳐 / 화질 / 단축키 변경 / 폴더 열기 / 종료
 - 단축키가 다른 프로그램과 겹치면 다른 단축키를 고르는 창이 뜸
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import io
import json
import os
import queue
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import Image, ImageDraw, ImageEnhance, ImageGrab, ImageTk

try:
    import pystray
except Exception:
    pystray = None

# ───────── 설정 ─────────
SUBFOLDER = "캡쳐"            # 사진 폴더 안의 하위 폴더 이름 ("" 로 두면 사진 폴더에 바로 저장)
LOW_JPG_QUALITY = 70          # 저용량 모드 JPG 품질 (1~95, 낮을수록 용량 작음)
DEFAULT_HOTKEY = {"mods": 0x0002 | 0x0004, "key": "A"}  # Ctrl + Shift + A
# ───────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(BASE_DIR, "capture.pid")
SETTINGS_FILE = os.path.join(BASE_DIR, "settings.json")
MODES = {"high": "고화질 (PNG 원본)", "low": "저용량 (JPG)"}

MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
KEYS = ([chr(c) for c in range(ord("A"), ord("Z") + 1)] + [str(i) for i in range(10)]
        + [f"F{i}" for i in range(1, 13)] + ["PrintScreen"])


def key_to_vk(key):
    if key == "PrintScreen":
        return 0x2C
    if key.startswith("F") and len(key) > 1:
        return 0x6F + int(key[1:])
    return ord(key)


def hotkey_label(hk):
    m = hk["mods"]
    parts = [n for f, n in ((MOD_CTRL, "Ctrl"), (MOD_SHIFT, "Shift"), (MOD_ALT, "Alt"), (MOD_WIN, "Win")) if m & f]
    return "+".join(parts + [hk["key"]])


# ───────── 설정 파일 ─────────
def load_settings():
    s = {"mode": "high", "hotkey": dict(DEFAULT_HOTKEY)}
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("mode") in MODES:
            s["mode"] = data["mode"]
        hk = data.get("hotkey")
        if isinstance(hk, dict) and hk.get("key") in KEYS and isinstance(hk.get("mods"), int):
            s["hotkey"] = {"mods": hk["mods"], "key": hk["key"]}
    except Exception:
        pass
    return s


def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
    except Exception:
        pass


# 고해상도(배율 125%, 150% 등) 화면에서도 좌표가 정확하도록
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

k32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
k32.GlobalAlloc.restype = wt.HGLOBAL
k32.GlobalLock.argtypes = [wt.HGLOBAL]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalUnlock.argtypes = [wt.HGLOBAL]
k32.GlobalFree.argtypes = [wt.HGLOBAL]
k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
k32.CreateMutexW.restype = wt.HANDLE
u32.OpenClipboard.argtypes = [wt.HWND]
u32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
u32.SetClipboardData.restype = wt.HANDLE
u32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
u32.RegisterClipboardFormatW.restype = wt.UINT
u32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
u32.UnregisterHotKey.argtypes = [wt.HWND, ctypes.c_int]
u32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
u32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.GetParent.argtypes = [wt.HWND]
u32.GetParent.restype = wt.HWND
u32.SetForegroundWindow.argtypes = [wt.HWND]


# ───────── 사진 폴더 찾기 (OneDrive로 옮겨진 경우도 정확히) ─────────
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def pictures_dir():
    try:
        folder_id = GUID(0x33E28130, 0x4E1E, 0x4676,
                         (ctypes.c_ubyte * 8)(0x83, 0x5A, 0x98, 0x39, 0x5C, 0x3B, 0xC3, 0xBB))
        out = ctypes.c_wchar_p()
        fn = ctypes.windll.shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(GUID), wt.DWORD, wt.HANDLE, ctypes.POINTER(ctypes.c_wchar_p)]
        if fn(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0 and out.value:
            return out.value
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Pictures")


def save_dir():
    d = os.path.join(pictures_dir(), SUBFOLDER) if SUBFOLDER else pictures_dir()
    os.makedirs(d, exist_ok=True)
    return d


# ───────── 클립보드 (파일 + 이미지 동시) ─────────
def _to_hglobal(data: bytes):
    h = k32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    p = k32.GlobalLock(h)
    ctypes.memmove(p, data, len(data))
    k32.GlobalUnlock(h)
    return h


def copy_to_clipboard(hwnd, path, img):
    files = (os.path.abspath(path) + "\0\0").encode("utf-16-le")
    dropfiles = struct.pack("<IiiII", 20, 0, 0, 0, 1) + files
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    dib = buf.getvalue()[14:]
    effect = struct.pack("<I", 1)  # 붙여넣기 = 복사

    for _ in range(20):
        if u32.OpenClipboard(hwnd):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        u32.EmptyClipboard()
        fmt_effect = u32.RegisterClipboardFormatW("Preferred DropEffect")
        for fmt, data in ((15, dropfiles), (8, dib), (fmt_effect, effect)):
            h = _to_hglobal(data)
            if not u32.SetClipboardData(fmt, h):
                k32.GlobalFree(h)
    finally:
        u32.CloseClipboard()
    return True


# ───────── 단축키 감시 스레드 ─────────
class HotkeyThread(threading.Thread):
    def __init__(self, hk, q):
        super().__init__(daemon=True)
        self.hk, self.q = hk, q
        self.tid = None
        self.ready = threading.Event()

    def run(self):
        self.tid = k32.GetCurrentThreadId()
        ok = u32.RegisterHotKey(None, 1, self.hk["mods"] | 0x4000, key_to_vk(self.hk["key"]))
        self.ready.set()
        if not ok:
            self.q.put(("hotkey_fail", self))
            return
        self.q.put(("hotkey_ok", self))
        msg = wt.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                self.q.put(("capture", None))
        u32.UnregisterHotKey(None, 1)

    def stop(self):
        self.ready.wait(2)
        if self.tid and self.is_alive():
            u32.PostThreadMessageW(self.tid, 0x0012, 0, 0)  # WM_QUIT
            self.join(2)


# ───────── 트레이 아이콘 그림 ─────────
def make_icon():
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(37, 99, 235, 255))
    for x0, y0, x1, y1 in ((14, 14, 28, 18), (14, 14, 18, 28), (36, 14, 50, 18), (46, 14, 50, 28),
                           (14, 46, 28, 50), (14, 36, 18, 50), (36, 46, 50, 50), (46, 36, 50, 50)):
        d.rectangle((x0, y0, x1, y1), fill="white")
    d.ellipse((26, 26, 38, 38), fill="white")
    return im


# ───────── 메인 앱 ─────────
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.q = queue.Queue()
        self.busy = False
        self.settings = load_settings()
        self.mode = self.settings["mode"]
        self.active_hk = None      # 현재 등록된 단축키 스레드
        self.trying_hk = None      # 등록 시도 중인 스레드
        self.dialog = None
        self.icon = None
        self.try_hotkey(self.settings["hotkey"])
        self.start_tray()
        with open(PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        self.root.after(100, self.poll)

    def hwnd(self):
        h = self.root.winfo_id()
        return u32.GetParent(h) or h

    # ── 단축키 ──
    def try_hotkey(self, hk, restore=False):
        old = self.active_hk
        if old:
            old.stop()
            self.active_hk = None
        t = HotkeyThread(dict(hk), self.q)
        self.trying_hk = (t, old, restore)
        t.start()

    def hk_text(self):
        return hotkey_label(self.active_hk.hk) if self.active_hk else "단축키 없음"

    # ── 트레이 ──
    def start_tray(self):
        if pystray is None:
            return
        put = self.q.put
        M = pystray.MenuItem
        menu = pystray.Menu(
            M(lambda item: f"캡쳐하기  ({self.hk_text()})", lambda: put(("capture_tray", None)), default=True),
            pystray.Menu.SEPARATOR,
            M(MODES["high"], lambda: put(("mode", "high")), checked=lambda i: self.mode == "high", radio=True),
            M(MODES["low"], lambda: put(("mode", "low")), checked=lambda i: self.mode == "low", radio=True),
            pystray.Menu.SEPARATOR,
            M("단축키 변경...", lambda: put(("hotkey_dialog", None))),
            M("저장 폴더 열기", lambda: put(("open_folder", None))),
            pystray.Menu.SEPARATOR,
            M("종료", lambda: put(("quit", None))),
        )
        self.icon = pystray.Icon("capture", make_icon(), "캡쳐 프로그램", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def update_tray(self):
        if self.icon:
            try:
                self.icon.title = f"캡쳐 프로그램 ({self.hk_text()} · {'고화질' if self.mode == 'high' else '저용량'})"
                self.icon.update_menu()
            except Exception:
                pass

    # ── 메시지 처리 ──
    def poll(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "hotkey_ok":
                    self.on_hotkey_ok(data)
                elif kind == "hotkey_fail":
                    self.on_hotkey_fail(data)
                elif kind == "capture" and not self.busy:
                    self.start_capture()
                elif kind == "capture_tray" and not self.busy:
                    self.root.after(300, self.start_capture)  # 메뉴가 닫힐 시간
                elif kind == "mode":
                    self.set_mode(data)
                elif kind == "hotkey_dialog":
                    self.open_hotkey_dialog()
                elif kind == "open_folder":
                    os.startfile(save_dir())
                elif kind == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def on_hotkey_ok(self, t):
        if not self.trying_hk or self.trying_hk[0] is not t:
            return
        restore = self.trying_hk[2]
        self.trying_hk = None
        self.active_hk = t
        self.settings["hotkey"] = t.hk
        save_settings(self.settings)
        self.update_tray()
        if restore:  # 원래 단축키로 되돌린 경우: 설정 창은 그대로 둠
            return
        if self.dialog:
            self.dialog.destroy()
            self.dialog = None
        self.toast(f"캡쳐 프로그램 실행 중\n{hotkey_label(t.hk)} 로 캡쳐  ·  트레이 아이콘 우클릭 = 메뉴/종료")

    def on_hotkey_fail(self, t):
        if not self.trying_hk or self.trying_hk[0] is not t:
            return
        _, old, restore = self.trying_hk
        self.trying_hk = None
        if restore:
            self.update_tray()
            return
        msg = f"{hotkey_label(t.hk)} 는 다른 프로그램이 이미 쓰고 있습니다.\n다른 단축키를 골라 주세요."
        if old:  # 바꾸다 실패하면 원래 단축키로 되돌림
            self.try_hotkey(old.hk, restore=True)
        self.open_hotkey_dialog(error=msg, suggest=t.hk)

    # ── 단축키 변경 창 ──
    def open_hotkey_dialog(self, error=None, suggest=None):
        if self.dialog:
            self.dialog.destroy()
        hk = suggest or (self.active_hk.hk if self.active_hk else self.settings["hotkey"])
        d = tk.Toplevel(self.root)
        self.dialog = d
        d.title("캡쳐 프로그램 - 단축키 설정")
        d.resizable(False, False)
        d.attributes("-topmost", True)
        f = tk.Frame(d, padx=20, pady=16)
        f.pack()
        tk.Label(f, text="캡쳐 단축키 설정", font=("맑은 고딕", 12, "bold")).grid(row=0, column=0, columnspan=5, sticky="w")
        err = tk.Label(f, text=error or f"현재: {self.hk_text()}", fg="#b91c1c" if error else "#374151",
                       font=("맑은 고딕", 10), justify="left")
        err.grid(row=1, column=0, columnspan=5, sticky="w", pady=(4, 12))

        vars_ = {}
        for col, (flag, name) in enumerate(((MOD_CTRL, "Ctrl"), (MOD_SHIFT, "Shift"), (MOD_ALT, "Alt"), (MOD_WIN, "Win"))):
            v = tk.BooleanVar(value=bool(hk["mods"] & flag))
            vars_[flag] = v
            tk.Checkbutton(f, text=name, variable=v, font=("맑은 고딕", 10)).grid(row=2, column=col, sticky="w")
        tk.Label(f, text="+ 키:", font=("맑은 고딕", 10)).grid(row=3, column=0, sticky="w", pady=(10, 0))
        key_var = tk.StringVar(value=hk["key"])
        ttk.Combobox(f, textvariable=key_var, values=KEYS, state="readonly", width=12).grid(
            row=3, column=1, columnspan=2, sticky="w", pady=(10, 0))
        tk.Label(f, text="예) Ctrl+Shift+S, Ctrl+Alt+A, Shift+PrintScreen 등",
                 fg="#6b7280", font=("맑은 고딕", 9)).grid(row=4, column=0, columnspan=5, sticky="w", pady=(8, 0))

        def apply():
            mods = sum(flag for flag, v in vars_.items() if v.get())
            key = key_var.get()
            if not mods and not key.startswith("F") and key != "PrintScreen":
                err.configure(text="Ctrl / Shift / Alt / Win 중 하나 이상을 같이 선택해 주세요.", fg="#b91c1c")
                return
            new = {"mods": mods, "key": key}
            if self.active_hk and self.active_hk.hk == new:
                close()
                return
            err.configure(text="등록 중...", fg="#374151")
            self.try_hotkey(new)

        def close():
            if self.dialog is d:
                self.dialog = None
            d.destroy()
            if not self.active_hk and not self.trying_hk:
                self.toast("단축키 없이 실행 중입니다.\n트레이 아이콘 우클릭 → 캡쳐하기 / 단축키 변경")

        bf = tk.Frame(f)
        bf.grid(row=5, column=0, columnspan=5, sticky="e", pady=(16, 0))
        tk.Button(bf, text="적용", width=10, command=apply, bg="#2563eb", fg="white",
                  relief="flat").pack(side="left", padx=4)
        tk.Button(bf, text="닫기", width=10, command=close, relief="flat", bg="#e5e7eb").pack(side="left")
        d.protocol("WM_DELETE_WINDOW", close)
        d.update_idletasks()
        sw, sh = d.winfo_screenwidth(), d.winfo_screenheight()
        d.geometry(f"+{(sw - d.winfo_width()) // 2}+{(sh - d.winfo_height()) // 3}")
        d.after(50, lambda: (d.lift(), d.focus_force()))

    # ── 영역 선택 화면 ──
    def start_capture(self):
        if self.busy:
            return
        self.busy = True
        gm = u32.GetSystemMetrics
        self.vx, self.vy, vw, vh = gm(76), gm(77), gm(78), gm(79)  # 모든 모니터를 합친 영역
        try:
            self.shot = ImageGrab.grab(all_screens=True)
        except Exception:
            self.shot = ImageGrab.grab()
            self.vx, self.vy = 0, 0
            vw, vh = self.shot.size

        dark = ImageEnhance.Brightness(self.shot).enhance(0.55)
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.geometry(f"{vw}x{vh}+{self.vx}+{self.vy}")
        c = tk.Canvas(top, width=vw, height=vh, highlightthickness=0, cursor="crosshair", bg="black")
        c.pack()
        self._dark_img = ImageTk.PhotoImage(dark)
        c.create_image(0, 0, anchor="nw", image=self._dark_img)

        px = -self.vx + gm(0) // 2
        py = -self.vy + 40
        c.create_text(px, py, text="드래그해서 캡쳐할 영역 선택   ·   Esc / 우클릭 = 취소",
                      fill="white", font=("맑은 고딕", 13, "bold"))
        self.mode_item = c.create_text(px, py + 30, text="", fill="#ffd54f", font=("맑은 고딕", 12, "bold"))

        self.top, self.c = top, c
        self.update_mode_text()
        self.start = None
        self.bright_item = self.rect_item = self.size_item = None
        self._bright_img = None

        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<ButtonPress-3>", lambda e: self.close_overlay())
        for w in (top, c):
            w.bind("<Escape>", lambda e: self.close_overlay())
            w.bind("1", lambda e: self.set_mode("high"))
            w.bind("2", lambda e: self.set_mode("low"))
            w.bind("<Tab>", lambda e: self.set_mode("low" if self.mode == "high" else "high"))

        def grab_focus():
            try:
                top.lift()
                top.focus_force()
                c.focus_set()
                u32.SetForegroundWindow(u32.GetParent(top.winfo_id()) or top.winfo_id())
            except Exception:
                pass
        top.after(20, grab_focus)

    def update_mode_text(self):
        if getattr(self, "c", None) is None or not self.busy:
            return
        other = "2 = 저용량" if self.mode == "high" else "1 = 고화질"
        self.c.itemconfigure(self.mode_item, text=f"현재: {MODES[self.mode]}     (바꾸기: {other})")

    def set_mode(self, mode):
        self.mode = mode
        self.settings["mode"] = mode
        save_settings(self.settings)
        self.update_mode_text()
        self.update_tray()
        return "break"

    def _box(self, e):
        x0, y0 = self.start
        x1, y1 = e.x, e.y
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

    def on_press(self, e):
        self.start = (e.x, e.y)

    def on_drag(self, e):
        if not self.start:
            return
        x0, y0, x1, y1 = self._box(e)
        c = self.c
        for item in (self.bright_item, self.rect_item, self.size_item):
            if item:
                c.delete(item)
        if x1 - x0 > 1 and y1 - y0 > 1:
            self._bright_img = ImageTk.PhotoImage(self.shot.crop((x0, y0, x1, y1)))
            self.bright_item = c.create_image(x0, y0, anchor="nw", image=self._bright_img)
        self.rect_item = c.create_rectangle(x0, y0, x1, y1, outline="#3fa9ff", width=2)
        ty = y0 - 14 if y0 > 30 else y1 + 14
        self.size_item = c.create_text(x0 + 4, ty, anchor="w", text=f"{x1 - x0} × {y1 - y0}",
                                       fill="white", font=("맑은 고딕", 10, "bold"))

    def on_release(self, e):
        if not self.start:
            return
        x0, y0, x1, y1 = self._box(e)
        img = self.shot.crop((x0, y0, x1, y1)) if (x1 - x0 >= 3 and y1 - y0 >= 3) else None
        self.close_overlay()
        if img is None:
            return
        try:
            d = save_dir()
            name = f"캡쳐_{datetime.datetime.now():%Y%m%d_%H%M%S}"
            ext = ".jpg" if self.mode == "low" else ".png"
            path = os.path.join(d, name + ext)
            n = 2
            while os.path.exists(path):
                path = os.path.join(d, f"{name}_{n}{ext}")
                n += 1
            if self.mode == "low":
                img.convert("RGB").save(path, "JPEG", quality=LOW_JPG_QUALITY, optimize=True)
            else:
                img.save(path, "PNG")
            ok = copy_to_clipboard(self.hwnd(), path, img)
            kb = os.path.getsize(path) / 1024
            size = f"{kb:,.0f} KB" if kb < 1024 else f"{kb / 1024:,.1f} MB"
            label = "고화질" if self.mode == "high" else "저용량"
            msg = f"캡쳐 저장 + 복사 완료 ({label} · {size})\n{os.path.basename(path)}"
            if not ok:
                msg = f"저장은 됐지만 복사 실패\n{os.path.basename(path)}"
            self.toast(msg)
        except Exception as ex:
            self.toast(f"캡쳐 실패: {ex}")

    def close_overlay(self):
        try:
            self.top.destroy()
        except Exception:
            pass
        self.shot = None
        self.c = None
        self._dark_img = self._bright_img = None
        self.busy = False

    # ── 오른쪽 아래 알림 ──
    def toast(self, text):
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        try:
            t.attributes("-alpha", 0.93)
        except Exception:
            pass
        tk.Label(t, text=text, bg="#1f2937", fg="white", font=("맑은 고딕", 10),
                 padx=16, pady=10, justify="left").pack()
        t.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        t.geometry(f"+{sw - t.winfo_width() - 24}+{sh - t.winfo_height() - 70}")
        t.after(2500, t.destroy)

    # ── 종료 ──
    def quit(self):
        try:
            if self.icon:
                self.icon.stop()
        except Exception:
            pass
        try:
            if self.active_hk:
                self.active_hk.stop()
        except Exception:
            pass
        try:
            os.remove(PID_FILE)
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("윈도우 전용 프로그램입니다.")
        sys.exit(1)
    # 이미 실행 중이면 두 번 켜지지 않도록
    _k = ctypes.WinDLL("kernel32", use_last_error=True)
    _k.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    _k.CreateMutexW.restype = wt.HANDLE
    _mutex = _k.CreateMutexW(None, False, "Local\\water-color-korea-screenshot")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        r = tk.Tk()
        r.withdraw()
        messagebox.showinfo("캡쳐 프로그램",
                            "캡쳐 프로그램이 이미 실행 중입니다.\n\n"
                            "화면 오른쪽 아래 트레이 아이콘(파란 네모)을 우클릭하면\n"
                            "단축키 변경이나 종료를 할 수 있습니다.\n"
                            "(아이콘이 안 보이면 작업표시줄의 ^ 를 눌러 보세요)")
        sys.exit(0)
    App().run()
