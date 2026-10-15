#!/usr/bin/env python3
"""Export filtered SPR packages and regenerate LITE resource files."""
from __future__ import annotations

import argparse
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

from client_data import resources
from lite_spr_workflow import SPR_DIRECTORY, build_lite, summary


def locate_resources(source: Path):
    data = source.expanduser().resolve() / "data"
    return data, resources(data)


class LiteWindow(tk.Tk):
    def __init__(self, client_dir: Path | None = None):
        super().__init__()
        self.title("LITE 客户端资源精简")
        self.geometry("850x590")
        self.minsize(700, 450)
        self.events = queue.Queue()
        self.busy = False
        self.test_only = tk.BooleanVar(value=True)
        self.client = tk.StringVar(value=str(client_dir) if client_dir else "")
        self.client.trace_add("write", lambda *_: self.start_button.configure(state="disabled"))
        self.progress = tk.DoubleVar(value=0)
        self.activity = tk.StringVar(value="尚未开始")
        self.started_at = 0.0
        self.last_progress_at = 0.0
        self.status = tk.StringVar(value="请选择游戏客户端目录，选择后自动检查资源文件")
        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text="游戏客户端目录").pack(anchor="w")
        row = ttk.Frame(top)
        row.pack(fill="x", pady=6)
        self.entry = ttk.Entry(row, textvariable=self.client)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _event: self.check() if not self.busy else None)
        self.choose_button = ttk.Button(row, text="选择目录", command=self.choose)
        self.choose_button.pack(side="left", padx=(8, 0))
        ttk.Label(top, text="仅保留方向 3、7 × 动作 0、3，导出 SPR 后生成独立的 BIN 文件。\n"
                  "源客户端不修改。SPR 输出到项目 spr 文件夹，BIN 输出到同级 data 文件夹。").pack(anchor="w", pady=8)
        self.start_button = ttk.Button(top, text="导出 SPR 并生成 BIN", command=self.start, state="disabled")
        self.start_button.pack(anchor="w")
        self.test_check = ttk.Checkbutton(top, text="测试模式：仅导出索引前 10 个形象，并生成只含本批形象的 BIN", variable=self.test_only)
        self.test_check.pack(anchor="w", pady=(6, 0))
        ttk.Progressbar(top, variable=self.progress, maximum=100).pack(fill="x", pady=(12, 6))
        ttk.Label(top, textvariable=self.status, wraplength=800).pack(anchor="w")
        ttk.Label(top, textvariable=self.activity).pack(anchor="w", pady=(4, 0))
        self.log = scrolledtext.ScrolledText(self, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.after(100, self.poll)
        if client_dir is not None:
            self.after_idle(self.check)

    def append(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def choose(self):
        selected = filedialog.askdirectory(title="选择游戏客户端目录（其中应有 data 文件夹）", parent=self)
        if selected:
            self.client.set(selected)
            self.check()

    def check(self):
        self.start_button.configure(state="disabled")
        try:
            if not self.client.get().strip():
                raise ValueError("请先选择游戏客户端目录")
            data, files = locate_resources(Path(self.client.get().strip()))
            self.append(f"资源目录：{data}")
            self.append(f"SPR 导出目录：{SPR_DIRECTORY}")
            for path in files.values():
                self.append(f"已找到 {path.name}：{path.stat().st_size:,} 字节")
            self.status.set("四个资源文件齐全，可以开始精简")
            self.start_button.configure(state="normal")
        except Exception as error:
            self.status.set("资源检查失败")
            self.append(f"检查失败：{error}")

    def start(self):
        source = Path(self.client.get().strip())
        limit = 10 if self.test_only.get() else None
        if not messagebox.askyesno("确认导出与生成", f"客户端：{source}\n\n"
                                  + ("测试模式：只导出索引前 10 个形象。\n" if limit else "全部处理模式。\n")
                                  +
                                  "仅保留方向 3、7 和动作 0、3。\n"
                                  "源客户端不修改。SPR 导出到项目 spr 目录。\n确定开始？", parent=self):
            return
        self.busy = True
        self.started_at = self.last_progress_at = time.monotonic()
        self.progress.set(0)
        for widget in (self.entry, self.choose_button, self.start_button, self.test_check):
            widget.configure(state="disabled")
        self.status.set("正在整理动画，请等待……")
        self.append("开始导出：无匹配动作的形象跳过；缺少的方向或动作不会补造。")
        self.append("测试模式：只处理前 10 个形象，其他形象不读取、不重建。" if limit else "全部处理模式")

        def work():
            try:
                result = build_lite(source, lambda value, text: self.events.put(("progress", (value, text, time.monotonic()))), limit=limit)
                self.events.put(("done", result))
            except Exception as error:
                self.events.put(("error", str(error)))

        threading.Thread(target=work, daemon=False).start()

    def poll(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                percent, text, timestamp = value
                self.last_progress_at = timestamp
                self.progress.set(percent)
                self.status.set(text)
                self.append(text)
            else:
                self.busy = False
                self.activity.set(f"用时 {time.monotonic() - self.started_at:.1f} 秒")
                for widget in (self.entry, self.choose_button, self.test_check):
                    widget.configure(state="normal")
                if kind == "done":
                    self.progress.set(100)
                    self.status.set("SPR 导出与 BIN 生成完成，源客户端未修改")
                    self.append(summary(value))
                    messagebox.showinfo("精简完成", summary(value), parent=self)
                else:
                    self.status.set("精简失败，请查看日志")
                    self.append(f"失败：{value}")
                    messagebox.showerror("精简失败", value, parent=self)
        if self.busy:
            now = time.monotonic()
            idle = now - self.last_progress_at
            detail = "" if idle < 2 else "；当前步骤尚未返回新进度"
            self.activity.set(f"进度 {self.progress.get():.1f}% · 已用 {now - self.started_at:.1f} 秒 · 距上次进度 {idle:.1f} 秒{detail}")
        self.after(100, self.poll)

    def close(self):
        if self.busy:
            messagebox.showinfo("正在处理", "请等待精简结束后再关闭窗口。", parent=self)
            return
        self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 LITE SPR 并生成独立 BIN，保留方向 3/7、动作 0/3")
    parser.add_argument("--client-dir", type=Path)
    args = parser.parse_args()
    LiteWindow(args.client_dir).mainloop()


if __name__ == "__main__":
    main()
