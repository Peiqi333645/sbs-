from __future__ import annotations

import asyncio
import base64
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
from io import BytesIO

import customtkinter as ctk
from PIL import Image
from tkinter import filedialog, messagebox
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
        self.pending_account_name = ""
        self.qr_cancel_event = threading.Event()
        self.qr_prefetch_ready = threading.Event()
        self.qr_worker_active = False
        self.qr_expires_at = 0.0
        self.qr_countdown_job = None
        self.friend_rows: list[dict] = []
        self.message_rows: list[dict] = []
        self.scroll_canvases = []

        self._prepare_runtime()
        self._build_ui()
        self.root.bind_all("<MouseWheel>", self._route_mousewheel)
        self._load_config()
        self._load_settings()
        self._refresh_login_state()
        self._render_target_status()
        threading.Thread(target=self._scheduler_loop, daemon=True).start()
        self._begin_qr_prefetch()

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
        self.body_scroll = body
        self._stabilize_scroll_edges(body)
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
            fg_color="#FFF4CC", hover_color="#FFE79A", text_color="#7A5600",
            command=self.add_account
        ).pack(side="right", padx=8, pady=8)
        ctk.CTkButton(
            account_row, text="修改名称", width=72, height=34, corner_radius=10,
            fg_color="#EFEFED", hover_color="#E1E1DD", text_color=INK,
            command=self._rename_current_account
        ).pack(side="right", pady=8)

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
        self.friends_list = ctk.CTkScrollableFrame(
            target_card, height=132, corner_radius=12, border_width=1,
            border_color=BORDER, fg_color="#FAFAF8",
            scrollbar_button_color="#D2D2CD"
        )
        self.friends_list.pack(fill="x", padx=22, pady=(4, 10))
        self._stabilize_scroll_edges(self.friends_list)
        ctk.CTkLabel(
            target_card,
            text="每位好友单独一行，可勾选是否参与；红色未发送，绿色已完成",
            text_color=MUTED,
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", padx=24, pady=(0, 8))
        ctk.CTkButton(
            target_card, text="＋ 添加好友", width=94, height=30,
            corner_radius=9, fg_color="#EFEFED", hover_color="#E2E2DE",
            text_color=INK, command=lambda: self._add_friend_row("")
        ).pack(anchor="w", padx=22, pady=(0, 18))
        self.target_status_frame = ctk.CTkFrame(
            target_card, fg_color="#FAFAF8", corner_radius=12,
            border_width=1, border_color=BORDER
        )

        message_card = self._card(body, 1, 0, "03", "发送内容", "默认发送第一条，也可从多条内容中随机选择")
        self.messages_list = ctk.CTkScrollableFrame(
            message_card, height=142, corner_radius=12, border_width=1,
            border_color=BORDER, fg_color="#FAFAF8",
            scrollbar_button_color="#D2D2CD"
        )
        self.messages_list.pack(fill="x", padx=22, pady=(4, 10))
        self._stabilize_scroll_edges(self.messages_list)
        ctk.CTkButton(
            message_card, text="＋ 添加内容", width=94, height=30,
            corner_radius=9, fg_color="#EFEFED", hover_color="#E2E2DE",
            text_color=INK, command=lambda: self._add_message_row("")
        ).pack(anchor="w", padx=22, pady=(0, 8))
        mode_row = ctk.CTkFrame(message_card, fg_color="transparent")
        mode_row.pack(fill="x", padx=22, pady=(0, 18))
        ctk.CTkLabel(
            mode_row, text="发送方式", text_color=MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left")
        self.message_mode = ctk.StringVar(value="默认第一条")
        self.message_mode_control = self._pill_selector(
            mode_row, ["默认第一条", "随机内容"], self.message_mode
        )
        self.message_mode_control.pack(side="right")

        schedule_card = self._card(body, 1, 1, "04", "智能计划", "选择随机分散时间，或指定每天固定开始时间")
        plan_mode_row = ctk.CTkFrame(schedule_card, fg_color="transparent")
        plan_mode_row.pack(fill="x", padx=22, pady=(5, 10))
        ctk.CTkLabel(
            plan_mode_row, text="时间方式", text_color=MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left")
        self.plan_mode = ctk.StringVar(value="随机时间")
        self.plan_mode_control = self._pill_selector(
            plan_mode_row, ["随机时间", "固定时间"], self.plan_mode,
            command=self._plan_mode_changed
        )
        self.plan_mode_control.pack(side="right")

        plan_box = ctk.CTkFrame(schedule_card, fg_color="#FAFAF8", corner_radius=14)
        plan_box.pack(fill="x", padx=22, pady=(0, 12))
        self.plan_title = ctk.CTkLabel(
            plan_box, text="每天自动生成分散时间", text_color=INK,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        self.plan_title.pack(anchor="w", padx=16, pady=(14, 2))
        self.plan_description = ctk.CTkLabel(
            plan_box, text="01:00–23:00 分段安排，确保当天完成",
            text_color=MUTED, font=ctk.CTkFont(size=11)
        )
        self.plan_description.pack(anchor="w", padx=16, pady=(0, 10))
        fixed_row = ctk.CTkFrame(plan_box, fg_color="transparent")
        fixed_row.pack(fill="x", padx=16, pady=(0, 14))
        ctk.CTkLabel(
            fixed_row, text="固定开始时间", text_color=MUTED, font=ctk.CTkFont(size=11)
        ).pack(side="left")
        self.fixed_hour = ctk.StringVar(value="21")
        self.fixed_minute = ctk.StringVar(value="00")
        self.fixed_minute_entry = ctk.CTkEntry(
            fixed_row, textvariable=self.fixed_minute, width=42, height=34,
            corner_radius=10, border_color=BORDER, fg_color="#FFFFFF",
            justify="center", font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled"
        )
        self.fixed_minute_entry.pack(side="right")
        ctk.CTkLabel(fixed_row, text=":", width=18, text_color=INK,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="right")
        self.fixed_hour_entry = ctk.CTkEntry(
            fixed_row, textvariable=self.fixed_hour, width=42, height=34,
            corner_radius=10, border_color=BORDER, fg_color="#FFFFFF",
            justify="center", font=ctk.CTkFont(size=13, weight="bold"),
            state="disabled"
        )
        self.fixed_hour_entry.pack(side="right")

        switch_row = ctk.CTkFrame(schedule_card, fg_color="transparent")
        switch_row.pack(fill="x", padx=24, pady=(2, 20))
        ctk.CTkLabel(
            switch_row, text="启用每日智能计划", text_color=INK,
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(side="left")
        self.schedule_enabled = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(
            switch_row, text="", variable=self.schedule_enabled, width=44,
            progress_color=YELLOW, button_color="#FFFFFF",
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
            fg_color="#FFF4CC",
            hover_color="#FFE79A",
            text_color="#7A5600",
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

    def _stabilize_scroll_edges(self, scroll_frame):
        """Register a scroll surface for deterministic macOS wheel routing."""
        canvas = getattr(scroll_frame, "_parent_canvas", None)
        if canvas is None:
            return
        self.scroll_canvases.append(canvas)

    def _route_mousewheel(self, event):
        """Route one gesture to one canvas and consume overscroll at the edge."""
        if not getattr(event, "delta", 0):
            return "break"
        pointer_x, pointer_y = self.root.winfo_pointerxy()
        candidates = []
        for canvas in self.scroll_canvases:
            try:
                left, top = canvas.winfo_rootx(), canvas.winfo_rooty()
                width, height = canvas.winfo_width(), canvas.winfo_height()
                if left <= pointer_x <= left + width and top <= pointer_y <= top + height:
                    candidates.append((width * height, canvas))
            except Exception:
                continue
        if not candidates:
            return None
        direction = -1 if event.delta > 0 else 1
        for _area, canvas in sorted(candidates, key=lambda item: item[0]):
            first, last = canvas.yview()
            can_move = (direction < 0 and first > 0.0005) or (direction > 0 and last < 0.9995)
            if can_move:
                units = max(1, min(3, abs(int(event.delta)) // 30 or 1))
                canvas.yview_scroll(direction * units, "units")
                first, last = canvas.yview()
                if first < 0.0005:
                    canvas.yview_moveto(0)
                elif last > 0.9995:
                    canvas.yview_moveto(1)
                break
        return "break"

    def _add_friend_row(self, name: str, selected: bool = True):
        row = ctk.CTkFrame(self.friends_list, fg_color="transparent")
        row.pack(fill="x", padx=5, pady=4)
        enabled = ctk.BooleanVar(value=selected)
        ctk.CTkCheckBox(
            row, text="", variable=enabled, width=26, checkbox_width=20,
            checkbox_height=20, fg_color=YELLOW, hover_color=YELLOW_HOVER,
            border_color="#BDBDB8", command=self._render_target_status
        ).pack(side="left", padx=(3, 5))
        value = ctk.StringVar(value=name)
        entry = ctk.CTkEntry(
            row, textvariable=value, height=34, corner_radius=9,
            border_color=BORDER, fg_color="#FFFFFF", text_color=INK
        )
        entry.pack(side="left", fill="x", expand=True)
        badge = ctk.CTkLabel(
            row, text="未发送", width=66, height=25, corner_radius=9,
            fg_color="#FDECEC", text_color="#B52E2E",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        badge.pack(side="right", padx=(7, 3))
        record = {"frame": row, "enabled": enabled, "value": value, "badge": badge}
        self.friend_rows.append(record)
        value.trace_add("write", lambda *_: self.root.after_idle(self._render_target_status))
        return record

    def _add_message_row(self, text: str):
        row = ctk.CTkFrame(self.messages_list, fg_color="transparent")
        row.pack(fill="x", padx=5, pady=4)
        number = ctk.CTkLabel(
            row, text=str(len(self.message_rows) + 1), width=27, height=27,
            corner_radius=8, fg_color="#FFF4CC", text_color="#8A6300",
            font=ctk.CTkFont(size=11, weight="bold")
        )
        number.pack(side="left", padx=(3, 7))
        value = ctk.StringVar(value=text)
        ctk.CTkEntry(
            row, textvariable=value, height=34, corner_radius=9,
            border_color=BORDER, fg_color="#FFFFFF", text_color=INK
        ).pack(side="left", fill="x", expand=True)
        record = {"frame": row, "value": value, "number": number}
        self.message_rows.append(record)
        ctk.CTkButton(
            row, text="×", width=30, height=30, corner_radius=9,
            fg_color="transparent", hover_color="#FDECEC", text_color=MUTED,
            command=lambda: self._remove_message_row(record)
        ).pack(side="right", padx=(5, 1))

    def _remove_message_row(self, record):
        if len(self.message_rows) == 1:
            record["value"].set("")
            return
        record["frame"].destroy()
        self.message_rows.remove(record)
        for index, item in enumerate(self.message_rows, 1):
            item["number"].configure(text=str(index))

    def _friend_values(self, selected_only: bool = True):
        return [
            row["value"].get().strip() for row in self.friend_rows
            if row["value"].get().strip() and (not selected_only or row["enabled"].get())
        ]

    def _message_values(self):
        return [row["value"].get().strip() for row in self.message_rows if row["value"].get().strip()]

    def _fixed_time_value(self):
        return f"{self.fixed_hour.get().strip()}:{self.fixed_minute.get().strip()}"

    def _account_config_path(self, account_id: str | None = None):
        value = account_id if account_id is not None else self.current_account_id
        return ACCOUNTS_DIR / f"{value}-config.json" if value else None

    def _account_settings_path(self, account_id: str | None = None):
        value = account_id if account_id is not None else self.current_account_id
        return ACCOUNTS_DIR / f"{value}-settings.json" if value else None

    def _reset_editor_state(self):
        for row in self.friend_rows:
            row["frame"].destroy()
        self.friend_rows.clear()
        self._add_friend_row("")
        for row in self.message_rows:
            row["frame"].destroy()
        self.message_rows.clear()
        self._add_message_row("")
        self.schedule_enabled.set(False)
        self.plan_mode.set("随机时间")
        self.fixed_hour.set("21")
        self.fixed_minute.set("00")
        self.daily_plan = []
        self._plan_mode_changed("随机时间", save=False)
        self._render_target_status()

    def _load_account_ui_state(self):
        config_path = self._account_config_path()
        settings_path = self._account_settings_path()
        if not config_path or not config_path.exists():
            self._reset_editor_state()
            return
        shutil.copyfile(config_path, CONFIG_PATH)
        if settings_path and settings_path.exists():
            shutil.copyfile(settings_path, SETTINGS_PATH)
        elif SETTINGS_PATH.exists():
            SETTINGS_PATH.unlink()
        self._load_config()
        self._load_settings()

    def _pill_selector(self, parent, values, variable, command=None):
        track = ctk.CTkFrame(parent, fg_color="#EFEFED", corner_radius=15, height=38)
        buttons = {}

        def paint(*_):
            current = variable.get()
            for item_value, button in buttons.items():
                selected = item_value == current
                button.configure(
                    fg_color=YELLOW if selected else "transparent",
                    hover_color=YELLOW_HOVER if selected else "#E4E4DF",
                    text_color=INK if selected else MUTED,
                )

        def choose(value, notify=True):
            variable.set(value)
            paint()
            if notify and command:
                command(value)

        for index, value in enumerate(values):
            button = ctk.CTkButton(
                track, text=value, width=92, height=34, corner_radius=12,
                border_width=0, fg_color="transparent",
                hover_color="#E4E4DF", text_color=MUTED,
                font=ctk.CTkFont(size=12, weight="bold"),
                command=lambda selected=value: choose(selected)
            )
            button.pack(side="left", padx=(2 if index == 0 else 0, 2), pady=2)
            buttons[value] = button
        variable.trace_add("write", paint)
        choose(variable.get(), notify=False)
        return track

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
        for row in self.friend_rows:
            row["frame"].destroy()
        self.friend_rows.clear()
        selected = set(data.get("friends", []))
        saved_rows = data.get("friend_rows") or data.get("friends", []) or [""]
        for name in saved_rows:
            self._add_friend_row(name, name in selected or not name)
        texts = [
            item.get("value") or item.get("content", "")
            for item in data.get("messages", [])
            if item.get("type") == "text"
        ]
        for row in self.message_rows:
            row["frame"].destroy()
        self.message_rows.clear()
        for text in texts or [""]:
            self._add_message_row(text)

    def _load_settings(self):
        if SETTINGS_PATH.exists():
            try:
                data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                self.schedule_enabled.set(bool(data.get("schedule_enabled", False)))
                saved_mode = data.get("message_mode", "默认第一条")
                legacy_modes = {
                    "固定第一条": "默认第一条",
                    "指定内容": "默认第一条",
                    "随机选择": "随机内容",
                }
                self.message_mode.set(legacy_modes.get(saved_mode, saved_mode))
                self.plan_mode.set(data.get("plan_mode", "随机时间"))
                hour, minute = (data.get("fixed_time", "21:00").split(":") + ["00"])[:2]
                self.fixed_hour.set(hour)
                self.fixed_minute.set(minute)
                self._plan_mode_changed(self.plan_mode.get(), save=False)
                self._load_daily_plan()
            except Exception:
                pass

    def save_config(self, silent: bool = False):
        friends = self._friend_values()
        messages = self._message_values()
        if not friends:
            if not silent:
                messagebox.showwarning(APP_NAME, "请至少填写一位互动对象。")
            return False
        if not messages and self.message_mode.get() == "随机内容":
            messages = list(RANDOM_MESSAGES)
        if not messages:
            if not silent:
                messagebox.showwarning(APP_NAME, "默认第一条模式需要至少填写一条内容。")
            return False
        if self.plan_mode.get() == "固定时间":
            try:
                parsed_time = datetime.strptime(self._fixed_time_value(), "%H:%M")
                self.fixed_hour.set(f"{parsed_time.hour:02d}")
                self.fixed_minute.set(f"{parsed_time.minute:02d}")
            except ValueError:
                if not silent:
                    messagebox.showwarning(APP_NAME, "固定时间请使用 24 小时格式，例如 21:00。")
                return False
        config = dict(DEFAULT_CONFIG)
        config["friends"] = friends
        config["friend_rows"] = self._friend_values(selected_only=False)
        config["messages"] = [{"type": "text", "value": text} for text in messages]
        CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        SETTINGS_PATH.write_text(
            json.dumps(
                {
                    "schedule_enabled": self.schedule_enabled.get(),
                    "message_mode": self.message_mode.get(),
                    "plan_mode": self.plan_mode.get(),
                    "fixed_time": self._fixed_time_value(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        account_config = self._account_config_path()
        account_settings = self._account_settings_path()
        if account_config and account_settings:
            shutil.copyfile(CONFIG_PATH, account_config)
            shutil.copyfile(SETTINGS_PATH, account_settings)
        self.activity_text.configure(text="设置已保存")
        self._ensure_daily_plan(force=True)
        self._render_target_status()
        if not silent:
            messagebox.showinfo(APP_NAME, "设置已保存。")
        return True

    def _refresh_login_state(self):
        logged_in = STATE_PATH.exists() and bool(self.current_account_id)
        self.login_dot.configure(text_color=GREEN if logged_in else MUTED)
        self.login_text.configure(
            text=f"已登录 · {self._current_account_name()}" if logged_in else "未登录"
        )
        self.login_button.configure(text="重新扫码" if logged_in else "扫码登录")
        self.logout_button.configure(state="normal" if logged_in else "disabled")

    def add_account(self):
        if self.busy:
            return
        self.pending_new_account = True
        self.start_qr_login()

    def _begin_qr_prefetch(self):
        if self.qr_worker_active:
            return
        self.qr_cancel_event.clear()
        self.qr_prefetch_ready.clear()
        self.qr_worker_active = True
        threading.Thread(target=self._qr_login_worker, daemon=True).start()

    def start_qr_login(self):
        if self.busy:
            return
        self._open_qr_window()
        self._set_busy(True, "获取二维码")

        # 软件启动时已经在后台准备二维码。点击后直接显示缓存结果，
        # 并继续使用同一个浏览器会话等待扫码，不能另开会话。
        if self.qr_worker_active:
            if (self.qr_prefetch_ready.is_set() and QR_PATH.exists()
                    and self.qr_expires_at > time.monotonic()):
                self.root.after(0, self._show_qr_image)
                self.activity_text.configure(text="二维码已准备好，请扫码")
            elif self.qr_prefetch_ready.is_set():
                self._login_error("二维码已失效，请稍候重新打开扫码页")
            return

        self.qr_cancel_event.clear()
        self.qr_prefetch_ready.clear()
        self.qr_worker_active = True
        threading.Thread(target=self._qr_login_worker, daemon=True).start()

    def _open_qr_window(self):
        if self.login_window and self.login_window.winfo_exists():
            self.login_window.destroy()
        window = ctk.CTkToplevel(self.root)
        self.login_window = window
        window.title("扫码登录")
        window.geometry("460x650")
        window.resizable(False, False)
        window.configure(fg_color=CANVAS)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._cancel_login)

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
            window, width=332, height=352, fg_color="#FFFFFF", corner_radius=18
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
        self.qr_countdown_label = ctk.CTkLabel(
            window, text="", text_color="#8A6300",
            font=ctk.CTkFont(size=12, weight="bold")
        )
        self.qr_countdown_label.pack(pady=(8, 0))
        ctk.CTkButton(
            window, text="保存二维码（可选）",
            width=250, height=34, corner_radius=10, fg_color="#FFF4CC",
            hover_color="#FFE79A", text_color="#7A5600",
            command=self._save_qr_image
        ).pack(pady=(8, 0))
        ctk.CTkButton(
            window,
            text="取消",
            width=100,
            height=38,
            corner_radius=12,
            fg_color="#E9E9E6",
            hover_color="#DCDCD8",
            text_color=INK,
            command=self._cancel_login,
        ).pack(pady=18)

    def _cancel_login(self):
        self.pending_new_account = False
        self.qr_cancel_event.set()
        if self.login_window and self.login_window.winfo_exists():
            self.login_window.destroy()
        self.activity_text.configure(text="已取消扫码登录")
        self._set_busy(False, "空闲")

    def _save_qr_image(self):
        if not QR_PATH.exists():
            messagebox.showwarning(APP_NAME, "二维码还没有准备好。")
            return
        destination = filedialog.asksaveasfilename(
            title="保存抖音登录二维码", defaultextension=".png",
            filetypes=[("PNG 图片", "*.png")], initialfile="douyin-login-qr.png"
        )
        if destination:
            shutil.copyfile(QR_PATH, destination)
            self.activity_text.configure(text="原始二维码已保存，可传到手机后从相册识别")

    def _update_qr_countdown(self):
        if not self.login_window or not self.login_window.winfo_exists():
            return
        remaining = max(0, int(self.qr_expires_at - time.monotonic()))
        if hasattr(self, "qr_countdown_label"):
            self.qr_countdown_label.configure(
                text=f"二维码将在 {remaining // 60:02d}:{remaining % 60:02d} 后失效"
                if remaining else "二维码已失效，请关闭后重新扫码"
            )
        if remaining:
            self.qr_countdown_job = self.root.after(1000, self._update_qr_countdown)

    def _qr_login_worker(self):
        try:
            asyncio.run(self._qr_login())
            self.root.after(0, self._login_success)
        except Exception as exc:
            if str(exc) != "登录已取消":
                # 后台预取失败时不弹出错误；用户真正打开扫码窗口后再提示。
                if self.login_window and self.login_window.winfo_exists():
                    self.root.after(
                        0, lambda error=str(exc): self._login_error(error)
                    )
        finally:
            self.qr_worker_active = False
            self.qr_prefetch_ready.clear()
            self._set_busy(False, "空闲")

    async def _qr_login(self):
        if QR_PATH.exists():
            QR_PATH.unlink()
        self.pending_account_name = ""

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "zh-CN,zh;q=0.9",
        }

        async def read_payload(response):
            text = (await response.text()).strip()
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                first, last = text.find("{"), text.rfind("}")
                if first >= 0 and last > first:
                    try:
                        return json.loads(text[first:last + 1])
                    except json.JSONDecodeError:
                        pass
                return {}

        def find_nickname(value):
            if isinstance(value, dict):
                nickname = value.get("nickname")
                if isinstance(nickname, str) and nickname.strip():
                    return nickname.strip()
                for child in value.values():
                    found = find_nickname(child)
                    if found:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = find_nickname(child)
                    if found:
                        return found
            return ""

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(
                locale="zh-CN",
                user_agent=headers["User-Agent"],
                viewport={"width": 1280, "height": 800},
            )
            try:
                # 跳过视频、字体和普通图片，保留 JS 与登录接口，显著缩短冷启动。
                async def route_fast(route):
                    resource_type = route.request.resource_type
                    if resource_type in {"media", "font", "image", "stylesheet"}:
                        await route.abort()
                    else:
                        await route.continue_()

                await context.route("**/*", route_fast)
                page = await context.new_page()
                loop = asyncio.get_running_loop()
                qr_future = loop.create_future()
                confirmed_future = loop.create_future()

                async def capture_login(response):
                    url = response.url
                    if "get_qrcode" in url and not qr_future.done():
                        payload = await read_payload(response)
                        data = payload.get("data") or {}
                        token = str(data.get("token") or "")
                        qr_value = str(
                            data.get("qrcode") or data.get("qr_code") or ""
                        )
                        if token and qr_value:
                            raw_expiry = data.get("expires_in") or data.get("expire_seconds") or 240
                            try:
                                expiry_seconds = max(1, min(600, int(raw_expiry)))
                            except (TypeError, ValueError):
                                expiry_seconds = 240
                            qr_future.set_result((qr_value, expiry_seconds))
                        return
                    if "check_qrconnect" in url:
                        payload = await read_payload(response)
                        data = payload.get("data") or {}
                        try:
                            status = int(data.get("status", 0))
                        except (TypeError, ValueError):
                            status = 0
                        if status == 2:
                            self.root.after(0, self._show_scanned_hint)
                        elif status == 3 and not confirmed_future.done():
                            confirmed_future.set_result(
                                data.get("redirect_url")
                                or data.get("redirectUrl")
                                or ""
                            )
                        elif status == 5 and not confirmed_future.done():
                            confirmed_future.set_exception(
                                RuntimeError("二维码已过期，请重新获取")
                            )

                page.on(
                    "response",
                    lambda response: asyncio.create_task(capture_login(response)),
                )
                await page.goto(
                    "https://www.douyin.com/",
                    wait_until="commit",
                    timeout=20_000,
                )

                # 页面脚本到达后立即触发登录，不等待所有图片和视频加载。
                try:
                    login = page.get_by_text("登录", exact=True).first
                    await login.wait_for(state="attached", timeout=5_000)
                    await login.evaluate("(element) => element.click()")
                except Exception:
                    pass

                try:
                    qr_value, expiry_seconds = await asyncio.wait_for(qr_future, timeout=12)
                except asyncio.TimeoutError as exc:
                    raise RuntimeError("抖音二维码加载超时，请重试") from exc

                encoded = (
                    qr_value.split(",", 1)[-1]
                    if "base64," in qr_value
                    else qr_value
                ).strip()
                encoded += "=" * (-len(encoded) % 4)
                try:
                    qr_bytes = base64.b64decode(encoded, validate=False)
                    with Image.open(BytesIO(qr_bytes)) as qr_image:
                        qr_image.verify()
                except Exception as exc:
                    raise RuntimeError("抖音返回的二维码格式无效") from exc
                QR_PATH.write_bytes(qr_bytes)
                self.qr_expires_at = time.monotonic() + expiry_seconds
                self.qr_prefetch_ready.set()
                self.root.after(0, self._show_qr_image)

                deadline = self.qr_expires_at
                redirect_url = ""
                while time.monotonic() < deadline:
                    if self.qr_cancel_event.is_set():
                        raise RuntimeError("登录已取消")

                    if confirmed_future.done():
                        redirect_url = confirmed_future.result()
                    cookies = await context.cookies()
                    authenticated = any(
                        cookie.get("name") in {
                            "sessionid",
                            "sessionid_ss",
                            "sid_guard",
                        }
                        and cookie.get("value")
                        for cookie in cookies
                    )
                    if authenticated:
                        tmp = STATE_PATH.with_suffix(".tmp")
                        await context.storage_state(path=str(tmp))
                        tmp.replace(STATE_PATH)
                        break

                    # 确认响应已经到达但 Cookie 尚未同步时，立即完成重定向。
                    if redirect_url:
                        try:
                            await page.goto(
                                str(redirect_url),
                                wait_until="commit",
                                timeout=8_000,
                            )
                        except Exception:
                            pass
                        redirect_url = ""
                    await page.wait_for_timeout(200)
                else:
                    raise RuntimeError(
                        "扫码确认后仍未完成登录，请在抖音 App 内完成验证后重试"
                    )

                # 登录凭证落盘后立即完成，不再让昵称接口拖慢成功反馈。
                # 通用名称会由后台回填线程在稍后安全更新。
                self.pending_account_name = await self._resolve_profile_name_fast(
                    page, context
                )
            finally:
                await browser.close()


    def _show_scanned_hint(self):
        if self.qr_label and self.qr_label.winfo_exists():
            self.qr_label.configure(text="已扫码，请在手机上确认登录", text_color=GREEN)
        self.activity_text.configure(text="已扫码，等待手机确认")


    def _show_qr_image(self):
        if not self.qr_label or not QR_PATH.exists():
            return
        image = Image.open(QR_PATH).convert("RGB")
        image.thumbnail((300, 320), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (300, 320), "#FFFFFF")
        canvas.paste(image, ((300 - image.width) // 2, (320 - image.height) // 2))
        self.qr_image = ctk.CTkImage(light_image=canvas, dark_image=canvas, size=(300, 320))
        self.qr_label.configure(image=self.qr_image, text="")
        self._update_qr_countdown()

    @staticmethod
    def _find_profile_nickname(value):
        if isinstance(value, dict):
            nickname = value.get("nickname")
            if isinstance(nickname, str) and nickname.strip():
                return nickname.strip()
            for child in value.values():
                found = DesktopApp._find_profile_nickname(child)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value:
                found = DesktopApp._find_profile_nickname(child)
                if found:
                    return found
        return ""

    async def _resolve_profile_name_fast(self, page, context):
        """Resolve the nickname concurrently without a slow page navigation."""
        endpoints = [
            (
                "https://www.douyin.com/aweme/v1/web/query/user/"
                "?device_platform=webapp&aid=6383&channel=channel_pc_web"
            ),
            (
                "https://www.douyin.com/aweme/v1/web/get/user/settings"
                "?device_platform=webapp&aid=6383"
            ),
        ]

        async def query(endpoint):
            try:
                response = await context.request.get(endpoint, timeout=2_000)
                text = (await response.text()).strip()
                payload = json.loads(text) if text.startswith("{") else {}
                return self._find_profile_nickname(payload)
            except Exception:
                return ""

        names = await asyncio.gather(*(query(endpoint) for endpoint in endpoints))
        for name in names:
            if name:
                return name
        try:
            payload = await page.evaluate(
                """() => {
                    const node = document.querySelector('#RENDER_DATA');
                    if (!node || !node.textContent) return {};
                    const raw = node.textContent.trim();
                    for (const text of [raw, decodeURIComponent(raw)]) {
                        try { return JSON.parse(text); } catch (_) {}
                    }
                    return {};
                }"""
            )
            return self._find_profile_nickname(payload)
        except Exception:
            return ""

    async def _resolve_profile_name(self, page, context):
        endpoints = [
            (
                "https://www.douyin.com/aweme/v1/web/query/user/"
                "?device_platform=webapp&aid=6383"
                "&channel=channel_pc_web&publish_video_strategy_type=2"
            ),
            (
                "https://www.douyin.com/aweme/v1/web/get/user/settings"
                "?device_platform=webapp&aid=6383"
            ),
        ]
        for endpoint in endpoints:
            try:
                response = await context.request.get(endpoint, timeout=4_000)
                text = (await response.text()).strip()
                payload = json.loads(text) if text.startswith("{") else {}
                nickname = self._find_profile_nickname(payload)
                if nickname:
                    return nickname
            except Exception:
                continue

        # 抖音会把当前用户资料写入页面的 RENDER_DATA。这个路径不依赖
        # 单独的用户资料接口，适合接口灰度或字段变化时兜底。
        try:
            await page.goto(
                "https://www.douyin.com/user/self?from_tab_name=main",
                wait_until="domcontentloaded",
                timeout=12_000,
            )
            payload = await page.evaluate(
                """() => {
                    const node = document.querySelector('#RENDER_DATA');
                    if (!node || !node.textContent) return {};
                    const raw = node.textContent.trim();
                    for (const text of [raw, decodeURIComponent(raw)]) {
                        try { return JSON.parse(text); } catch (_) {}
                    }
                    return {};
                }"""
            )
            nickname = self._find_profile_nickname(payload)
            if nickname:
                return nickname
        except Exception:
            pass

        # 最后读取个人主页标题、用户资料区及页面标题。
        try:
            candidates = await page.locator(
                'h1, [data-e2e*="user"], [class*="user-info"], '
                'meta[property="og:title"]'
            ).evaluate_all(
                "els => els.map(el => el.content || el.innerText || '')"
            )
            candidates.append(await page.title())
            ignored = {"我的", "用户", "个人主页", "登录"}
            for text in candidates:
                name = " ".join(text.split()).strip().removesuffix(" - 抖音")
                if name and name not in ignored and 1 < len(name) <= 40:
                    return name
        except Exception:
            pass
        return ""

    def _account_name_backfill_worker(self):
        # 先让二维码预热占用网络和浏览器冷启动资源，昵称迁移稍后执行。
        time.sleep(8)
        generic = [
            item for item in self.accounts
            if item.get("name", "").startswith("账号 ")
            and (ACCOUNTS_DIR / f"{item.get('id', '')}.json").exists()
        ]
        if not generic:
            return
        try:
            asyncio.run(self._backfill_account_names(generic))
        except Exception:
            return

    async def _backfill_account_names(self, accounts):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            changed = False
            unresolved = []
            try:
                for account in accounts:
                    state_path = ACCOUNTS_DIR / f"{account['id']}.json"
                    context = await browser.new_context(
                        storage_state=str(state_path),
                        locale="zh-CN",
                        viewport={"width": 1100, "height": 720},
                    )
                    try:
                        page = await context.new_page()
                        nickname = await self._resolve_profile_name(page, context)
                        if nickname:
                            used = {
                                item["name"] for item in self.accounts
                                if item["id"] != account["id"]
                            }
                            display_name = nickname
                            suffix = 2
                            while display_name in used:
                                display_name = f"{nickname} ({suffix})"
                                suffix += 1
                            account["name"] = display_name
                            changed = True
                        else:
                            unresolved.append(account["id"])
                    finally:
                        await context.close()
            finally:
                await browser.close()
            if changed:
                self._save_account_registry()
                self.root.after(0, self._refresh_account_menu)
                self.root.after(0, self._refresh_login_state)
                self.root.after(
                    0,
                    lambda: self.activity_text.configure(
                        text="已同步抖音账号名称"
                    ),
                )
            for account_id in unresolved:
                self.root.after(
                    0, lambda value=account_id: self._request_account_name(value)
                )

    def _request_account_name(self, account_id: str):
        account = next((item for item in self.accounts if item["id"] == account_id), None)
        if not account:
            return
        dialog = ctk.CTkInputDialog(
            title="设置账号名称",
            text="请输入这个账号需要显示的抖音昵称：",
        )
        name = (dialog.get_input() or "").strip()
        if not name:
            return
        used = {item["name"] for item in self.accounts if item["id"] != account_id}
        display_name = name
        suffix = 2
        while display_name in used:
            display_name = f"{name} ({suffix})"
            suffix += 1
        account["name"] = display_name
        self._save_account_registry()
        self._refresh_account_menu()
        self._refresh_login_state()

    def _rename_current_account(self):
        if not self.current_account_id:
            messagebox.showwarning(APP_NAME, "请先登录一个抖音账号。")
            return
        self._request_account_name(self.current_account_id)


    def _login_success(self):
        nickname = self.pending_account_name.strip()
        created_account = self.pending_new_account or not self.current_account_id
        if created_account:
            names = set(self._account_names())
            number = 1
            if nickname:
                account_name = nickname
                while account_name in names:
                    number += 1
                    account_name = f"{nickname} ({number})"
            else:
                account_name = f"账号 {number}"
                while account_name in names:
                    number += 1
                    account_name = f"账号 {number}"
            account = {
                "id": f"account-{time.time_ns()}",
                "name": account_name,
            }
            self.accounts.append(account)
            self.current_account_id = account["id"]
        elif nickname:
            current = next(
                (item for item in self.accounts
                 if item["id"] == self.current_account_id),
                None,
            )
            if current:
                used = {
                    item["name"] for item in self.accounts
                    if item["id"] != self.current_account_id
                }
                account_name = nickname
                suffix = 2
                while account_name in used:
                    account_name = f"{nickname} ({suffix})"
                    suffix += 1
                current["name"] = account_name
        self.pending_account_name = ""
        account_path = self._current_account_path()
        if account_path:
            shutil.copyfile(STATE_PATH, account_path)
        self.pending_new_account = False
        self._save_account_registry()
        self._refresh_account_menu()
        self._refresh_login_state()
        if created_account:
            self._reset_editor_state()
        self.risk_badge.configure(text="●  状态正常", fg_color="#173D2C", text_color="#6FE0A5")
        self.activity_text.configure(text=f"{self._current_account_name()} 登录成功")
        if self.login_window and self.login_window.winfo_exists():
            self.login_window.destroy()
        messagebox.showinfo(APP_NAME, "登录成功，登录状态已安全保存在本机。")
        current = next(
            (item for item in self.accounts if item["id"] == self.current_account_id),
            None,
        )
        if current and current["name"].startswith("账号 "):
            threading.Thread(
                target=lambda: asyncio.run(self._backfill_account_names([current])),
                daemon=True,
            ).start()

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
        self.account_name.set(self._current_account_name() if self.current_account_id else "暂无账号")

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
        self._load_account_ui_state()
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
        for path in (self._account_config_path(current), self._account_settings_path(current)):
            if path and path.exists():
                path.unlink()
        self.accounts = [a for a in self.accounts if a["id"] != current]
        # 退出后不自动切换到别的账号，界面回到明确的未登录默认态。
        self.current_account_id = ""
        self._activate_current_account()
        self.daily_plan = []
        self._load_daily_plan()
        self._save_account_registry()
        self._refresh_account_menu()
        self._refresh_login_state()
        self._reset_editor_state()
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
        except (AuthenticationError, RiskControlError) as exc:
            self.risk_stopped = True
            self.schedule_enabled.set(False)
            if isinstance(exc, AuthenticationError):
                self._invalidate_current_account()
            self.root.after(0, lambda: self._emergency_stop(str(exc)))
        except Exception as exc:
            self.root.after(0, lambda: self.activity_text.configure(
                text=f"检查失败：{str(exc)[:48]}"
            ))
        finally:
            self._set_busy(False, "空闲")

    def _plan_mode_changed(self, selected: str, save: bool = True):
        fixed = selected == "固定时间"
        state = "normal" if fixed else "disabled"
        self.fixed_hour_entry.configure(state=state)
        self.fixed_minute_entry.configure(state=state)
        self.plan_title.configure(text="每天按固定时间开始" if fixed else "每天自动生成分散时间")
        self.plan_description.configure(
            text="从设定时间开始，好友之间仍保留自然间隔"
            if fixed else "01:00–23:00 分段安排，确保当天完成"
        )
        if save and hasattr(self, "schedule_enabled"):
            self._ensure_daily_plan(force=True)
            self._save_settings_only()
            self._render_target_status()

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
            "plan_mode": self.plan_mode.get(),
            "fixed_time": self._fixed_time_value(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        account_settings = self._account_settings_path()
        if account_settings:
            shutil.copyfile(SETTINGS_PATH, account_settings)

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
        friends = self._friend_values()
        today = datetime.now().strftime("%Y-%m-%d")
        existing_names = [item.get("friend") for item in self.daily_plan]
        if not force and self.daily_plan and existing_names == friends:
            return
        now = datetime.now()
        if self.plan_mode.get() == "固定时间":
            fixed = datetime.strptime(self._fixed_time_value(), "%H:%M")
            start = now.replace(hour=fixed.hour, minute=fixed.minute, second=0, microsecond=0)
            if start <= now:
                start += timedelta(days=1)
            slots = []
            cursor = 0
            for _ in friends:
                slots.append(cursor)
                cursor += random.randint(120, 300)
        else:
            start = now.replace(hour=1, minute=0, second=0, microsecond=0)
            end = now.replace(hour=23, minute=0, second=0, microsecond=0)
            if now > start:
                start = now + timedelta(minutes=5)
            if start >= end:
                start = (now + timedelta(days=1)).replace(
                    hour=1, minute=0, second=0, microsecond=0
                )
                end = start.replace(hour=23)
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
        if not hasattr(self, "target_status_frame"):
            return
        friends = self._friend_values()
        by_name = {item.get("friend"): item for item in self.daily_plan}
        styles = {
            "success": ("已完成", "#E8F7EF", "#147A49"),
            "running": ("正在发送", "#FFF4CC", "#8A6300"),
            "failed": ("已停止", "#FDECEC", "#B52E2E"),
            "pending": ("未发送", "#FDECEC", "#B52E2E"),
        }
        for row_data in self.friend_rows:
            friend = row_data["value"].get().strip()
            badge = row_data["badge"]
            if not friend:
                badge.configure(text="未设置", fg_color="#EFEFED", text_color=MUTED)
                continue
            if not row_data["enabled"].get():
                badge.configure(text="已取消", fg_color="#EFEFED", text_color=MUTED)
                continue
            item = by_name.get(friend, {})
            status = item.get("status", "pending")
            label, badge_bg, badge_text = styles.get(status, styles["pending"])
            badge.configure(text=label, fg_color=badge_bg, text_color=badge_text)


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
            selected = all_messages[0] if self.message_mode.get() == "默认第一条" else random.choice(all_messages)
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
    root = None
    try:
        root = ctk.CTk()
        DesktopApp(root)
        root.mainloop()
    except Exception as exc:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        error_path = DATA_DIR / "startup-error.log"
        error_path.write_text(
            f"{datetime.now().isoformat()}\n{type(exc).__name__}: {exc}\n",
            encoding="utf-8",
        )
        try:
            if root is not None:
                root.withdraw()
            messagebox.showerror(
                APP_NAME,
                f"软件启动失败，错误信息已保存。\n\n{type(exc).__name__}: {exc}",
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
