from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from PIL import Image
from tkinter import messagebox
from playwright.async_api import async_playwright

from app.main import run


APP_NAME = "SBS Spark"
YELLOW = "#FFBE0B"
YELLOW_HOVER = "#E5A900"
INK = "#141414"
SURFACE = "#FFFFFF"
CANVAS = "#F4F4F2"
MUTED = "#707070"
BORDER = "#E6E6E2"
GREEN = "#20A464"
RED = "#E94B4B"

DATA_DIR = Path(
    os.getenv("LOCALAPPDATA")
    or (
        Path.home() / "Library/Application Support"
        if platform.system() == "Darwin"
        else Path.home() / ".local/share"
    )
) / "SBS-Spark"
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "storage-state.json"
ARTIFACTS_DIR = DATA_DIR / "artifacts"
SETTINGS_PATH = DATA_DIR / "desktop-settings.json"
QR_PATH = DATA_DIR / "login-qr.png"

DEFAULT_CONFIG = {
    "friends": ["好友昵称"],
    "messages": [{"type": "text", "value": "今天也要开心呀 ✨"}],
    "send_interval_seconds": {"min": 15, "max": 30},
    "prevent_duplicates": True,
    "continue_on_error": False,
    "target_open_retries": 1,
    "target_open_timeout_seconds": 15,
}


