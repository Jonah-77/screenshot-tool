# -*- coding: utf-8 -*-
"""
框选截图工具

用法：
  1. 启动后屏幕出现半透明遮罩，按住鼠标左键拖出一个矩形区域
  2. 松开后该区域被红框标出，同时弹出控制面板
  3. 点击「截图」立即截取，图片存到桌面的「截图」文件夹，按 001.png 递增

双击桌面上的「截图工具」快捷方式启动即可（无控制台黑框）。
"""

import ctypes
import ctypes.wintypes
import os
import sys
import time
import traceback
import tkinter as tk
from tkinter import messagebox

from PIL import ImageGrab

MOUSEEVENTF_WHEEL = 0x0800
VK_END = 0x23          # End 键，连拍时用来停


# ── 高 DPI 适配：必须在任何窗口创建之前 ──────────────────────────────
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # Per-Monitor V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def get_desktop_dir():
    """取当前用户桌面路径（兼容 OneDrive 重定向）。"""
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)
    if buf.value and os.path.isdir(buf.value):
        return buf.value
    return os.path.join(os.path.expanduser("~"), "Desktop")


def get_virtual_screen():
    """多显示器时返回覆盖全部屏幕的虚拟矩形。"""
    u = ctypes.windll.user32
    x = u.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
    y = u.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
    w = u.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
    h = u.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
    if w <= 0 or h <= 0:
        w, h = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
        x = y = 0
    return x, y, w, h


TRANSPARENT_KEY = "#0f0e0d"   # 用作"挖空"的色键，正常界面不会出现这个颜色


