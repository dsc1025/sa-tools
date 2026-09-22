#!/usr/bin/env python3
"""Read-only CrossGate animation inspector and reviewed .spr exporter."""
from __future__ import annotations

import hashlib
import json
import struct
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from cg_to_stoneage_package import (
    CG_TO_SA_ACTION, CG_TO_SA_DIRECTION, SETS, anime_rows, build, cg_palette,
    paths, read_animation, read_graphic, stoneage_actions,
)


DEFAULT_CG = Path(r"C:\Work\CG\CrossGate\bin")
DEFAULT_SA = Path(r"C:\Work\SA\SA2.5\stoneage2.5\data")
CANVAS_W, CANVAS_H = 565, 390


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class CGViewer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("魔力宝贝资源审查与 SPR 导出")
        self.geometry("800x600")
        self.resizable(False, False)
        self.cg_var = tk.StringVar(value=str(DEFAULT_CG))
        self.sa_var = tk.StringVar(value=str(DEFAULT_SA))
        self.set_var = tk.StringVar(value="base")
        self.anime_var = tk.StringVar()
        self.direction_var = tk.StringVar(value="0")
        self.action_var = tk.StringVar(value="0")
        self.status_var = tk.StringVar(value="选择资源集后扫描；程序不会修改任何客户端文件。")
        self.rows: list[tuple[int, int, int]] = []
        self.current_number: int | None = None
        self.current_actions: list[dict] = []
        self.current_action_index = 0
        self.current_frame_index = 0
        self.current_paths: tuple[Path, Path, Path, Path] | None = None
        self.colours: list[tuple[int, int, int]] = []
        self.photo: ImageTk.PhotoImage | None = None
        self.playing = False
        self.play_job: str | None = None
        self._build_ui()
        self.after_idle(self.scan)

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="CG bin 目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.cg_var, width=67).grid(row=0, column=1, columnspan=7, sticky="ew", padx=5)
        ttk.Button(top, text="选择目录", command=self.choose_cg).grid(row=0, column=8)
        ttk.Label(top, text="石器参考 data（只读）").grid(row=1, column=0, sticky="w")
        ttk.Entry(top, textvariable=self.sa_var, width=67).grid(row=1, column=1, columnspan=7, sticky="ew", padx=5)
        ttk.Button(top, text="选择目录", command=self.choose_sa).grid(row=1, column=8)
        ttk.Label(top, text="资源集").grid(row=2, column=0, sticky="w")
        sets = ttk.Combobox(top, textvariable=self.set_var, values=list(SETS), state="readonly", width=9)
        sets.grid(row=2, column=1, sticky="w", padx=5)
        sets.bind("<<ComboboxSelected>>", lambda _e: self.scan())
        ttk.Label(top, text="动画编号").grid(row=2, column=2, sticky="e")
        self.anime_box = ttk.Combobox(top, textvariable=self.anime_var, width=11)
        self.anime_box.grid(row=2, column=3, padx=5)
        self.anime_box.bind("<<ComboboxSelected>>", self.select_sprite)
        self.anime_box.bind("<Return>", self.select_sprite)
        ttk.Label(top, text="方向").grid(row=2, column=4, sticky="e")
        self.direction_box = ttk.Combobox(top, textvariable=self.direction_var, width=5, state="readonly")
        self.direction_box.grid(row=2, column=5, padx=5)
        self.direction_box.bind("<<ComboboxSelected>>", lambda _e: self.refresh_action_choices())
        ttk.Label(top, text="动作").grid(row=2, column=6, sticky="e")
        self.action_box = ttk.Combobox(top, textvariable=self.action_var, width=5, state="readonly")
        self.action_box.grid(row=2, column=7, padx=5)
        self.action_box.bind("<<ComboboxSelected>>", lambda _e: self.select_action())
        ttk.Button(top, text="读取", command=self.select_sprite).grid(row=2, column=8)

        content = ttk.Frame(self, padding=(8, 0))
        content.pack(fill="both", expand=True)
        list_frame = ttk.Frame(content, width=195)
        list_frame.pack(side="left", fill="y", padx=(0, 8))
        ttk.Label(list_frame, text="动画列表").pack(anchor="w")
        self.sprite_tree = ttk.Treeview(list_frame, columns=("id", "actions"), show="headings", selectmode="browse", height=19)
        self.sprite_tree.heading("id", text="编号")
        self.sprite_tree.heading("actions", text="动作")
        self.sprite_tree.column("id", width=105, anchor="e")
        self.sprite_tree.column("actions", width=48, anchor="e")
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.sprite_tree.yview)
        self.sprite_tree.configure(yscrollcommand=list_scroll.set)
        self.sprite_tree.pack(side="left", fill="y")
        list_scroll.pack(side="right", fill="y")
        self.sprite_tree.bind("<<TreeviewSelect>>", self.select_sprite)
        self.canvas = tk.Canvas(content, width=CANVAS_W, height=CANVAS_H, bg="#303030", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        info = ttk.Frame(self, padding=(8, 4))
        info.pack(fill="x")
        self.action_summary = ttk.Label(info, text="尚未选择动画")
        self.action_summary.pack(side="left")
        self.frame_label = ttk.Label(info, text="未选择帧")
        self.frame_label.pack(side="right")

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Button(bottom, text="导出 .spr", command=self.export).pack(side="left", padx=3)
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", padx=10)

    def choose_cg(self) -> None:
        selected = filedialog.askdirectory(title="选择魔力宝贝 bin 目录", initialdir=self.cg_var.get())
        if selected:
            self.cg_var.set(selected)
            self.scan()

    def choose_sa(self) -> None:
        selected = filedialog.askdirectory(title="选择石器时代 data 目录（仅作为格式参考）", initialdir=self.sa_var.get())
        if selected:
            self.sa_var.set(selected)

    def scan(self) -> None:
        try:
            self.stop()
            self.current_paths = paths(Path(self.cg_var.get()), self.set_var.get())
            self.rows = anime_rows(self.current_paths[2])
            self.reload_palette(refresh=False)
            self.populate_sprites()
            unique_count = len({row[0] for row in self.rows})
            duplicate_count = len(self.rows) - unique_count
            self.status_var.set(f"已扫描 {unique_count} 个动画；合并 {duplicate_count} 条重复索引；所有源文件均为只读。")
        except Exception as error:
            self.status_var.set(f"扫描失败：{error}")
            messagebox.showerror("扫描失败", str(error))

    def reload_palette(self, refresh: bool = True) -> None:
        self.colours = cg_palette(Path(self.cg_var.get()) / "pal" / "palet_00.cgp")
        if refresh and self.current_number is not None:
            self.show_frame()

    def populate_sprites(self) -> None:
        # Official CG indexes may repeat animation ids. Match the decoder's
        # last-record-wins rule and show each logical resource only once.
        unique = {number: (offset, count) for number, offset, count in self.rows}
        values = [str(number) for number in sorted(unique)]
        self.anime_box["values"] = values
        self.sprite_tree.delete(*self.sprite_tree.get_children())
        for row_index, number in enumerate(sorted(unique)):
            _offset, count = unique[number]
            self.sprite_tree.insert("", "end", iid=f"row-{row_index}", values=(number, count))
        if values and self.anime_var.get() not in values:
            self.anime_var.set(values[0])

    def select_sprite(self, _event=None) -> None:
        selected = self.sprite_tree.selection()
        if selected and _event is not None and getattr(_event, "widget", None) is self.sprite_tree:
            self.anime_var.set(str(self.sprite_tree.item(selected[0], "values")[0]))
        if not self.anime_var.get().strip() or self.current_paths is None:
            return
        try:
            self.stop()
            self.current_number = int(self.anime_var.get().strip())
            self.current_actions = read_animation(self.current_paths[2], self.current_paths[3], self.current_number)
            self.current_action_index = 0
            self.current_frame_index = 0
            if self.current_actions:
                directions = sorted({item["direction"] for item in self.current_actions})
                self.direction_box["values"] = [str(value) for value in directions]
                self.direction_var.set("0" if 0 in directions else str(directions[0]))
                self.refresh_action_choices()
            unique = len({f["graphic"] for a in self.current_actions for f in a["frames"]})
            self.status_var.set(f"动画 {self.current_number}：{len(self.current_actions)} 个动作，{unique} 个唯一图像。")
        except Exception as error:
            self.status_var.set(f"读取失败：{error}")

    def refresh_action_choices(self) -> None:
        if not self.current_actions:
            return
        direction = int(self.direction_var.get())
        values = sorted({item["action"] for item in self.current_actions if item["direction"] == direction})
        self.action_box["values"] = [str(value) for value in values]
        if not values:
            return
        current = int(self.action_var.get()) if self.action_var.get().isdigit() else values[0]
        self.action_var.set(str(current if current in values else values[0]))
        self.select_action()

    def select_action(self) -> None:
        self.stop()
        direction = int(self.direction_var.get())
        action_code = int(self.action_var.get())
        self.current_action_index = next(
            index for index, item in enumerate(self.current_actions)
            if item["direction"] == direction and item["action"] == action_code
        )
        self.current_frame_index = 0
        item = self.current_actions[self.current_action_index]
        self.action_summary.configure(text=f"方向 {direction} · 动作 {action_code} · {len(item['frames'])} 帧 · {item['duration']} ms")
        self.start_playback()

    def current_frame(self) -> tuple[dict, dict]:
        action = self.current_actions[self.current_action_index]
        frame = action["frames"][self.current_frame_index % len(action["frames"])]
        return action, frame

    def show_frame(self) -> None:
        if self.current_paths is None or not self.current_actions:
            return
        try:
            action, frame = self.current_frame()
            image, info = read_graphic(self.current_paths[0], self.current_paths[1], frame["graphic"], self.colours)
            zoom = 1
            view = Image.new("RGB", (CANVAS_W, CANVAS_H), "#303030")
            draw = ImageDraw.Draw(view)
            anchor = (CANVAS_W // 2, CANVAS_H // 2)
            left = anchor[0] + (info.x + frame["x"]) * zoom
            top = anchor[1] + (info.y + frame["y"]) * zoom
            view.paste(image, (left, top), image)
            draw.line((anchor[0] - 12, anchor[1], anchor[0] + 12, anchor[1]), fill="#ff4060")
            draw.line((anchor[0], anchor[1] - 12, anchor[0], anchor[1] + 12), fill="#ff4060")
            draw.rectangle((left, top, left + image.width - 1, top + image.height - 1), outline="#51b8ff")
            self.photo = ImageTk.PhotoImage(view)
            self.canvas.delete("all")
            self.canvas.create_image(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2, image=self.photo)
            self.frame_label.configure(
                text=f"帧 {self.current_frame_index + 1}/{len(action['frames'])} · Graphic {frame['graphic']} · {info.width}×{info.height}"
            )
        except Exception as error:
            self.status_var.set(f"预览失败：{error}")

    def start_playback(self) -> None:
        self.stop()
        if self.current_actions:
            self.playing = True
            self.play_next()

    def play_next(self) -> None:
        if not self.playing:
            return
        action = self.current_actions[self.current_action_index]
        self.show_frame()
        self.current_frame_index = (self.current_frame_index + 1) % len(action["frames"])
        delay = max(35, min(1000, round(action["duration"] / len(action["frames"]))))
        self.play_job = self.after(delay, self.play_next)

    def stop(self) -> None:
        self.playing = False
        if self.play_job:
            self.after_cancel(self.play_job)
            self.play_job = None

    def audit(self) -> dict:
        if self.current_number is None or self.current_paths is None:
            raise ValueError("请先选择一个动画资源")
        issues: list[dict] = []
        directions = sorted({a["direction"] for a in self.current_actions})
        action_codes = sorted({a["action"] for a in self.current_actions})
        graphics = sorted({f["graphic"] for a in self.current_actions for f in a["frames"]})
        decoded = 0
        dimensions: list[tuple[int, int]] = []
        for action_index, action in enumerate(self.current_actions):
            if not action["frames"]:
                issues.append({"severity": "error", "where": f"action[{action_index}]", "message": "动作没有帧"})
            if action["duration"] <= 0:
                issues.append({"severity": "error", "where": f"action[{action_index}]", "message": "动作时长不是正数"})
        for graphic in graphics:
            try:
                _image, info = read_graphic(self.current_paths[0], self.current_paths[1], graphic, self.colours)
                decoded += 1
                dimensions.append((info.width, info.height))
                if info.width <= 0 or info.height <= 0:
                    issues.append({"severity": "error", "where": f"graphic {graphic}", "message": "图像尺寸无效"})
            except Exception as error:
                issues.append({"severity": "error", "where": f"graphic {graphic}", "message": str(error)})
        if set(directions) != set(range(8)):
            issues.append({"severity": "warning", "where": "animation", "message": f"方向集合不是完整 0..7：{directions}"})
        mapped_actions, skipped_actions = stoneage_actions(self.current_actions)
        mapped_pairs = {(item["direction"], item["action"]) for item in mapped_actions}
        expected_pairs = {(direction, action) for direction in range(8) for action in (0, 1, 2, 3, 4, 9, 10)}
        missing_pairs = sorted(expected_pairs - mapped_pairs)
        if missing_pairs:
            issues.append({
                "severity": "warning", "where": "mapping",
                "message": f"转换后缺少 {len(missing_pairs)} 个石器方向/动作组合。",
            })
        if self.set_var.get() not in ("base", "ex"):
            issues.append({
                "severity": "warning", "where": "palette",
                "message": "此版本可能使用动画级隐藏调色板；必须逐动作目视确认颜色后再导出。",
            })
        return {
            "format": "crossgate-sprite-review", "version": 1,
            "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": {"directory": self.cg_var.get(), "set": self.set_var.get(), "anime": self.current_number, "palette": 0},
            "summary": {
                "actions": len(self.current_actions), "directions": directions, "action_codes": action_codes,
                "frame_references": sum(len(a["frames"]) for a in self.current_actions),
                "unique_graphics": len(graphics), "decoded_graphics": decoded,
                "width_range": [min((d[0] for d in dimensions), default=0), max((d[0] for d in dimensions), default=0)],
                "height_range": [min((d[1] for d in dimensions), default=0), max((d[1] for d in dimensions), default=0)],
                "export_actions": len(mapped_actions), "skipped_cg_actions": len(skipped_actions),
            },
            "mapping": {
                "cg_to_sa_direction": CG_TO_SA_DIRECTION,
                "cg_to_sa_action": CG_TO_SA_ACTION,
                "idle_rule": "CG action 0 preferred; CG action 1 is fallback",
                "missing_sa_pairs": missing_pairs,
                "skipped_cg_pairs": [{"direction": a["direction"], "action": a["action"]} for a in skipped_actions],
            },
            "actions": [{"index": i, "direction": a["direction"], "action": a["action"], "duration_ms": a["duration"], "frames": len(a["frames"])} for i, a in enumerate(self.current_actions)],
            "issues": issues,
        }

    def audit_dialog(self) -> None:
        try:
            report = self.audit()
            summary = report["summary"]
            errors = sum(i["severity"] == "error" for i in report["issues"])
            warnings = sum(i["severity"] == "warning" for i in report["issues"])
            messagebox.showinfo(
                "完整审查结果",
                f"动作：{summary['actions']}\n方向：{summary['directions']}\n帧引用：{summary['frame_references']}\n"
                f"唯一图像：{summary['unique_graphics']}\n成功解码：{summary['decoded_graphics']}\n"
                f"尺寸范围：{summary['width_range']} × {summary['height_range']}\n\n错误：{errors}　警告：{warnings}",
            )
            self.status_var.set(f"审查完成：{errors} 个错误，{warnings} 个警告。")
        except Exception as error:
            messagebox.showerror("审查失败", str(error))

    def export(self) -> None:
        try:
            if self.current_number is None:
                raise ValueError("请先选择一个动画编号")
            target_name = filedialog.asksaveasfilename(
                title="导出 SPR", defaultextension=".spr", initialfile=f"{self.current_number}.spr",
                filetypes=[("Stone Age SPR package", "*.spr")],
            )
            if not target_name:
                return
            target = Path(target_name)
            # A numeric filename is the manual-import target id. For a custom
            # non-numeric name, retain the source CG id in the package.
            sprite = self.current_number
            if target.stem.isascii() and target.stem.isdecimal():
                sprite = int(target.stem)
            if not 100000 <= sprite <= 132767:
                raise ValueError("目标编号必须在 100000～132767；请用数字文件名保存，例如 132767.spr")
            self.status_var.set("正在导出 SPR……")
            self.update_idletasks()
            build(Path(self.cg_var.get()), self.set_var.get(), self.current_number, Path(self.sa_var.get()), target, sprite, 0)
            self.status_var.set(f"导出完成：{target.name}")
            messagebox.showinfo("导出完成", f"SPR：{target}\n\n客户端文件没有被修改。")
        except Exception as error:
            self.status_var.set(f"导出失败：{error}")
            messagebox.showerror("导出失败", str(error))


if __name__ == "__main__":
    CGViewer().mainloop()