class DesktopApp:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.busy = False
        self.last_schedule_day = ""
        self.login_window: ctk.CTkToplevel | None = None
        self.qr_label: ctk.CTkLabel | None = None
        self.qr_image = None

        self._prepare_runtime()
        self._build_ui()
        self._load_config()
        self._load_settings()
        self._refresh_login_state()
        threading.Thread(target=self._scheduler_loop, daemon=True).start()

    def _prepare_runtime(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        os.chdir(DATA_DIR)
        os.environ["TASK_CONFIG"] = str(CONFIG_PATH)
        os.environ["ARTIFACTS_DIR"] = str(ARTIFACTS_DIR)
        os.environ["DOUYIN_STORAGE_STATE"] = str(STATE_PATH)
        os.environ.setdefault("HEADLESS", "true")
        if getattr(sys, "frozen", False):
            executable_dir = Path(sys.executable).resolve().parent
            browser_dir = (
                executable_dir.parent / "Resources" / "ms-playwright"
                if platform.system() == "Darwin"
                else executable_dir / "ms-playwright"
            )
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browser_dir)
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(
                json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _build_ui(self):
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")
        self.root.title(APP_NAME)
        self.root.geometry("980x720")
        self.root.minsize(900, 660)
        self.root.configure(fg_color=CANVAS)

        shell = ctk.CTkFrame(self.root, fg_color=CANVAS, corner_radius=0)
        shell.pack(fill="both", expand=True)

        header = ctk.CTkFrame(shell, fg_color=INK, height=86, corner_radius=0)
        header.pack(fill="x")
        header.pack_propagate(False)

        logo = ctk.CTkFrame(header, width=48, height=48, fg_color=YELLOW, corner_radius=15)
        logo.pack(side="left", padx=(30, 14), pady=19)
        logo.pack_propagate(False)
        ctk.CTkLabel(
            logo, text="S", text_color=INK, font=ctk.CTkFont(size=24, weight="bold")
        ).pack(expand=True)

        title_wrap = ctk.CTkFrame(header, fg_color="transparent")
        title_wrap.pack(side="left", pady=18)
        ctk.CTkLabel(
            title_wrap,
            text="SBS Spark",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=21, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_wrap,
            text="轻量、清晰的好友互动管理",
            text_color="#AFAFAF",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(1, 0))

        self.run_badge = ctk.CTkLabel(
            header,
            text="●  空闲",
            width=100,
            height=34,
            corner_radius=17,
            fg_color="#2B2B2B",
            text_color="#D9D9D9",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.run_badge.pack(side="right", padx=30)

        body = ctk.CTkScrollableFrame(
            shell, fg_color=CANVAS, scrollbar_button_color="#D2D2CD"
        )
        body.pack(fill="both", expand=True, padx=24, pady=20)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        login_card = self._card(body, 0, 0, "01", "账号登录", "扫码后登录状态仅保存在本机")
        login_row = ctk.CTkFrame(login_card, fg_color="transparent")
        login_row.pack(fill="x", padx=22, pady=(4, 22))
        self.login_dot = ctk.CTkLabel(
            login_row, text="●", width=18, text_color=MUTED, font=ctk.CTkFont(size=17)
        )
        self.login_dot.pack(side="left")
        self.login_text = ctk.CTkLabel(
            login_row,
            text="未登录",
            text_color=INK,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.login_text.pack(side="left", padx=(4, 0))
        self.login_button = ctk.CTkButton(
            login_row,
            text="扫码登录",
            width=108,
            height=38,
            corner_radius=12,
            fg_color=YELLOW,
            hover_color=YELLOW_HOVER,
            text_color=INK,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.start_qr_login,
        )
        self.login_button.pack(side="right")
        self.logout_button = ctk.CTkButton(
            login_row,
            text="退出",
            width=60,
            height=38,
            corner_radius=12,
            fg_color="#EFEFED",
            hover_color="#E2E2DE",
            text_color=INK,
            command=self.logout,
        )
        self.logout_button.pack(side="right", padx=(0, 8))

        target_card = self._card(body, 0, 1, "02", "互动对象", "填写聊天列表中显示的完整备注或昵称")
        self.friends = ctk.CTkTextbox(
            target_card,
            height=112,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
            fg_color="#FAFAF8",
            text_color=INK,
            font=ctk.CTkFont(size=14),
        )
        self.friends.pack(fill="x", padx=22, pady=(4, 10))
        ctk.CTkLabel(
            target_card,
            text="每行一个，建议控制在 10 人以内",
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=24, pady=(0, 18))

        message_card = self._card(body, 1, 0, "03", "发送内容", "每天随机选择一条，不需要编辑配置文件")
        self.messages = ctk.CTkTextbox(
            message_card,
            height=124,
            corner_radius=12,
            border_width=1,
            border_color=BORDER,
            fg_color="#FAFAF8",
            text_color=INK,
            font=ctk.CTkFont(size=14),
        )
        self.messages.pack(fill="x", padx=22, pady=(4, 10))
        ctk.CTkLabel(
            message_card,
            text="每行一条。避免广告、重复营销或大量相同内容",
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=24, pady=(0, 18))

        schedule_card = self._card(body, 1, 1, "04", "运行设置", "电脑开机且软件运行时按计划执行")
        schedule_row = ctk.CTkFrame(schedule_card, fg_color="transparent")
        schedule_row.pack(fill="x", padx=22, pady=(6, 12))
        ctk.CTkLabel(
            schedule_row, text="每天运行时间", text_color=INK, font=ctk.CTkFont(size=14)
        ).pack(side="left")
        self.schedule_time = ctk.StringVar(value="21:00")
        self.time_entry = ctk.CTkEntry(
            schedule_row,
            textvariable=self.schedule_time,
            width=86,
            height=38,
            corner_radius=11,
            border_color=BORDER,
            fg_color="#FAFAF8",
            justify="center",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.time_entry.pack(side="right")

        switch_row = ctk.CTkFrame(schedule_card, fg_color="#FAFAF8", corner_radius=12)
        switch_row.pack(fill="x", padx=22, pady=(0, 20))
        ctk.CTkLabel(
            switch_row,
            text="启用自动运行",
            text_color=INK,
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(side="left", padx=14, pady=13)
        self.schedule_enabled = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            switch_row,
            text="",
            variable=self.schedule_enabled,
            width=44,
            progress_color=GREEN,
            button_color="#FFFFFF",
            button_hover_color="#FFFFFF",
        ).pack(side="right", padx=14)

        footer = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=18, border_width=1, border_color=BORDER)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=7, pady=(8, 12))
        footer.grid_columnconfigure(1, weight=1)

        self.save_button = ctk.CTkButton(
            footer,
            text="保存设置",
            width=112,
            height=44,
            corner_radius=13,
            fg_color="#EFEFED",
            hover_color="#E2E2DE",
            text_color=INK,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.save_config,
        )
        self.save_button.grid(row=0, column=0, padx=(18, 10), pady=17)

        self.activity_text = ctk.CTkLabel(
            footer,
            text="尚未运行",
            text_color=MUTED,
            anchor="w",
            font=ctk.CTkFont(size=12),
        )
        self.activity_text.grid(row=0, column=1, sticky="ew", padx=6)

        self.test_button = ctk.CTkButton(
            footer,
            text="安全检查",
            width=108,
            height=44,
            corner_radius=13,
            fg_color="#242424",
            hover_color="#383838",
            text_color="#FFFFFF",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self.start_run(True),
        )
        self.test_button.grid(row=0, column=2, padx=8)

        self.run_button = ctk.CTkButton(
            footer,
            text="开始运行",
            width=120,
            height=44,
            corner_radius=13,
            fg_color=YELLOW,
            hover_color=YELLOW_HOVER,
            text_color=INK,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=lambda: self.start_run(False),
        )
        self.run_button.grid(row=0, column=3, padx=(0, 18))

    def _card(self, parent, row: int, column: int, number: str, title: str, subtitle: str):
        card = ctk.CTkFrame(
            parent, fg_color=SURFACE, corner_radius=18, border_width=1, border_color=BORDER
        )
        card.grid(row=row, column=column, sticky="nsew", padx=7, pady=7)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=22, pady=(19, 12))
        ctk.CTkLabel(
            top,
            text=number,
            width=35,
            height=25,
            corner_radius=9,
            fg_color="#FFF4CC",
            text_color="#8A6300",
            font=ctk.CTkFont(size=11, weight="bold"),
        ).pack(side="left")
        title_wrap = ctk.CTkFrame(top, fg_color="transparent")
        title_wrap.pack(side="left", padx=10)
        ctk.CTkLabel(
            title_wrap,
            text=title,
            text_color=INK,
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_wrap,
            text=subtitle,
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(2, 0))
        return card

    def _load_config(self):
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.friends.delete("1.0", "end")
        self.friends.insert("1.0", "\n".join(data.get("friends", [])))
        texts = [
            item.get("value") or item.get("content", "")
            for item in data.get("messages", [])
            if item.get("type") == "text"
        ]
        self.messages.delete("1.0", "end")
        self.messages.insert("1.0", "\n".join(texts))

    def _load_settings(self):
        if SETTINGS_PATH.exists():
            try:
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                self.schedule_time.set(data.get("schedule_time", "21:00"))
                self.schedule_enabled.set(bool(data.get("schedule_enabled", False)))
            except Exception:
                pass

    def save_config(self, silent: bool = False):
        friends = [line.strip() for line in self.friends.get("1.0", "end").splitlines() if line.strip()]
        messages = [line.strip() for line in self.messages.get("1.0", "end").splitlines() if line.strip()]
        schedule = self.schedule_time.get().strip()

        if not friends or not messages:
            if not silent:
                messagebox.showwarning(APP_NAME, "请至少填写一位互动对象和一条发送内容。")
            return False
        if len(friends) > 10:
            if not silent:
                messagebox.showwarning(APP_NAME, "为降低账号风险，第一版最多设置 10 位互动对象。")
            return False
        try:
            datetime.strptime(schedule, "%H:%M")
        except ValueError:
            if not silent:
                messagebox.showwarning(APP_NAME, "运行时间请使用 24 小时格式，例如 21:00。")
            return False

        config = dict(DEFAULT_CONFIG)
        config["friends"] = friends
        config["messages"] = [{"type": "text", "value": text} for text in messages]
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        SETTINGS_PATH.write_text(
            json.dumps(
                {
                    "schedule_time": schedule,
                    "schedule_enabled": self.schedule_enabled.get(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.activity_text.configure(text="设置已保存")
        if not silent:
            messagebox.showinfo(APP_NAME, "设置已保存。")
        return True

    def _refresh_login_state(self):
        logged_in = STATE_PATH.exists()
        self.login_dot.configure(text_color=GREEN if logged_in else MUTED)
        self.login_text.configure(text="已登录" if logged_in else "未登录")
        self.login_button.configure(text="重新扫码" if logged_in else "扫码登录")
        self.logout_button.configure(state="normal" if logged_in else "disabled")

    def start_qr_login(self):
        if self.busy:
            return
        self._open_qr_window()
        self._set_busy(True, "获取二维码")
        threading.Thread(target=self._qr_login_worker, daemon=True).start()

    def _open_qr_window(self):
        if self.login_window and self.login_window.winfo_exists():
            self.login_window.destroy()
        window = ctk.CTkToplevel(self.root)
        self.login_window = window
        window.title("扫码登录")
        window.geometry("420x520")
        window.resizable(False, False)
        window.configure(fg_color=CANVAS)
        window.transient(self.root)
        window.grab_set()

        ctk.CTkLabel(
            window,
            text="使用抖音 App 扫码",
            text_color=INK,
            font=ctk.CTkFont(size=21, weight="bold"),
        ).pack(pady=(28, 5))
        ctk.CTkLabel(
            window,
            text="请在手机上完成登录确认",
            text_color=MUTED,
            font=ctk.CTkFont(size=12),
        ).pack()

        qr_frame = ctk.CTkFrame(
            window, width=286, height=286, fg_color="#FFFFFF", corner_radius=18
        )
        qr_frame.pack(pady=24)
        qr_frame.pack_propagate(False)
        self.qr_label = ctk.CTkLabel(
            qr_frame,
            text="正在获取二维码…",
            text_color=MUTED,
            font=ctk.CTkFont(size=13),
        )
        self.qr_label.pack(expand=True)

        ctk.CTkLabel(
            window,
            text="二维码只用于本次登录，不会上传到服务器",
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        ).pack()
        ctk.CTkButton(
            window,
            text="取消",
            width=100,
            height=38,
            corner_radius=12,
            fg_color="#E9E9E6",
            hover_color="#DCDCD8",
            text_color=INK,
            command=window.destroy,
        ).pack(pady=18)

    def _qr_login_worker(self):
        try:
            asyncio.run(self._qr_login())
            self.root.after(0, self._login_success)
        except Exception as exc:
            self.root.after(0, lambda: self._login_error(str(exc)))
        finally:
            self._set_busy(False, "空闲")

    async def _qr_login(self):
        if QR_PATH.exists():
            QR_PATH.unlink()
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(locale="zh-CN")
            page = await context.new_page()
            await page.goto("https://www.douyin.com/", wait_until="domcontentloaded", timeout=60_000)

            login = page.get_by_text("登录", exact=True)
            if await login.count():
                try:
                    await login.first.click(timeout=8_000)
                except Exception:
                    pass
            qr_tab = page.get_by_text("扫码登录", exact=True)
            if await qr_tab.count():
                try:
                    await qr_tab.first.click(timeout=5_000)
                except Exception:
                    pass

            qr = None
            selectors = [
                '[class*="qrcode"] img',
                '[class*="qr-code"] img',
                '[class*="login"] canvas',
                '[class*="qrcode"] canvas',
                'canvas',
            ]
            for _ in range(20):
                for selector in selectors:
                    candidate = page.locator(selector)
                    if await candidate.count():
                        for index in range(min(await candidate.count(), 4)):
                            item = candidate.nth(index)
                            try:
                                box = await item.bounding_box()
                                if box and box["width"] >= 120 and box["height"] >= 120:
                                    qr = item
                                    break
                            except Exception:
                                continue
                    if qr is not None:
                        break
                if qr is not None:
                    break
                await page.wait_for_timeout(500)

            if qr is None:
                await browser.close()
                raise RuntimeError("暂时没有获取到登录二维码，请稍后重试。")

            await qr.screenshot(path=str(QR_PATH))
            self.root.after(0, self._show_qr_image)

            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                cookies = await context.cookies()
                authenticated = any(
                    cookie.get("name") in {"sessionid", "sessionid_ss", "sid_guard"}
                    and cookie.get("value")
                    for cookie in cookies
                )
                if authenticated:
                    tmp = STATE_PATH.with_suffix(".tmp")
                    await context.storage_state(path=str(tmp))
                    tmp.replace(STATE_PATH)
                    await browser.close()
                    return
                await page.wait_for_timeout(1500)

            await browser.close()
            raise RuntimeError("二维码已过期，请重新扫码。")

    def _show_qr_image(self):
        if not self.qr_label or not QR_PATH.exists():
            return
        image = Image.open(QR_PATH).convert("RGB")
        self.qr_image = ctk.CTkImage(light_image=image, dark_image=image, size=(248, 248))
        self.qr_label.configure(image=self.qr_image, text="")

    def _login_success(self):
        self._refresh_login_state()
        self.activity_text.configure(text="账号登录成功")
        if self.login_window and self.login_window.winfo_exists():
            self.login_window.destroy()
        messagebox.showinfo(APP_NAME, "登录成功，登录状态已安全保存在本机。")

    def _login_error(self, error: str):
        if self.qr_label and self.qr_label.winfo_exists():
            self.qr_label.configure(text=error, image=None)
        self.activity_text.configure(text="登录失败，请重试")

    def logout(self):
        if STATE_PATH.exists():
            STATE_PATH.unlink()
        self._refresh_login_state()
        self.activity_text.configure(text="已退出登录")

    def start_run(self, dry_run: bool):
        if self.busy:
            return
        if not self.save_config(silent=True):
            messagebox.showwarning(APP_NAME, "请先补全互动对象、发送内容和运行时间。")
            return
        if not STATE_PATH.exists():
            messagebox.showwarning(APP_NAME, "请先使用手机扫码登录。")
            return
        if not dry_run:
            confirmed = messagebox.askyesno(
                APP_NAME,
                "即将按当前设置发送消息。建议先执行一次“安全检查”，确认对象无误。是否继续？",
            )
            if not confirmed:
                return
        self._set_busy(True, "安全检查" if dry_run else "正在运行")
        threading.Thread(target=self._run_worker, args=(dry_run,), daemon=True).start()

    def _run_worker(self, dry_run: bool):
        try:
            code = asyncio.run(run(dry_run=dry_run))
            label = "安全检查通过" if dry_run and code == 0 else (
                "本次运行完成" if code == 0 else "运行完成，但存在失败"
            )
            self.root.after(0, lambda: self.activity_text.configure(
                text=f"{label} · {datetime.now():%H:%M}"
            ))
        except Exception as exc:
            self.root.after(0, lambda: self.activity_text.configure(
                text=f"运行失败：{str(exc)[:48]}"
            ))
        finally:
            self._set_busy(False, "空闲")

    def _scheduler_loop(self):
        while True:
            try:
                now = datetime.now()
                target = self.schedule_time.get().strip()
                today = now.strftime("%Y-%m-%d")
                if (
                    self.schedule_enabled.get()
                    and now.strftime("%H:%M") == target
                    and self.last_schedule_day != today
                ):
                    self.last_schedule_day = today
                    self.root.after(0, lambda: self.start_run(False))
            except Exception:
                pass
            time.sleep(20)

    def _set_busy(self, busy: bool, label: str):
        self.busy = busy

        def update():
            color = YELLOW if busy else "#2B2B2B"
            text_color = INK if busy else "#D9D9D9"
            self.run_badge.configure(
                text=f"●  {label}", fg_color=color, text_color=text_color
            )
            state = "disabled" if busy else "normal"
            self.run_button.configure(state=state)
            self.test_button.configure(state=state)
            self.login_button.configure(state=state)

        self.root.after(0, update)


def main():
    root = ctk.CTk()
    DesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