class RegionFrame:
    """用红框标出已选定的区域（内部透明，不遮挡内容）。"""

    def __init__(self, master, bbox):
        self._alive = True
        self._visible = True
        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)

        l, t, r, b = bbox
        w, h = r - l, b - t
        self.top.geometry(f"{w}x{h}+{l}+{t}")
        self.top.configure(bg=TRANSPARENT_KEY)
        try:
            self.top.attributes("-transparentcolor", TRANSPARENT_KEY)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(
            self.top, bg=TRANSPARENT_KEY, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # 线宽 3，坐标内缩半个线宽，避免被窗口边缘裁掉
        self.canvas.create_rectangle(
            1, 1, w - 2, h - 2, outline="#ff2d2d", width=3)
        # 四角加粗标记，更容易看清边界
        for cx, cy, sx, sy in ((1, 1, 1, 1), (w - 2, 1, -1, 1),
                               (1, h - 2, 1, -1), (w - 2, h - 2, -1, -1)):
            self.canvas.create_line(
                cx, cy, cx + 22 * sx, cy, fill="#ff2d2d", width=4)
            self.canvas.create_line(
                cx, cy, cx, cy + 22 * sy, fill="#ff2d2d", width=4)

        self._make_click_through()

    def _make_click_through(self):
        """让红框不接收鼠标消息。

        否则滚轮会落在红框上，无法传到下面的聊天窗口。
        """
        try:
            self.top.update_idletasks()
            hwnd = int(self.top.wm_frame(), 16)
            u = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_TOOLWINDOW = 0x00000080
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(
                hwnd, GWL_EXSTYLE,
                ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def set_alpha(self, value):
        """临时调透明度。

        截图瞬间用它把红框"变透明"，而不是 withdraw ——
        窗口隐藏再显示会有明显闪烁，透明则不会。
        """
        if not self._alive:
            return
        try:
            self.top.attributes("-alpha", value)
        except Exception:
            pass

    def hide(self):
        if not self._alive:
            return
        self.top.withdraw()
        self.top.update()
        self._visible = False

    def show(self):
        if not self._alive:
            return
        self.top.deiconify()
        self.top.lift()
        self._visible = True

    @property
    def visible(self):
        return self._alive and self._visible

    def destroy(self):
        self._alive = False
        self._visible = False
        try:
            self.top.destroy()
        except Exception:
            pass


class RegionSelector:
    """全屏遮罩，用于拖拽框选区域。"""

    def __init__(self, master, on_done):
        self.master = master
        self.on_done = on_done

        vx, vy, vw, vh = get_virtual_screen()
        self.origin = (vx, vy)

        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.geometry(f"{vw}x{vh}{vx:+d}{vy:+d}")
        self.top.attributes("-topmost", True)
        self.top.attributes("-alpha", 0.28)
        self.top.configure(bg="black", cursor="crosshair")

        self.canvas = tk.Canvas(self.top, bg="black", highlightthickness=0,
                                cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        self.canvas.create_text(
            vw // 2, 48,
            text="按住鼠标左键拖出要截取的区域，松开完成    （按 Esc 退出）",
            fill="#ffffff", font=("Microsoft YaHei UI", 16))

        self.start = None
        self.rect_id = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.top.bind("<Escape>", lambda e: self._cancel())
        self.top.focus_force()

    def _on_press(self, event):
        self.start = (event.x, event.y)
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline="#ff2d2d", width=3, fill="white", stipple="gray25")

    def _on_drag(self, event):
        if self.start is None:
            return
        x0, y0 = self.start
        self.canvas.coords(self.rect_id, x0, y0, event.x, event.y)

    def _on_release(self, event):
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        left, top = min(x0, x1), min(y0, y1)
        right, bottom = max(x0, x1), max(y0, y1)

        if right - left < 5 or bottom - top < 5:
            messagebox.showwarning("区域太小", "拖出的区域太小了，请重新拖一次。")
            return

        ox, oy = self.origin
        bbox = (ox + left, oy + top, ox + right, oy + bottom)

        self.top.destroy()
        self.on_done(bbox)

    def _cancel(self):
        self.top.destroy()
        self.master.destroy()


def _round_rect_points(x1, y1, x2, y2, r):
    """圆角矩形的多边形顶点（配合 smooth=True 使用）。"""
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class RoundButton(tk.Canvas):
    """圆角按钮。

    tkinter 原生 Button 做不出圆角，用 Canvas 自绘。
    """

    def __init__(self, master, text, command, width, height,
                 bg, fg, hover_bg, radius=9, font=None, parent_bg=None):
        super().__init__(master, width=width, height=height,
                         highlightthickness=0, bd=0,
                         bg=parent_bg or master.cget("bg"))
        self._cmd = command
        self._bg, self._fg, self._hover = bg, fg, hover_bg
        self._radius = radius
        self._font = font or ("Microsoft YaHei UI", 10)
        # 注意：不能用 self._w，那是 tkinter 内部存控件路径名的属性
        self._bw, self._bh = width, height
        self._text = text
        self._flash_job = None
        self._draw(bg)
        self.configure(cursor="hand2")
        self.bind("<Button-1>", lambda e: self._draw(self._hover))
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", lambda e: self._draw(self._hover))
        self.bind("<Leave>", lambda e: self._draw(self._bg))

    def _draw(self, fill):
        self.delete("all")
        self.create_polygon(
            _round_rect_points(0, 0, self._bw - 1, self._bh - 1, self._radius),
            smooth=True, fill=fill, outline="")
        self.create_text(self._bw // 2, self._bh // 2, text=self._text,
                         fill=self._fg, font=self._font)

    def set_text(self, text):
        self._text = text
        self._draw(self._bg)

    def set_colors(self, bg, fg, hover_bg):
        self._bg, self._fg, self._hover = bg, fg, hover_bg
        self._draw(bg)

    def flash(self, color="#15a05c"):
        """成功反馈：闪一下绿再变回来。"""
        if self._flash_job:
            try:
                self.after_cancel(self._flash_job)
            except Exception:
                pass
        self._draw(color)
        self._flash_job = self.after(260, lambda: self._draw(self._bg))

    def _on_release(self, event):
        inside = 0 <= event.x <= self._bw and 0 <= event.y <= self._bh
        self._draw(self._hover if inside else self._bg)
        if inside and self._cmd:
            self._cmd()


class CapturePanel:
    """截图控制面板。"""

    def __init__(self, master, bbox, frame, on_reselect):
        self.master = master
        self.bbox = bbox
        self.frame = frame
        self.on_reselect = on_reselect
        self._busy = False
        self._auto = False
        self._last_thumb = None

        self.save_dir = os.path.join(get_desktop_dir(), "截图")
        os.makedirs(self.save_dir, exist_ok=True)

        self._notches = 3
        self.count = self._count_existing()

        # ── 配色 ───────────────────────────────────────────────
        BG = "#f5f6f8"          # 面板底色
        INK = "#1f2733"         # 主文字
        MUTED = "#7c8698"       # 次要文字
        ACCENT = "#2563eb"      # 主强调色
        BAR = "#242b3a"         # 顶部条

        self.top = tk.Toplevel(master)
        self.top.title("截图工具")
        self.top.attributes("-topmost", True)
        self.top.resizable(False, False)
        self.top.configure(bg=BG)
        self.top.overrideredirect(True)   # 去掉系统标题栏，改用自绘顶条

        # 放到选区下方；放不下就放上方。
        # 高度先给个估值，稍后按内容实际高度收紧（字体渲染随 DPI 变化，写死会裁字）。
        PW, PH = 420, 300
        x, y = bbox[0], bbox[3] + 10
        vx, vy, vw, vh = get_virtual_screen()
        if y + PH > vy + vh:
            y = max(vy + 8, bbox[1] - PH)
        x = min(max(vx + 8, x), vx + vw - PW - 8)
        self.top.geometry(f"{PW}x{PH}+{x}+{y}")

        self._drag_off = None

        # ── 顶部条（可拖动）────────────────────────────────────
        bar = tk.Frame(self.top, bg=BAR, height=38)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        title = tk.Label(bar, text="截图工具", bg=BAR, fg="#ffffff",
                         font=("Microsoft YaHei UI", 11, "bold"))
        title.pack(side="left", padx=(16, 0))

        close_x = tk.Label(bar, text="✕", bg=BAR, fg="#96a0b5",
                           font=("Microsoft YaHei UI", 11), cursor="hand2")
        close_x.pack(side="right", padx=(0, 15))
        close_x.bind("<Button-1>", lambda e: self.close())
        close_x.bind("<Enter>", lambda e: close_x.config(fg="#ff6b6b"))
        close_x.bind("<Leave>", lambda e: close_x.config(fg="#96a0b5"))

        for w in (bar, title):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        body = tk.Frame(self.top, bg=BG)
        body.pack(fill="both", expand=True)

        # ── 状态：尺寸（主角）+ 已截张数 ──────────────────────
        info = tk.Frame(body, bg=BG)
        info.pack(fill="x", padx=16, pady=(12, 0))

        self.size_label = tk.Label(info, text="", bg=BG, fg=INK,
                                   font=("Microsoft YaHei UI", 15, "bold"))
        self.size_label.pack(side="left")

        # 状态文字要短：左侧是大号尺寸，太长会和它撞在一起
        self.count_label = tk.Label(info, text="", bg=BG, fg=MUTED,
                                    font=("Microsoft YaHei UI", 8))
        self.count_label.pack(side="right", pady=(10, 0))
        self._update_info()

        # ── 主动作 ────────────────────────────────────────────
        self.cap_btn = RoundButton(
            body, "截图并下滚", self.capture, width=388, height=52,
            bg=ACCENT, fg="#ffffff", hover_bg="#1d4ed8",
            font=("Microsoft YaHei UI", 12, "bold"), parent_bg=BG)
        self.cap_btn.pack(padx=16, pady=(12, 0))

        # ── 次动作 ────────────────────────────────────────────
        row2 = tk.Frame(body, bg=BG)
        row2.pack(fill="x", padx=16, pady=(8, 0))

        self.auto_btn = RoundButton(
            row2, "自动连拍", self.toggle_auto, width=250, height=42,
            bg="#e8eeff", fg=ACCENT, hover_bg="#dbe4ff",
            font=("Microsoft YaHei UI", 10, "bold"), parent_bg=BG)
        self.auto_btn.pack(side="left")

        RoundButton(
            row2, "单张", lambda: self.capture(scroll=False), width=128, height=42,
            bg="#ffffff", fg="#4a5365", hover_bg="#e9ebef",
            font=("Microsoft YaHei UI", 10), parent_bg=BG).pack(
            side="left", padx=(10, 0))

        # ── 设置行 ────────────────────────────────────────────
        row3 = tk.Frame(body, bg=BG)
        row3.pack(fill="x", padx=16, pady=(12, 0))

        self.border_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            row3, text="红框", variable=self.border_var,
            command=self._toggle_border,
            bg=BG, fg="#4a5365", activebackground=BG,
            activeforeground="#4a5365", selectcolor="#ffffff",
            font=("Microsoft YaHei UI", 9), cursor="hand2",
            bd=0, highlightthickness=0).pack(side="left")

        tk.Label(row3, text="下滚", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(20, 8))

        RoundButton(row3, "−", lambda: self._adj_scroll(-1), width=26, height=24,
                    bg="#ffffff", fg="#4a5365", hover_bg="#e9ebef", radius=6,
                    font=("Microsoft YaHei UI", 10), parent_bg=BG).pack(side="left")

        self.scroll_label = tk.Label(row3, text="3", bg=BG, fg=INK, width=3,
                                     font=("Microsoft YaHei UI", 10, "bold"))
        self.scroll_label.pack(side="left")

        RoundButton(row3, "＋", lambda: self._adj_scroll(1), width=26, height=24,
                    bg="#ffffff", fg="#4a5365", hover_bg="#e9ebef", radius=6,
                    font=("Microsoft YaHei UI", 10), parent_bg=BG).pack(side="left")

        tk.Label(row3, text="档", bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(5, 0))

        # ── 底部文字按钮 ──────────────────────────────────────
        foot = tk.Frame(body, bg=BG)
        foot.pack(fill="x", padx=16, pady=(11, 0))
        for text, cmd in (("重选区域", self.reselect), ("打开文件夹", self.open_folder)):
            lb = tk.Label(foot, text=text, bg=BG, fg=MUTED,
                          font=("Microsoft YaHei UI", 9), cursor="hand2")
            lb.pack(side="left", padx=(0, 18))
            lb.bind("<Button-1>", lambda e, c=cmd: c())
            lb.bind("<Enter>", lambda e, w=lb: w.config(fg=ACCENT))
            lb.bind("<Leave>", lambda e, w=lb: w.config(fg=MUTED))

        self.top.protocol("WM_DELETE_WINDOW", self.close)
        self._fit_height(x, y)
        self._make_no_activate()

    def _fit_height(self, x, y):
        """按内容实际需要的高度收紧窗口，避免不同 DPI 下把底部文字裁掉。"""
        try:
            self.top.update_idletasks()
            need = self.top.winfo_reqheight()
            PW = self.top.winfo_width() or 420
            if need > 120:
                self.top.geometry(f"{PW}x{need}+{x}+{y}")
                self.top.update_idletasks()
        except Exception:
            pass

    # ── 窗口拖动（无边框窗口必须自己实现）────────────────────
    def _drag_start(self, event):
        self._drag_off = (event.x_root - self.top.winfo_x(),
                          event.y_root - self.top.winfo_y())

    def _drag_move(self, event):
        if self._drag_off is None:
            return
        nx = event.x_root - self._drag_off[0]
        ny = event.y_root - self._drag_off[1]
        # 无边框窗口一旦拖出屏幕就找不回来，限制顶栏至少留 80px 可见
        vx, vy, vw, vh = get_virtual_screen()
        pw = self.top.winfo_width() or 420
        nx = max(vx - pw + 80, min(nx, vx + vw - 80))
        ny = max(vy, min(ny, vy + vh - 40))
        self.top.geometry(f"+{nx}+{ny}")

    def _make_no_activate(self):
        """让面板不抢焦点。

        这样点按钮时微信始终保持激活状态，滚轮直接落在聊天窗口上，
        也不会因为面板获得焦点导致别的软件"失焦"。
        """
        try:
            self.top.update_idletasks()
            hwnd = int(self.top.wm_frame(), 16)
            u = ctypes.windll.user32
            GWL_EXSTYLE = -20
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TOOLWINDOW = 0x00000080
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(
                hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        except Exception:
            pass

    def _adj_scroll(self, delta):
        self._notches = max(0, min(20, self._notches + delta))
        self.scroll_label.config(text=str(self._notches))

    def close(self):
        """关闭面板 = 退出工具，红框一并清理。"""
        self._auto = False
        self.frame.destroy()
        try:
            self.top.destroy()
        except Exception:
            pass
        self.master.destroy()

    def _toggle_border(self):
        if self.border_var.get():
            self.frame.show()
        else:
            self.frame.hide()

    # ── 文件命名 ────────────────────────────────────────────────
    def _count_existing(self):
        n = 0
        for name in os.listdir(self.save_dir):
            stem, ext = os.path.splitext(name)
            if ext.lower() == ".png" and stem.isdigit():
                n = max(n, int(stem))
        return n

    def _next_path(self):
        while True:
            self.count += 1
            path = os.path.join(self.save_dir, f"{self.count:03d}.png")
            if not os.path.exists(path):
                return path

    def _update_info(self):
        l, t, r, b = self.bbox
        try:
            self.size_label.config(text=f"{r - l} × {b - t}")
            self.count_label.config(text=f"已截 {self.count} 张")
        except Exception:
            pass

    # ── 动作 ────────────────────────────────────────────────────
    def _capture_once(self):
        """隐藏遮挡 → 截图 → 存盘。返回 (path, img)；失败返回 (None, None)。"""
        panel_overlap = self._overlaps_region()
        path = self._next_path()

        # 红框必定压着选区边缘，必须藏掉；用"变透明"而不是"隐藏"，
        # 避免窗口消失再出现造成闪烁。
        self.frame.set_alpha(0.0)
        if panel_overlap:
            self.top.attributes("-alpha", 0.0)
        self.top.update()
        time.sleep(0.07)
        self.top.update()

        try:
            img = ImageGrab.grab(bbox=self.bbox, all_screens=True)
        except Exception as exc:
            messagebox.showerror("截图失败", f"抓取屏幕时出错：\n{exc}")
            img = None
        finally:
            # 红框常驻：截完立刻恢复，直到关闭程序
            if self.border_var.get():
                self.frame.set_alpha(1.0)
            else:
                self.frame.hide()
            if panel_overlap:
                self.top.attributes("-alpha", 1.0)
            self.top.lift()

        if img is None:
            return None, None

        try:
            img.save(path, "PNG")
        except Exception as exc:
            messagebox.showerror("保存失败", f"保存到 {path} 时出错：\n{exc}")
            return None, None

        self._update_info()
        return path, img

    def capture(self, scroll=True):
        if self._busy:          # 防重入：双击/连按不会出两张
            return
        self._busy = True
        mouse_origin = self._cursor_pos()

        try:
            path, _ = self._capture_once()
        except Exception:
            self._busy = False
            raise

        if path is None:        # 失败要把锁放掉，否则之后按钮全失灵
            self._busy = False
            return

        self.top.bell()
        self._flash()

        if scroll:
            self.top.after(110, lambda: self._do_scroll(mouse_origin))
        else:
            self._busy = False

    # ── 自动连拍 ────────────────────────────────────────────────
    def toggle_auto(self):
        if self._auto:
            self._stop_auto("已停止")
            return
        self._auto = True
        self._last_thumb = None
        self.auto_btn.set_text("停止连拍")
        self.auto_btn.set_colors("#e14b4b", "#ffffff", "#c93c3c")
        self._poll_end()
        self._auto_step()

    def _poll_end(self):
        """轮询 End 键。

        面板是 WS_EX_NOACTIVATE，拿不到键盘焦点，普通 bind 收不到按键，
        所以只能用 GetAsyncKeyState 全局检测。
        """
        if not self._auto:
            return
        try:
            if ctypes.windll.user32.GetAsyncKeyState(VK_END) & 0x8000:
                self._stop_auto("End 停止")
                return
        except Exception:
            pass
        self.top.after(70, self._poll_end)

    def _auto_step(self):
        if not self._auto:
            return
        if self._busy:
            self.top.after(150, self._auto_step)
            return

        self._busy = True
        mouse_origin = self._cursor_pos()
        try:
            path, img = self._capture_once()
            if img is None:
                self._stop_auto("截图出错")
                return
            self.top.bell()

            thumb = self._thumb(img)
            if self._last_thumb is not None and self._same(thumb, self._last_thumb):
                self._stop_auto("已到底部")
                return
            self._last_thumb = thumb
            self._update_info()
            self.count_label.config(text=f"连拍中 {self.count} 张")
        finally:
            self._busy = False

        # 滚一屏，然后继续下一张
        self._busy = True
        self._do_scroll(mouse_origin)
        if self._auto:
            self.top.after(220, self._auto_step)

    def _stop_auto(self, reason=""):
        self._auto = False
        self._last_thumb = None
        self.auto_btn.set_text("自动连拍")
        self.auto_btn.set_colors("#e8eeff", "#2563eb", "#dbe4ff")
        if reason:
            self.count_label.config(text=f"{reason} · {self.count} 张")

    @staticmethod
    def _thumb(img):
        return img.convert("L").resize((48, 48))

    @staticmethod
    def _same(a, b):
        """两帧缩略图是否几乎一样（用于判断滚到底了）。

        按"差异明显的像素占比"判断，而不是全图平均值——
        平均值法在只滚了一两行时容易把变化抹平，导致提前停止。
        """
        try:
            from PIL import ImageChops
            diff = ImageChops.difference(a, b)
            hist = diff.histogram()
            changed = sum(hist[13:])          # 灰度差 > 12 视为有实质变化
            return (changed / float(48 * 48)) < 0.005
        except Exception:
            return False

    def _cursor_pos(self):
        """取当前鼠标位置；取不到就返回主按钮中心，避免把鼠标甩到 (0,0)。"""
        try:
            pt = ctypes.wintypes.POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return (pt.x, pt.y)
        except Exception:
            pass
        return self._button_center()

    def _button_center(self):
        try:
            self.top.update_idletasks()
            return (self.cap_btn.winfo_rootx() + self.cap_btn.winfo_width() // 2,
                    self.cap_btn.winfo_rooty() + self.cap_btn.winfo_height() // 2)
        except Exception:
            return None

    def _do_scroll(self, mouse_origin=None):
        """在选区里模拟鼠标滚轮下滚，方便连续截取长对话。"""
        notches = int(self._notches)

        if notches <= 0:
            self._busy = False
            return

        u = ctypes.windll.user32
        point = self._scroll_point()
        panel_hidden = False

        if point is None:
            # 选区全被面板盖住才出此下策
            self.top.withdraw()
            self.top.update()
            panel_hidden = True
            l, t, r, b = self.bbox
            point = ((l + r) // 2, (t + b) // 2)

        try:
            u.SetCursorPos(int(point[0]), int(point[1]))
            time.sleep(0.05)
            for _ in range(notches):
                # 负数 = 向下滚，每档 120
                u.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, -120, 0)
                time.sleep(0.045)
            time.sleep(0.30)     # 等新内容渲染出来再允许下一张
        finally:
            # 滚动完把鼠标放回按钮上，省得每次都要手动移回来
            if mouse_origin:
                try:
                    u.SetCursorPos(int(mouse_origin[0]), int(mouse_origin[1]))
                except Exception:
                    pass
            if panel_hidden:
                self.top.deiconify()
                self.top.lift()
            self._busy = False

    def _scroll_point(self):
        """挑一个在选区内、又不被面板遮挡的点，用来接收滚轮。"""
        l, t, r, b = self.bbox
        cx, cy = (l + r) // 2, (t + b) // 2
        candidates = [
            (cx, cy),
            (cx, t + (b - t) // 4),
            (cx, t + (b - t) * 3 // 4),
            (l + (r - l) // 4, cy),
            (l + (r - l) * 3 // 4, cy),
        ]
        for x, y in candidates:
            if not self._panel_covers(x, y):
                return (x, y)
        return None

    def _panel_covers(self, x, y):
        try:
            px, py = self.top.winfo_x(), self.top.winfo_y()
            pw, ph = self.top.winfo_width(), self.top.winfo_height()
        except Exception:
            return False
        return px <= x <= px + pw and py <= y <= py + ph

    def _overlaps_region(self):
        """面板是否压在选区上（压住了截图时就必须让路）。"""
        l, t, r, b = self.bbox
        try:
            px, py = self.top.winfo_x(), self.top.winfo_y()
            pw, ph = self.top.winfo_width(), self.top.winfo_height()
        except Exception:
            return False
        return not (px + pw < l or px > r or py + ph < t or py > b)

    def _flash(self):
        """按钮闪一下绿作为截图成功反馈。"""
        try:
            self.cap_btn.flash()
        except Exception:
            pass

    def reselect(self):
        self._auto = False
        self.frame.destroy()
        self.top.destroy()
        start_selector(self.master)

    def open_folder(self):
        os.startfile(self.save_dir)


def start_selector(root):
    root.withdraw()
    RegionSelector(root, on_done=lambda bbox: _on_region(root, bbox))


def _on_region(root, bbox):
    root.withdraw()
    frame = RegionFrame(root, bbox)
    CapturePanel(root, bbox, frame, on_reselect=lambda: start_selector(root))


def _excepthook(exc_type, exc_value, exc_tb):
    """pythonw 下没有控制台，出错必须弹窗，否则什么都看不到。"""
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    try:
        messagebox.showerror("截图工具出错", msg[:2000])
    except Exception:
        pass


def main():
    sys.excepthook = _excepthook
    root = tk.Tk()
    root.title("截图工具")
    root.geometry("1x1+0+0")
    root.withdraw()
    root.after(60, lambda: start_selector(root))
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _excepthook(*sys.exc_info())
