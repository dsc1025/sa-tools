#!/usr/bin/env python3
"""Small read-only visual inspector for Stone Age 2.5 sprite resources."""
from __future__ import annotations

import base64
import struct
import tkinter as tk
import zlib
from pathlib import Path
from tkinter import ttk

from export_sprite_preview import actions, sprite_range
from sa_resource import palette, read_image


ROOT = Path(r"C:\Work\SA\SA2.5")
DATA = ROOT / "stoneage2.5" / "data"
CANVAS_SIZE = 240


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Viewer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Stone Age 2.5 Resource Viewer")
        self.resizable(False, False)
        self.sprite_var = tk.StringVar(value="100250")
        self.direction_var = tk.StringVar(value="1")
        self.action_var = tk.StringVar(value="0")
        self.palette_var = tk.StringVar(value="1")
        self.frame_index = 0
        self.playing = False
        self.play_job: str | None = None
        self.sprite_actions: list[dict] = []
        self.palette_cache: dict[int, list[tuple[int, int, int]]] = {}
        self.photo: tk.PhotoImage | None = None
        controls = ttk.Frame(self, padding=8)
        controls.grid(row=0, column=0, sticky="ew")
        ttk.Label(controls, text="形象编号").grid(row=0, column=0)
        ttk.Entry(controls, textvariable=self.sprite_var, width=12).grid(row=0, column=1, padx=(3, 8))
        ttk.Button(controls, text="读取", command=self.load_sprite).grid(row=0, column=2)
        ttk.Label(controls, text="方向").grid(row=0, column=3, padx=(12, 0))
        self.direction = ttk.Combobox(controls, textvariable=self.direction_var, width=5, state="readonly")
        self.direction.grid(row=0, column=4, padx=3)
        self.direction.bind("<<ComboboxSelected>>", lambda _event: self.refresh_action_choices())
        ttk.Label(controls, text="调色板").grid(row=0, column=5, padx=(8, 0))
        palette_box = ttk.Combobox(controls, textvariable=self.palette_var, values=[str(i) for i in range(16)], width=4, state="readonly")
        palette_box.grid(row=0, column=6, padx=3)
        palette_box.bind("<<ComboboxSelected>>", lambda _event: self.show_frame(self.frame_index))
        ttk.Label(controls, text="动作").grid(row=0, column=7, padx=(8, 0))
        self.action = ttk.Combobox(controls, textvariable=self.action_var, width=5, state="readonly")
        self.action.grid(row=0, column=8, padx=3)
        self.action.bind("<<ComboboxSelected>>", lambda _event: self.show_frame(0))
        self.canvas = tk.Label(self, width=CANVAS_SIZE, height=CANVAS_SIZE, bg="#3c3c3c")
        self.canvas.grid(row=1, column=0, padx=8, pady=(0, 5))
        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.grid(row=2, column=0, sticky="ew")
        self.play_button = ttk.Button(bottom, text="播放", command=self.toggle_play)
        self.play_button.pack(side="left")
        self.status = ttk.Label(bottom, text="输入形象编号后读取")
        self.status.pack(side="left", padx=8)
        self.load_sprite()

    def load_sprite(self) -> None:
        try:
            self.stop()
            number = int(self.sprite_var.get())
            start, end = sprite_range(DATA / "spradrn_5.bin", DATA / "spr_4.bin", number)
            self.sprite_actions = actions(DATA / "spr_4.bin", start, end)
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
        self.show_frame(0)

    def selected(self) -> dict:
        return next(item for item in self.sprite_actions if item["direction"] == int(self.direction_var.get()) and item["action"] == int(self.action_var.get()))

    def show_frame(self, requested: int) -> None:
        try:
            action = self.selected()
            frames = action["frames"]
            self.frame_index = requested % len(frames)
            frame = frames[self.frame_index]
            info, pixels = read_image(DATA / "adrn_15.bin", DATA / "real_15.bin", frame["bitmap"])
            palette_number = int(self.palette_var.get())
            colours = self.palette_cache.setdefault(palette_number, palette(DATA / "pal" / f"Palet_{palette_number}.sap"))
            rgba = bytearray([60, 60, 60, 255] * CANVAS_SIZE * CANVAS_SIZE)
            # Centre the sprite reference point; ADRN and SPR offsets place the frame.
            left = CANVAS_SIZE // 2 + info.x + frame["x"]
            top = CANVAS_SIZE // 2 + info.y + frame["y"]
            for source_y in range(info.height):
                for source_x in range(info.width):
                    colour_index = pixels[(info.height - 1 - source_y) * info.width + source_x]
                    x, y = left + source_x, top + source_y
                    if colour_index != 253 and 0 <= x < CANVAS_SIZE and 0 <= y < CANVAS_SIZE:
                        red, green, blue = colours[colour_index]
                        at = (y * CANVAS_SIZE + x) * 4
                        rgba[at:at + 4] = bytes((red, green, blue, 255))
            self.photo = tk.PhotoImage(data=base64.b64encode(png_bytes(CANVAS_SIZE, CANVAS_SIZE, bytes(rgba))))
            self.canvas.configure(image=self.photo)
            self.status.configure(text=f"帧 {self.frame_index + 1}/{len(frames)} | 图片 {info.number} | {info.width}×{info.height} | 调色板 {palette_number} | 声音 {frame['sound']}")
        except Exception as error:
            self.status.configure(text=f"预览失败：{error}")


    def toggle_play(self) -> None:
        if self.playing:
            self.stop()
            return
        self.playing = True
        self.play_button.configure(text="停止")
        self.frame_index = 0
        self.play_next()

    def stop(self) -> None:
        self.playing = False
        self.play_button.configure(text="播放")
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


if __name__ == "__main__":
    Viewer().mainloop()
