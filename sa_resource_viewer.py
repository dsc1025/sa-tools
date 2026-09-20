#!/usr/bin/env python3
"""Small read-only visual inspector for Stone Age 2.5 sprite resources."""
from __future__ import annotations

import argparse
import base64
import struct
import tkinter as tk
import zlib
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from export_sprite_preview import actions, sprite_range
from client_data import resources
from sa_resource import palette, read_image
from spr_importer import filename_image_number, import_package, sprite_exists
from spr_package import export_package


ROOT = Path(r"C:\Work\SA\SA2.5")
DATA = ROOT / "stoneage2.5" / "data"
CANVAS_WIDTH = 780
CANVAS_HEIGHT = 480


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Viewer(tk.Tk):
    def __init__(self, data_dir: Path | None = None, sprite_number: int | None = None) -> None:
        super().__init__()
        self.title("Stone Age Resource Viewer")
        self.geometry("800x600")
        self.resizable(False, False)
        self.data_var = tk.StringVar(value="")
        self.sprite_var = tk.StringVar(value="100250")
        self.direction_var = tk.StringVar(value="1")
        self.action_var = tk.StringVar(value="0")
        self.frame_index = 0
        self.playing = False
        self.play_job: str | None = None
        self.sprite_actions: list[dict] = []
        self.files: dict[str, Path] = {}
        self.colours: list[tuple[int, int, int]] | None = None
        self.photo: tk.PhotoImage | None = None
        controls = ttk.Frame(self, padding=8)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(controls, text="客户端 data 目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.data_var, width=58).grid(row=0, column=1, columnspan=5, padx=(5, 3), sticky="ew")
        ttk.Button(controls, text="选择目录", command=self.choose_data).grid(row=0, column=6, sticky="e")
        ttk.Label(controls, text="形象编号").grid(row=1, column=0)
        ttk.Entry(controls, textvariable=self.sprite_var, width=12).grid(row=1, column=1, padx=(3, 8))
        ttk.Button(controls, text="读取", command=self.load_sprite).grid(row=1, column=2)
        ttk.Label(controls, text="方向").grid(row=1, column=3, padx=(12, 0))
        self.direction = ttk.Combobox(controls, textvariable=self.direction_var, width=5, state="readonly")
        self.direction.grid(row=1, column=4, padx=3)
        self.direction.bind("<<ComboboxSelected>>", lambda _event: self.refresh_action_choices())
        ttk.Label(controls, text="动作").grid(row=1, column=5, padx=(8, 0))
        self.action = ttk.Combobox(controls, textvariable=self.action_var, width=5, state="readonly")
        self.action.grid(row=1, column=6, padx=3)
        self.action.bind("<<ComboboxSelected>>", lambda _event: self.start_animation())
        self.canvas = tk.Canvas(self, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg="#3c3c3c", highlightthickness=0)
        self.canvas.grid(row=1, column=0, padx=8, pady=(0, 5))
        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.grid(row=2, column=0, sticky="ew")
        ttk.Button(bottom, text="导出 .spr", command=self.export_spr).pack(side="left", padx=(0, 3))
        ttk.Button(bottom, text="导入 .spr", command=self.import_spr).pack(side="left")
        self.status = ttk.Label(bottom, text="输入形象编号后读取")
        self.status.pack(side="left", padx=8)
        self.status.configure(text="请先选择客户端 data 目录")
        if data_dir is not None:
            self.data_var.set(str(data_dir))
        if sprite_number is not None:
            self.sprite_var.set(str(sprite_number))
        if data_dir is not None and sprite_number is not None:
            self.after_idle(self.load_sprite)


    def choose_data(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.data_var.get() or None, title="选择客户端 data 目录")
        if selected:
            self.data_var.set(selected)
            self.load_sprite()

    def load_sprite(self) -> None:
        try:
            self.stop()
            number = int(self.sprite_var.get())
            self.sprite_var.set(str(number))
            data_dir = Path(self.data_var.get())
            self.files = resources(data_dir)
            palette_path = next(
                (path for path in (data_dir / "pal").iterdir() if path.name.lower() == "palet_1.sap"),
                None,
            )
            if palette_path is None:
                raise FileNotFoundError(f"找不到客户端调色板：{data_dir / 'pal' / 'Palet_1.sap'}")
            self.colours = palette(palette_path)
            start, end = sprite_range(self.files["spradrn"], self.files["spr"], number)
            self.sprite_actions = actions(self.files["spr"], start, end)
            directions = sorted({str(item["direction"]) for item in self.sprite_actions}, key=int)
            self.direction["values"] = directions
            self.direction_var.set("1" if "1" in directions else directions[0])
            self.refresh_action_choices()
        except Exception as error:
            self.status.configure(text=f"读取失败：{error}")

    def refresh_action_choices(self) -> None:
        self.stop()
        direction = int(self.direction_var.get())
        values = sorted({str(item["action"]) for item in self.sprite_actions if item["direction"] == direction}, key=int)
        self.action["values"] = values
        self.action_var.set(values[0])
        self.start_animation()

    def selected(self) -> dict:
        return next(item for item in self.sprite_actions if item["direction"] == int(self.direction_var.get()) and item["action"] == int(self.action_var.get()))

    def show_frame(self, requested: int) -> None:
        try:
            action = self.selected()
            frames = action["frames"]
            self.frame_index = requested % len(frames)
            frame = frames[self.frame_index]
            info, pixels = read_image(self.files["adrn"], self.files["real"], frame["bitmap"])
            if self.colours is None:
                raise ValueError("客户端调色板尚未加载")
            rgba = bytearray([60, 60, 60, 255] * CANVAS_WIDTH * CANVAS_HEIGHT)
            # Centre the sprite reference point; ADRN and SPR offsets place the frame.
            left = CANVAS_WIDTH // 2 + info.x + frame["x"]
            top = CANVAS_HEIGHT // 2 + info.y + frame["y"]
            for source_y in range(info.height):
                for source_x in range(info.width):
                    colour_index = pixels[(info.height - 1 - source_y) * info.width + source_x]
                    x, y = left + source_x, top + source_y
                    if colour_index != 253 and 0 <= x < CANVAS_WIDTH and 0 <= y < CANVAS_HEIGHT:
                        red, green, blue = self.colours[colour_index]
                        at = (y * CANVAS_WIDTH + x) * 4
                        rgba[at:at + 4] = bytes((red, green, blue, 255))
            self.photo = tk.PhotoImage(data=base64.b64encode(png_bytes(CANVAS_WIDTH, CANVAS_HEIGHT, bytes(rgba))))
            self.canvas.delete("all")
            self.canvas.create_image(CANVAS_WIDTH // 2, CANVAS_HEIGHT // 2, image=self.photo)
            self.status.configure(text=f"帧 {self.frame_index + 1}/{len(frames)} | 图片 {info.number} | {info.width}×{info.height} | 客户端调色板 | 声音 {frame['sound']}")
        except Exception as error:
            self.status.configure(text=f"预览失败：{error}")


    def export_spr(self) -> None:
        try:
            if not self.files:
                raise ValueError("请先选择客户端 data 目录并读取形象")
            sprite_number = int(self.sprite_var.get().strip())
            self.sprite_var.set(str(sprite_number))
            directory = filedialog.askdirectory(title="选择 .spr 导出目录")
            if not directory:
                return
            target = Path(directory) / f"{sprite_number}.spr"
            if target.exists() and not messagebox.askyesno("文件已存在", f"{target.name} 已存在，是否覆盖？"):
                return
            export_package(Path(self.data_var.get()), sprite_number, target)
            self.status.configure(text=f"导出成功：{target}")
            messagebox.showinfo("导出成功", f"已导出单个资源包：\n{target}")
        except Exception as error:
            self.status.configure(text=f"导出失败：{error}")
            messagebox.showerror("导出失败", str(error))

    def import_spr(self) -> None:
        try:
            if not self.data_var.get():
                raise ValueError("请先选择目标客户端 data 目录")
            source = filedialog.askopenfilename(title="导入单个资源 .spr", filetypes=[("Stone Age sprite package", "*.spr")])
            if not source:
                return
            number = filename_image_number(Path(source))
            target_data = Path(self.data_var.get())
            overwrite = sprite_exists(target_data, number)
            if overwrite:
                if not messagebox.askyesno("形象编号重复", f"目标客户端已有形象编号 {number}。是否覆盖？"):
                    self.status.configure(text="已取消导入，未修改资源")
                    return
            result = import_package(Path(source), target_data, overwrite=overwrite)
            if result["status"] == "already_present":
                self.status.configure(text="导入完成：资源已存在，无需写入")
                messagebox.showinfo("无需导入", result["message"])
            else:
                self.status.configure(text=f"导入成功：形象 {result['source_sprite']} → {result['target_sprite']}")
                messagebox.showinfo("导入成功", f"原形象编号：{result['source_sprite']}\n新形象编号：{result['target_sprite']}\n新增图片帧：{result['frames_added']}\n复用图片帧：{result['frames_reused']}\n\n请在服务端使用新形象编号。")
                self.sprite_var.set(str(result["target_sprite"]))
                self.load_sprite()
        except Exception as error:
            self.status.configure(text=f"导入失败：{error}")
            messagebox.showerror("导入失败", str(error))

    def start_animation(self) -> None:
        self.stop()
        self.playing = True
        self.frame_index = 0
        self.play_next()

    def stop(self) -> None:
        self.playing = False
        if self.play_job is not None:
            self.after_cancel(self.play_job)
            self.play_job = None

    def play_next(self) -> None:
        if not self.playing:
            return
        action = self.selected()
        self.show_frame(self.frame_index)
        frame_count = len(action["frames"])
        interval_ms = max(35, min(1000, int(action["duration"] / frame_count)))
        self.frame_index = (self.frame_index + 1) % frame_count
        self.play_job = self.after(interval_ms, self.play_next)


def main() -> None:
    parser = argparse.ArgumentParser(description="View a Stone Age client sprite resource.")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--sprite", type=int)
    args = parser.parse_args()
    Viewer(args.data_dir, args.sprite).mainloop()


if __name__ == "__main__":
    main()
