from __future__ import annotations

import asyncio
import json
import os
import platform
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

from app.main import run
from playwright.async_api import async_playwright

APP_NAME = "SBS 好友互动助手"
DATA_DIR = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "Library/Application Support" if platform.system() == "Darwin" else Path.home() / ".local/share")) / "SBS-Spark"
CONFIG_PATH = DATA_DIR / "config.json"
STATE_PATH = DATA_DIR / "storage-state.json"
ARTIFACTS_DIR = DATA_DIR / "artifacts"
SETTINGS_PATH = DATA_DIR / "desktop-settings.json"

DEFAULT_CONFIG = {
    "friends": ["好友昵称"],
    "messages": [{"type": "text", "value": "今天也要开心呀 ✨"}],
    "send_interval_seconds": {"min": 3, "max": 8},
    "prevent_duplicates": False,
    "target_open_retries": 1,
    "target_open_timeout_seconds": 15,
}

class DesktopApp:
    def __init__(self, root: tk.Tk):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        os.chdir(DATA_DIR)
        os.environ["TASK_CONFIG"] = str(CONFIG_PATH)
        os.environ["ARTIFACTS_DIR"] = str(ARTIFACTS_DIR)
        os.environ["DOUYIN_STORAGE_STATE"] = str(STATE_PATH)
        os.environ.setdefault("HEADLESS", "true")
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
        if not CONFIG_PATH.exists():
            CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")

        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("860x650")
        self.root.minsize(720, 560)
        self.busy = False
        self.login_ready = threading.Event()
        self.last_schedule_day = ""
        self._build()
        self._load_config()
        self._load_settings()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self._scheduler_thread.start()
        self._log(f"数据保存在：{DATA_DIR}")

    def _build(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Accent.TButton", foreground="#111111", background="#F4C430", font=("Arial", 11, "bold"))

        header = tk.Frame(self.root, bg="#F4C430", height=72)
        header.pack(fill="x")
        tk.Label(header, text="SBS", bg="#111111", fg="#F4C430", font=("Arial", 18, "bold"), padx=14, pady=8).pack(side="left", padx=18, pady=14)
        tk.Label(header, text="好友互动助手", bg="#F4C430", fg="#111111", font=("Arial", 20, "bold")).pack(side="left")
        self.status = tk.StringVar(value="空闲")
        tk.Label(header, textvariable=self.status, bg="#26834A", fg="white", padx=12, pady=6).pack(side="right", padx=18)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=16, pady=14)
        config_tab = ttk.Frame(notebook)
        log_tab = ttk.Frame(notebook)
        notebook.add(config_tab, text="任务设置")
        notebook.add(log_tab, text="运行记录")

        form = ttk.Frame(config_tab, padding=12)
        form.pack(fill="both", expand=True)
        ttk.Label(form, text="好友昵称（每行一个）").grid(row=0, column=0, sticky="w")
        self.friends = scrolledtext.ScrolledText(form, height=8, wrap="word")
        self.friends.grid(row=1, column=0, sticky="nsew", padx=(0, 10), pady=(6, 14))
        ttk.Label(form, text="随机文字（每行一条）").grid(row=0, column=1, sticky="w")
        self.messages = scrolledtext.ScrolledText(form, height=8, wrap="word")
        self.messages.grid(row=1, column=1, sticky="nsew", pady=(6, 14))

        schedule_box = ttk.LabelFrame(form, text="本地定时", padding=10)
        schedule_box.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Label(schedule_box, text="每天运行时间").pack(side="left")
        self.schedule_time = tk.StringVar(value="21:00")
        ttk.Entry(schedule_box, textvariable=self.schedule_time, width=8).pack(side="left", padx=8)
        self.schedule_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(schedule_box, text="启用（软件需保持运行）", variable=self.schedule_enabled).pack(side="left", padx=8)

        actions = ttk.Frame(form)
        actions.grid(row=3, column=0, columnspan=2, sticky="ew", pady=18)
        ttk.Button(actions, text="保存设置", command=self.save_config).pack(side="left", padx=4)
        ttk.Button(actions, text="扫码登录", command=self.start_login, style="Accent.TButton").pack(side="left", padx=4)
        ttk.Button(actions, text="完成扫码", command=self.confirm_login).pack(side="left", padx=4)
        ttk.Button(actions, text="检查任务", command=lambda: self.start_run(True)).pack(side="left", padx=4)
        ttk.Button(actions, text="立即发送", command=lambda: self.start_run(False)).pack(side="left", padx=4)
        ttk.Button(actions, text="打开数据目录", command=self.open_data_dir).pack(side="right", padx=4)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)
        form.rowconfigure(1, weight=1)

        self.log = scrolledtext.ScrolledText(log_tab, state="disabled", bg="#181818", fg="#D8D8D8", insertbackground="white", font=("Menlo", 11))
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

    def _load_config(self):
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        self.friends.delete("1.0", "end")
        self.friends.insert("1.0", "\n".join(data.get("friends", [])))
        texts = [m.get("value") or m.get("content", "") for m in data.get("messages", []) if m.get("type") == "text"]
        self.messages.delete("1.0", "end")
        self.messages.insert("1.0", "\n".join(texts))

    def _load_settings(self):
        if SETTINGS_PATH.exists():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            self.schedule_time.set(data.get("schedule_time", "21:00"))
            self.schedule_enabled.set(bool(data.get("schedule_enabled", False)))

    def save_config(self):
        friends = [x.strip() for x in self.friends.get("1.0", "end").splitlines() if x.strip()]
        messages = [x.strip() for x in self.messages.get("1.0", "end").splitlines() if x.strip()]
        if not friends or not messages:
            messagebox.showwarning(APP_NAME, "好友和消息都至少填写一项。")
            return False
        data = dict(DEFAULT_CONFIG)
        data["friends"] = friends
        data["messages"] = [{"type": "text", "value": x} for x in messages]
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        SETTINGS_PATH.write_text(json.dumps({"schedule_time": self.schedule_time.get().strip(), "schedule_enabled": self.schedule_enabled.get()}, ensure_ascii=False, indent=2), encoding="utf-8")
        self._log("设置已保存。")
        return True

    def start_login(self):
        if self.busy:
            return
        self.login_ready.clear()
        self._set_busy(True, "等待扫码")
        threading.Thread(target=self._login_worker, daemon=True).start()

    def confirm_login(self):
        self.login_ready.set()

    def _login_worker(self):
        try:
            asyncio.run(self._login())
            self.root.after(0, lambda: messagebox.showinfo(APP_NAME, "登录状态保存成功。"))
            self._log("扫码登录成功。")
        except Exception as exc:
            self._log(f"登录失败：{exc}")
            self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"登录失败：{exc}"))
        finally:
            self._set_busy(False, "空闲")

    async def _login(self):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False)
            context = await browser.new_context(locale="zh-CN")
            page = await context.new_page()
            await page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
            self._log("请在弹出的浏览器中扫码并在手机确认，然后点击软件中的“完成扫码”。")
            await asyncio.to_thread(self.login_ready.wait)
            await page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
            login = page.get_by_text("登录", exact=True)
            if await login.count() and await login.first.is_visible():
                raise RuntimeError("未检测到登录成功")
            tmp = STATE_PATH.with_suffix(".tmp")
            await context.storage_state(path=str(tmp))
            await browser.close()
            tmp.replace(STATE_PATH)

    def start_run(self, dry_run: bool):
        if self.busy or not self.save_config():
            return
        if not STATE_PATH.exists():
            messagebox.showwarning(APP_NAME, "请先扫码登录。")
            return
        self._set_busy(True, "检查中" if dry_run else "发送中")
        threading.Thread(target=self._run_worker, args=(dry_run,), daemon=True).start()

    def _run_worker(self, dry_run: bool):
        try:
            code = asyncio.run(run(dry_run=dry_run))
            self._log(("检查" if dry_run else "发送") + f"完成，退出码 {code}。")
        except Exception as exc:
            self._log(f"运行失败：{exc}")
            self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"运行失败：{exc}"))
        finally:
            self._set_busy(False, "空闲")

    def _scheduler_loop(self):
        while True:
            try:
                now = datetime.now()
                target = self.schedule_time.get().strip()
                if self.schedule_enabled.get() and now.strftime("%H:%M") == target and self.last_schedule_day != now.strftime("%Y-%m-%d"):
                    self.last_schedule_day = now.strftime("%Y-%m-%d")
                    self.root.after(0, lambda: self.start_run(False))
            except Exception:
                pass
            time.sleep(20)

    def open_data_dir(self):
        if platform.system() == "Windows":
            os.startfile(DATA_DIR)
        elif platform.system() == "Darwin":
            os.system(f'open "{DATA_DIR}"')
        else:
            os.system(f'xdg-open "{DATA_DIR}"')

    def _set_busy(self, value: bool, label: str):
        self.busy = value
        self.root.after(0, lambda: self.status.set(label))

    def _log(self, text: str):
        def append():
            self.log.configure(state="normal")
            self.log.insert("end", f"[{datetime.now():%H:%M:%S}] {text}\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.root.after(0, append)

def main():
    root = tk.Tk()
    DesktopApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
