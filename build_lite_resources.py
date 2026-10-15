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
from lite_spr_workflow import SPR_DIRECTORY, export_sprites, generate_bins, optimize_sprites, summary


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
        self.client_ready = False
        self.client = tk.StringVar(value=str(client_dir) if client_dir else "")
        self.client.trace_add("write", lambda *_: self.invalidate())
        self.progress = tk.DoubleVar(value=0)
        self.activity = tk.StringVar(value="尚未开始")
        self.started_at = 0.0
        self.last_progress_at = 0.0
        self.status = tk.StringVar(value="导出 SPR 请先选择游戏目录；生成 BIN、批量 SPR 优化可直接使用")
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
        ttk.Label(top, text="导出 SPR：必须具备方向 3、7 × 动作 0、3 四种组合，仅保留这些组合。\n"
                  "SPR 输出到项目 spr，BIN 输出到同级 data；调色板放在 data\\Palet_1.sap。").pack(anchor="w", pady=8)
        buttons = ttk.Frame(top)
        buttons.pack(anchor="w")
        self.start_button = ttk.Button(buttons, text="导出 SPR", command=lambda: self.start("export"), state="disabled")
        self.start_button.pack(side="left")
        self.bin_button = ttk.Button(buttons, text="生成 BIN", command=lambda: self.start("generate"))
        self.bin_button.pack(side="left", padx=(8, 0))
        self.optimize_button = ttk.Button(buttons, text="批量 SPR 优化", command=lambda: self.start("optimize"))
        self.optimize_button.pack(side="left", padx=(8, 0))
        ttk.Progressbar(top, variable=self.progress, maximum=100).pack(fill="x", pady=(12, 6))
        ttk.Label(top, textvariable=self.status, wraplength=800).pack(anchor="w")
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

    def invalidate(self):
        self.client_ready = False
        self.start_button.configure(state="disabled")

    def check(self):
        self.invalidate()
        try:
            if not self.client.get().strip():
                raise ValueError("请先选择游戏客户端目录")
            data, files = locate_resources(Path(self.client.get().strip()))
            self.append(f"资源目录：{data}")
            self.append(f"SPR 导出目录：{SPR_DIRECTORY}")
            for path in files.values():
                self.append(f"已找到 {path.name}：{path.stat().st_size:,} 字节")
            self.status.set("资源文件齐全，可分别导出 SPR 或从现有 SPR 生成 BIN")
            self.start_button.configure(state="normal")
            self.bin_button.configure(state="normal")
            self.client_ready = True
        except Exception as error:
            self.status.set("资源检查失败")
            self.append(f"检查失败：{error}")

    def start(self, operation):
        source = Path(self.client.get().strip())
        packages = []
        skipped = []
        if operation == "optimize":
            selected = filedialog.askopenfilenames(title="选择原始 SPR 文件（可多选）", filetypes=[("SPR 资源包", "*.spr")], parent=self)
            if not selected:
                return
            targets = set()
            for value in selected:
                package = Path(value)
                target = SPR_DIRECTORY / package.name
                overwrite = False
                if target.exists() or target in targets:
                    overwrite = messagebox.askyesno("是否覆盖", f"目标文件已存在或本批有同名目标：\n{target}\n\n当前源文件：{package}\n是否覆盖？", parent=self)
                    if not overwrite:
                        skipped.append((package, "用户取消覆盖同名文件"))
                        continue
                packages.append((package, overwrite))
                targets.add(target)
        title = {"export": "导出 SPR", "optimize": "批量 SPR 优化", "generate": "生成 BIN"}[operation]
        details = (f"导出全部索引形象，缺少方向 3、7 × 动作 0、3 任一组合则跳过。\n目标：{SPR_DIRECTORY}"
                   if operation == "export" else
                   f"从 {SPR_DIRECTORY} 的全部 SPR 包生成 BIN，不重新导出。\n目标：{SPR_DIRECTORY.parent / 'data'}")
        if operation == "optimize":
            details = f"批量优化 {len(packages) + len(skipped)} 个 SPR 文件。\n必须具备 3/0、3/3、7/0、7/3，仅保留这四种组合；错误跳过。\n目标：{SPR_DIRECTORY}"
        context = f"客户端：{source}\n\n" if operation == "export" else ""
        if operation != "optimize" and not messagebox.askyesno(title, f"{context}{details}\n同名文件覆盖，源客户端不修改。\n确定开始？", parent=self):
            return
        self.busy = True
        self.started_at = self.last_progress_at = time.monotonic()
        self.progress.set(0)
        for widget in (self.entry, self.choose_button, self.start_button, self.optimize_button, self.bin_button):
            widget.configure(state="disabled")
        self.status.set(f"正在{title}，请等待……")
        self.append(details)

        def work():
            try:
                report = lambda value, text: self.events.put(("progress", (value, text, time.monotonic())))
                if operation == "optimize":
                    result = optimize_sprites(packages, report, skipped)
                else:
                    result = generate_bins(report) if operation == "generate" else export_sprites(source, report)
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
                for widget in (self.entry, self.choose_button, self.bin_button, self.start_button, self.optimize_button):
                    widget.configure(state="normal")
                if not self.client_ready:
                    self.invalidate()
                if kind == "done":
                    self.progress.set(100)
                    self.status.set({"export": "SPR 导出完成", "generate": "BIN 生成完成", "optimize_batch": "批量 SPR 优化完成"}[value["operation"]])
                    self.append(summary(value))
                    if value["operation"] in ("export", "optimize_batch") and value["failures"]:
                        messagebox.showwarning("处理完成，存在异常", summary(value), parent=self)
                    else:
                        messagebox.showinfo("处理完成", summary(value), parent=self)
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
