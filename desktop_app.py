from __future__ import annotations

import asyncio
import json
import os
import platform
import random
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import customtkinter as ctk
from PIL import Image
from tkinter import messagebox
from playwright.async_api import async_playwright

from app.main import run
from app.browser import AuthenticationError, RiskControlError


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
DAILY_STATUS_PATH = DATA_DIR / "daily-status.json"
ACCOUNTS_DIR = DATA_DIR / "accounts"
ACCOUNTS_PATH = DATA_DIR / "accounts.json"

RANDOM_MESSAGES = [
    "今天也要开心呀 ✨",
    "记得照顾好自己，保持好心情～",
    "忙完记得休息一下呀",
    "愿你今天一切顺利 😊",
    "来和你打个招呼，祝你今天愉快",
]

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
        self.daily_plan: list[dict] = []
        self.risk_stopped = False
        self.active_plan_item: dict | None = None
        self.accounts: list[dict] = []
        self.current_account_id = ""
        self.pending_new_account = False

        self._prepare_runtime()
        self._build_ui()
        self._load_config()
        self._load_settings()
        self._refresh_login_state()
        self._render_target_status()
        threading.Thread(target=self._scheduler_loop, daemon=True).start()

    def _prepare_runtime(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ACCOUNTS_DIR.mkdir(parents=True, exist_ok=True)
        self._load_account_registry()
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
        self.run_badge.pack(side="right", padx=(10, 30))
        self.risk_badge = ctk.CTkLabel(
            header, text="●  状态正常", width=112, height=34, corner_radius=17,
            fg_color="#173D2C", text_color="#6FE0A5",
            font=ctk.CTkFont(size=13, weight="bold")
        )
        self.risk_badge.pack(side="right")

        body = ctk.CTkScrollableFrame(
            shell, fg_color=CANVAS, scrollbar_button_color="#D2D2CD"
        )
        body.pack(fill="both", expand=True, padx=24, pady=20)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)

        login_card = self._card(body, 0, 0, "01", "账号登录", "扫码后登录状态仅保存在本机")
        account_row = ctk.CTkFrame(login_card, fg_color="#FAFAF8", corner_radius=12)
        account_row.pack(fill="x", padx=22, pady=(4, 10))
        ctk.CTkLabel(
            account_row, text="当前账号", text_color=MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left", padx=(14, 8), pady=10)
        self.account_name = ctk.StringVar(value=self._current_account_name())
        self.account_menu = ctk.CTkOptionMenu(
            account_row, variable=self.account_name,
            values=self._account_names() or ["暂无账号"],
            command=self._account_selected, width=126, height=34,
            fg_color="#EFEFED", button_color="#E1E1DD",
            button_hover_color="#D5D5D0", text_color=INK
        )
        self.account_menu.pack(side="left", pady=8)
        ctk.CTkButton(
            account_row, text="＋ 添加账号", width=96, height=34, corner_radius=10,
            fg_color=INK, hover_color="#333333", text_color="#FFFFFF",
            command=self.add_account
        ).pack(side="right", padx=8, pady=8)

        login_row = ctk.CTkFrame(login_card, fg_color="transparent")
        login_row.pack(fill="x", padx=22, pady=(0, 18))
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
            text="每行一个，最多 10 人。红色未发送，绿色已完成",
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=24, pady=(0, 8))
        self.target_status = ctk.CTkTextbox(
            target_card, height=82, corner_radius=12, border_width=0,
            fg_color="#FAFAF8", text_color=INK, font=ctk.CTkFont(size=12)
        )
        self.target_status.pack(fill="x", padx=22, pady=(0, 18))
        self.target_status.configure(state="disabled")

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
        mode_row = ctk.CTkFrame(message_card, fg_color="transparent")
        mode_row.pack(fill="x", padx=22, pady=(0, 18))
        ctk.CTkLabel(
            mode_row, text="发送方式", text_color=MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left")
        self.message_mode = ctk.StringVar(value="随机选择")
        ctk.CTkSegmentedButton(
            mode_row, values=["固定第一条", "随机选择"], variable=self.message_mode,
            selected_color=INK, selected_hover_color="#303030",
            unselected_color="#EFEFED", unselected_hover_color="#E2E2DE",
            text_color=INK, corner_radius=10
        ).pack(side="right")

        schedule_card = self._card(body, 1, 1, "04", "智能计划", "自动分散到白天和晚间，不再设置固定时间")
        plan_box = ctk.CTkFrame(schedule_card, fg_color="#FAFAF8", corner_radius=14)
        plan_box.pack(fill="x", padx=22, pady=(5, 12))
        ctk.CTkLabel(
            plan_box, text="每天自动生成分散时间", text_color=INK,
            font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=16, pady=(14, 2))
        ctk.CTkLabel(
            plan_box, text="09:30–21:30 随机安排，每位好友单独执行",
            text_color=MUTED, font=ctk.CTkFont(size=11)
        ).pack(anchor="w", padx=16, pady=(0, 14))
        switch_row = ctk.CTkFrame(schedule_card, fg_color="transparent")
        switch_row.pack(fill="x", padx=24, pady=(2, 20))
        ctk.CTkLabel(
            switch_row, text="启用每日智能计划", text_color=INK,
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left")
        self.schedule_enabled = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            switch_row, text="", variable=self.schedule_enabled, width=44,
            progress_color=GREEN, button_color="#FFFFFF",
            command=self._schedule_switch_changed
        ).pack(side="right")

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
            text="启动今日计划",
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
                self.schedule_enabled.set(bool(data.get("schedule_enabled", False)))
                self.message_mode.set(data.get("message_mode", "随机选择"))
                self._load_daily_plan()
            except Exception:
                pass

    def save_config(self, silent: bool = False):
        friends = [line.strip() for line in self.friends.get("1.0", "end").splitlines() if line.strip()]
        messages = [line.strip() for line in self.messages.get("1.0", "end").splitlines() if line.strip()]
        if not friends:
            if not silent:
                messagebox.showwarning(APP_NAME, "请至少填写一位互动对象。")
            return False
        if not messages and self.message_mode.get() == "随机选择":
            messages = list(RANDOM_MESSAGES)
        if not messages:
            if not silent:
                messagebox.showwarning(APP_NAME, "固定发送模式需要至少填写一条内容。")
            return False
        if len(friends) > 10:
            if not silent:
                messagebox.showwarning(APP_NAME, "为降低账号风险，第一版最多设置 10 位互动对象。")
            return False
        config = dict(DEFAULT_CONFIG)
        config["friends"] = friends
        config["messages"] = [{"type": "text", "value": text} for text in messages]
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        SETTINGS_PATH.write_text(
            json.dumps(
                {
                    "schedule_enabled": self.schedule_enabled.get(),
                    "message_mode": self.message_mode.get(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        self.activity_text.configure(text="设置已保存")
        self._ensure_daily_plan(force=True)
        self._render_target_status()
        if not silent:
            messagebox.showinfo(APP_NAME, "设置已保存。")
        return True

    def _refresh_login_state(self):
        logged_in = STATE_PATH.exists() and bool(self.current_account_id)
        self.login_dot.configure(text_color=GREEN if logged_in else MUTED)
        self.login_text.configure(text="已登录" if logged_in else "未登录")
        self.login_button.configure(text="重新扫码" if logged_in else "扫码登录")
        self.logout_button.configure(state="normal" if logged_in else "disabled")

    def add_account(self):
        if self.busy:
            return
        self.pending_new_account = True
        self.start_qr_login()

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
        if self.pending_new_account or not self.current_account_id:
            number = 1
            names = set(self._account_names())
            while f"账号 {number}" in names:
                number += 1
            account = {"id": f"account-{int(time.time())}", "name": f"账号 {number}"}
            self.accounts.append(account)
            self.current_account_id = account["id"]
        account_path = self._current_account_path()
        if account_path:
            shutil.copyfile(STATE_PATH, account_path)
        self.pending_new_account = False
        self._save_account_registry()
        self._refresh_account_menu()
        self._refresh_login_state()
        self.risk_badge.configure(text="●  状态正常", fg_color="#173D2C", text_color="#6FE0A5")
        self.activity_text.configure(text=f"{self._current_account_name()} 登录成功")
        if self.login_window and self.login_window.winfo_exists():
            self.login_window.destroy()
        messagebox.showinfo(APP_NAME, "登录成功，登录状态已安全保存在本机。")

    def _login_error(self, error: str):
        if self.qr_label and self.qr_label.winfo_exists():
            self.qr_label.configure(text=error, image=None)
        self.activity_text.configure(text="登录失败，请重试")

    def _load_account_registry(self):
        if ACCOUNTS_PATH.exists():
            try:
                payload = json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
                self.accounts = payload.get("accounts", [])
                self.current_account_id = payload.get("current", "")
            except Exception:
                self.accounts = []
                self.current_account_id = ""
        if STATE_PATH.exists() and not self.accounts:
            account = {"id": "account-1", "name": "账号 1"}
            self.accounts = [account]
            self.current_account_id = account["id"]
            shutil.copyfile(STATE_PATH, ACCOUNTS_DIR / "account-1.json")
            self._save_account_registry()
        self._activate_current_account()

    def _save_account_registry(self):
        ACCOUNTS_PATH.write_text(json.dumps({
            "current": self.current_account_id,
            "accounts": self.accounts,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _account_names(self):
        return [item["name"] for item in self.accounts]

    def _current_account_name(self):
        item = next((a for a in self.accounts if a["id"] == self.current_account_id), None)
        return item["name"] if item else "暂无账号"

    def _current_account_path(self):
        return ACCOUNTS_DIR / f"{self.current_account_id}.json" if self.current_account_id else None

    def _daily_status_path(self):
        return ACCOUNTS_DIR / f"{self.current_account_id}-daily.json" if self.current_account_id else DAILY_STATUS_PATH

    def _activate_current_account(self):
        path = self._current_account_path()
        if path and path.exists():
            shutil.copyfile(path, STATE_PATH)
        elif STATE_PATH.exists():
            STATE_PATH.unlink()

    def _refresh_account_menu(self):
        values = self._account_names() or ["暂无账号"]
        self.account_menu.configure(values=values)
        self.account_name.set(self._current_account_name())

    def _account_selected(self, selected: str):
        account = next((a for a in self.accounts if a["name"] == selected), None)
        if not account or account["id"] == self.current_account_id:
            return
        if self.busy:
            self.account_name.set(self._current_account_name())
            messagebox.showwarning(APP_NAME, "任务运行中，暂时不能切换账号。")
            return
        self.current_account_id = account["id"]
        self._activate_current_account()
        self.daily_plan = []
        self.risk_stopped = False
        self._load_daily_plan()
        self._save_account_registry()
        self._refresh_login_state()
        self._render_target_status()
        self.risk_badge.configure(text="●  状态正常", fg_color="#173D2C", text_color="#6FE0A5")
        self.activity_text.configure(text=f"已切换到 {selected}")

    def _invalidate_current_account(self):
        path = self._current_account_path()
        if path and path.exists():
            path.unlink()
        if STATE_PATH.exists():
            STATE_PATH.unlink()

    def logout(self):
        if not self.current_account_id:
            return
        current = self.current_account_id
        self._invalidate_current_account()
        self.accounts = [a for a in self.accounts if a["id"] != current]
        self.current_account_id = self.accounts[0]["id"] if self.accounts else ""
        self._activate_current_account()
        self.daily_plan = []
        self._load_daily_plan()
        self._save_account_registry()
        self._refresh_account_menu()
        self._refresh_login_state()
        self._render_target_status()
        self.activity_text.configure(text="当前账号已退出并移除")

    def start_run(self, dry_run: bool):
        if self.busy:
            return
        if not self.save_config(silent=True):
            messagebox.showwarning(APP_NAME, "请先补全互动对象和发送内容。")
            return
        if not STATE_PATH.exists():
            messagebox.showwarning(APP_NAME, "请先使用手机扫码登录。")
            return
        if dry_run:
            self._set_busy(True, "安全检查")
            threading.Thread(target=self._run_worker, args=(True,), daemon=True).start()
            return
        self.schedule_enabled.set(True)
        self.risk_stopped = False
        self._ensure_daily_plan(force=True)
        self._save_settings_only()
        self._render_target_status()
        self.activity_text.configure(text="今日计划已启动，将在分散时间自动发送")
        messagebox.showinfo(APP_NAME, "今日计划已启动。软件保持打开时，会在不同时间分别执行。")

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

    def _schedule_switch_changed(self):
        if self.schedule_enabled.get():
            if not STATE_PATH.exists():
                self.schedule_enabled.set(False)
                messagebox.showwarning(APP_NAME, "请先扫码登录。")
                return
            if not self.save_config(silent=True):
                self.schedule_enabled.set(False)
                return
            self.risk_stopped = False
            self._ensure_daily_plan(force=True)
            self.activity_text.configure(text="每日智能计划已开启")
        else:
            self.activity_text.configure(text="每日智能计划已暂停")
        self._save_settings_only()
        self._render_target_status()

    def _save_settings_only(self):
        SETTINGS_PATH.write_text(json.dumps({
            "schedule_enabled": self.schedule_enabled.get(),
            "message_mode": self.message_mode.get(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_daily_plan(self):
        status_path = self._daily_status_path()
        if not status_path.exists():
            return
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            if payload.get("date") == datetime.now().strftime("%Y-%m-%d"):
                self.daily_plan = payload.get("items", [])
                self.risk_stopped = bool(payload.get("risk_stopped", False))
        except Exception:
            self.daily_plan = []

    def _ensure_daily_plan(self, force: bool = False):
        friends = [line.strip() for line in self.friends.get("1.0", "end").splitlines() if line.strip()]
        today = datetime.now().strftime("%Y-%m-%d")
        existing_names = [item.get("friend") for item in self.daily_plan]
        if not force and self.daily_plan and existing_names == friends:
            return
        now = datetime.now()
        start = now.replace(hour=9, minute=30, second=0, microsecond=0)
        end = now.replace(hour=21, minute=30, second=0, microsecond=0)
        if now > start:
            start = now + timedelta(minutes=5)
        if start >= end:
            start = now + timedelta(minutes=2)
            end = now + timedelta(hours=2)
        span = max(1, int((end - start).total_seconds()))
        count = len(friends)
        bucket = span / max(1, count)
        slots = [min(span - 1, int(i * bucket + random.uniform(bucket * 0.18, bucket * 0.82)))
                 for i in range(count)]
        self.daily_plan = [{
            "friend": friend,
            "time": (start + timedelta(seconds=slots[i])).isoformat(timespec="seconds"),
            "status": "pending",
            "error": "",
        } for i, friend in enumerate(friends)]
        self.risk_stopped = False
        self._persist_daily_plan(today)

    def _persist_daily_plan(self, date: str | None = None):
        self._daily_status_path().write_text(json.dumps({
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "risk_stopped": self.risk_stopped,
            "items": self.daily_plan,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def _render_target_status(self):
        if not hasattr(self, "target_status"):
            return
        friends = [line.strip() for line in self.friends.get("1.0", "end").splitlines() if line.strip()]
        by_name = {item.get("friend"): item for item in self.daily_plan}
        lines = []
        for friend in friends:
            item = by_name.get(friend, {})
            status = item.get("status", "pending")
            when = item.get("time", "")
            clock = datetime.fromisoformat(when).strftime("%H:%M") if when else "--:--"
            if status == "success":
                lines.append(f"🟢  {friend}  ·  已完成")
            elif status == "running":
                lines.append(f"🟡  {friend}  ·  正在发送")
            elif status == "failed":
                lines.append(f"🔴  {friend}  ·  已停止")
            else:
                lines.append(f"🔴  {friend}  ·  未发送  {clock}")
        self.target_status.configure(state="normal")
        self.target_status.delete("1.0", "end")
        self.target_status.insert("1.0", "\n".join(lines) or "添加好友后显示今日状态")
        self.target_status.configure(state="disabled")

    def _scheduler_loop(self):
        while True:
            try:
                today = datetime.now().strftime("%Y-%m-%d")
                if self.schedule_enabled.get() and STATE_PATH.exists() and not self.risk_stopped:
                    self._ensure_daily_plan()
                    due = next((item for item in self.daily_plan
                                if item.get("status") == "pending"
                                and datetime.fromisoformat(item["time"]) <= datetime.now()), None)
                    if due and not self.busy:
                        due["status"] = "running"
                        self.active_plan_item = due
                        self._persist_daily_plan(today)
                        self.root.after(0, self._render_target_status)
                        self._set_busy(True, "风险监测中")
                        threading.Thread(target=self._run_plan_item, args=(due,), daemon=True).start()
            except Exception as exc:
                self.root.after(0, lambda: self.activity_text.configure(text=f"计划检查失败：{str(exc)[:36]}"))
            time.sleep(15)

    def _run_plan_item(self, item: dict):
        original = CONFIG_PATH.read_text(encoding="utf-8")
        try:
            config = json.loads(original)
            all_messages = config.get("messages", [])
            if not all_messages:
                raise RuntimeError("没有可发送的内容")
            selected = all_messages[0] if self.message_mode.get() == "固定第一条" else random.choice(all_messages)
            config["friends"] = [item["friend"]]
            config["messages"] = [selected]
            CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            code = asyncio.run(run(dry_run=False))
            if code != 0:
                raise RuntimeError("发送未完成")
            item["status"] = "success"
            item["error"] = ""
            self.root.after(0, lambda: self.activity_text.configure(
                text=f"{item['friend']} 已发送 · {datetime.now():%H:%M}"
            ))
        except (AuthenticationError, RiskControlError) as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            self.risk_stopped = True
            self.schedule_enabled.set(False)
            if isinstance(exc, AuthenticationError):
                self._invalidate_current_account()
            self.root.after(0, lambda: self._emergency_stop(str(exc)))
        except Exception as exc:
            item["status"] = "failed"
            item["error"] = str(exc)
            self.risk_stopped = True
            self.schedule_enabled.set(False)
            self.root.after(0, lambda: self._emergency_stop(f"运行异常：{str(exc)[:60]}"))
        finally:
            CONFIG_PATH.write_text(original, encoding="utf-8")
            self._persist_daily_plan()
            self.root.after(0, self._render_target_status)
            self._set_busy(False, "空闲")
            self.active_plan_item = None

    def _emergency_stop(self, reason: str):
        self._save_settings_only()
        self._refresh_login_state()
        self.activity_text.configure(text=f"已自动停止：{reason[:52]}")
        self.run_badge.configure(text="●  已安全停止", fg_color=RED, text_color="#FFFFFF")
        self.risk_badge.configure(text="●  高风险/异常", fg_color="#8E1F1F", text_color="#FFFFFF")
        messagebox.showwarning(APP_NAME, f"检测到风险或登录异常，今天剩余任务已立即停止。\n\n{reason}")


    def _set_busy(self, busy: bool, label: str):
        self.busy = busy

        def update():
            color = YELLOW if busy else "#2B2B2B"
            text_color = INK if busy else "#D9D9D9"
            self.run_badge.configure(
                text=f"●  {label}", fg_color=color, text_color=text_color
            )
            if busy and not self.risk_stopped:
                self.risk_badge.configure(text="●  实时检测中", fg_color="#173D2C", text_color="#6FE0A5")
            elif not busy and not self.risk_stopped:
                self.risk_badge.configure(text="●  状态正常", fg_color="#173D2C", text_color="#6FE0A5")
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
