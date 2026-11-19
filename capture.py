# -*- coding: utf-8 -*-
"""
영역 캡쳐 프로그램
 - Ctrl+Shift+A : 화면에서 드래그로 영역 지정 → 캡쳐
 - 사진\\캡쳐 폴더에 PNG로 저장
 - 클립보드에 '파일'과 '이미지'를 동시에 복사
     · 탐색기/바탕화면에서 Ctrl+V 또는 우클릭→붙여넣기 : 파일로 들어감
     · 카톡/메신저/그림판/엑셀에서 Ctrl+V : 이미지로 들어감
 - 취소 : Esc 또는 마우스 오른쪽 클릭
"""
import ctypes
import ctypes.wintypes as wt
import datetime
import io
import os
import queue
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox

from PIL import ImageEnhance, ImageGrab, ImageTk

# ───────── 설정 ─────────
SUBFOLDER = "캡쳐"          # 사진 폴더 안의 하위 폴더 이름 ("" 로 두면 사진 폴더에 바로 저장)
HOTKEY_MOD = 0x0002 | 0x0004  # Ctrl + Shift
HOTKEY_VK = ord("A")          # A
# ───────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(BASE_DIR, "capture.pid")

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
u32.OpenClipboard.argtypes = [wt.HWND]
u32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
u32.SetClipboardData.restype = wt.HANDLE
u32.RegisterClipboardFormatW.argtypes = [wt.LPCWSTR]
u32.RegisterClipboardFormatW.restype = wt.UINT
u32.RegisterHotKey.argtypes = [wt.HWND, ctypes.c_int, wt.UINT, wt.UINT]
u32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
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
        if fn(ctypes.byref(folder_id), 0, None, ctypes.byref(out)) == 0:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
            ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, ctypes.c_void_p))
            if path:
                return path
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
    # CF_HDROP : 탐색기에 '파일'로 붙여넣기
    files = (os.path.abspath(path) + "\0\0").encode("utf-16-le")
    dropfiles = struct.pack("<IiiII", 20, 0, 0, 0, 1) + files
    # CF_DIB : 메신저/그림판 등에 '이미지'로 붙여넣기
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    dib = buf.getvalue()[14:]
    # 붙여넣을 때 '이동'이 아니라 '복사'가 되도록
    effect = struct.pack("<I", 1)

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


# ───────── 단축키 감시 (별도 스레드) ─────────
def hotkey_thread(q):
    if not u32.RegisterHotKey(None, 1, HOTKEY_MOD | 0x4000, HOTKEY_VK):  # 0x4000 = 꾹 누를 때 반복 방지
        q.put("fail")
        return
    q.put("ok")
    msg = wt.MSG()
    while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            q.put("capture")


# ───────── 메인 앱 ─────────
class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.q = queue.Queue()
        self.busy = False
        threading.Thread(target=hotkey_thread, args=(self.q,), daemon=True).start()
        self.root.after(100, self.poll)

    def hwnd(self):
        h = self.root.winfo_id()
        return u32.GetParent(h) or h

    def poll(self):
        try:
            while True:
                m = self.q.get_nowait()
                if m == "fail":
                    messagebox.showwarning(
                        "캡쳐 프로그램",
                        "Ctrl+Shift+A 단축키를 등록하지 못했습니다.\n\n"
                        "· 캡쳐 프로그램이 이미 실행 중이거나\n"
                        "· 다른 프로그램이 같은 단축키를 쓰고 있습니다.")
                    self.root.destroy()
                    return
                if m == "ok":
                    with open(PID_FILE, "w") as f:
                        f.write(str(os.getpid()))
                    self.toast("캡쳐 프로그램 실행 중\nCtrl+Shift+A 로 캡쳐")
                if m == "capture" and not self.busy:
                    self.start_capture()
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    # ── 영역 선택 화면 ──
    def start_capture(self):
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

        # 안내 문구 (주 모니터 상단 가운데)
        px = -self.vx + gm(0) // 2
        py = -self.vy + 40
        c.create_text(px, py, text="드래그해서 캡쳐할 영역 선택   ·   Esc / 우클릭 = 취소",
                      fill="white", font=("맑은 고딕", 13, "bold"))

        self.top, self.c = top, c
        self.start = None
        self.bright_item = self.rect_item = self.size_item = None
        self._bright_img = None

        c.bind("<ButtonPress-1>", self.on_press)
        c.bind("<B1-Motion>", self.on_drag)
        c.bind("<ButtonRelease-1>", self.on_release)
        c.bind("<ButtonPress-3>", lambda e: self.close_overlay())
        top.bind("<Escape>", lambda e: self.close_overlay())
        c.bind("<Escape>", lambda e: self.close_overlay())

        def grab_focus():
            try:
                top.lift()
                top.focus_force()
                c.focus_set()
                u32.SetForegroundWindow(u32.GetParent(top.winfo_id()) or top.winfo_id())
            except Exception:
                pass
        top.after(20, grab_focus)

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
            path = os.path.join(d, name + ".png")
            n = 2
            while os.path.exists(path):
                path = os.path.join(d, f"{name}_{n}.png")
                n += 1
            img.save(path, "PNG")
            ok = copy_to_clipboard(self.hwnd(), path, img)
            msg = f"캡쳐 저장 + 복사 완료\n{os.path.basename(path)}"
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
        t.after(1800, t.destroy)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if sys.platform != "win32":
        print("윈도우 전용 프로그램입니다.")
        sys.exit(1)
    App().run()
