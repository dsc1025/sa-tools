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
from client_data import client_data_dir as resolve_client_data_dir, resources
from sa_resource import palette, read_image
from spr_importer import delete_sprites, filename_image_number, import_package, sprite_exists
from spr_package import export_package


ROOT = Path(r"C:\Work\SA\SA2.5")
DATA = ROOT / "stoneage2.5" / "data"
CANVAS_WIDTH = 565
CANVAS_HEIGHT = 390


def png_bytes(width: int, height: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class Viewer(tk.Tk):
    def __init__(self, client_dir: Path | None = None, sprite_number: int | None = None) -> None:
        super().__init__()
        self.title("Stone Age Resource Viewer")
        self.geometry("800x600")
        self.resizable(False, False)
        self.data_var = tk.StringVar(value="")
        self.sprite_var = tk.StringVar(value="")
        self.direction_var = tk.StringVar(value="0")
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
        ttk.Label(controls, text="客户端目录").grid(row=0, column=0, sticky="w")
        ttk.Entry(controls, textvariable=self.data_var, width=58).grid(row=0, column=1, columnspan=5, padx=(5, 3), sticky="ew")
        ttk.Button(controls, text="选择目录", command=self.choose_data).grid(row=0, column=6, sticky="e")
        ttk.Label(controls, text="编号").grid(row=1, column=0)
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
        content = ttk.Frame(self, padding=(8, 0))
        content.grid(row=1, column=0, sticky="nsew")
        list_frame = ttk.Frame(content, width=195)
        list_frame.pack(side="left", fill="y", padx=(0, 8))
        ttk.Label(list_frame, text="形象列表").pack(anchor="w")
        self.sprite_tree = ttk.Treeview(list_frame, columns=("id", "actions"), show="headings", selectmode="extended", height=19)
        self.sprite_tree.heading("id", text="编号", anchor="center")
        self.sprite_tree.heading("actions", text="动作", anchor="center")
        self.sprite_tree.column("id", width=105, anchor="center")
        self.sprite_tree.column("actions", width=48, anchor="center")
        list_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.sprite_tree.yview)
        self.sprite_tree.configure(yscrollcommand=list_scroll.set)
        self.sprite_tree.pack(side="left", fill="y")
        list_scroll.pack(side="right", fill="y")
        self.sprite_tree.bind("<<TreeviewSelect>>", self.select_sprite_from_list)
        self.canvas = tk.Canvas(content, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg="#3c3c3c", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        bottom = ttk.Frame(self, padding=(8, 0, 8, 8))
        bottom.grid(row=2, column=0, sticky="ew")
        ttk.Button(bottom, text="批量导出 .spr", command=self.export_spr_batch).pack(side="left", padx=(0, 3))
        ttk.Button(bottom, text="批量导入 .spr", command=self.import_spr_batch).pack(side="left")
        ttk.Button(bottom, text="批量删除 .spr", command=self.delete_spr_batch).pack(side="left", padx=(3, 0))
        self.status = ttk.Label(bottom, text="输入编号后读取", justify="left", anchor="w", wraplength=420)
        self.status.pack(side="left", padx=8)
        self.status.configure(text="请先选择客户端目录")
        if client_dir is not None:
            self.data_var.set(str(client_dir))
        if sprite_number is not None:
            self.sprite_var.set(str(sprite_number))
        if client_dir is not None and sprite_number is not None:
            self.after_idle(self.load_sprite)


    def choose_data(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.data_var.get() or str(ROOT), title="选择客户端目录")
        if selected:
            self.data_var.set(selected)
            try:
                self.scan_sprites()
                if self.sprite_var.get().strip():
                    self.load_sprite()
            except Exception as error:
                self.status.configure(text=f"读取失败：{error}")

    def client_data_dir(self) -> Path:
        client_dir = self.data_var.get().strip()
        if not client_dir:
            raise ValueError("请先选择客户端目录")
        return resolve_client_data_dir(Path(client_dir))

    def scan_sprites(self) -> None:
        data_dir = self.client_data_dir()
        files = resources(data_dir)
        rows = list(struct.iter_unpack("<III", files["spradrn"].read_bytes()))
        sprites = {number: flags & 0xFFFF for number, _offset, flags in rows}
        self.sprite_tree.delete(*self.sprite_tree.get_children())
        for index, (number, action_count) in enumerate(sorted(sprites.items())):
            self.sprite_tree.insert("", "end", iid=f"row-{index}", values=(number, action_count))

    def select_sprite_from_list(self, _event=None) -> None:
        selected = self.sprite_tree.selection()
        if not selected:
            return
        self.sprite_var.set(str(self.sprite_tree.item(selected[0], "values")[0]))
        self.load_sprite()

    def load_sprite(self) -> None:
        try:
            self.stop()
            number = int(self.sprite_var.get())
            self.sprite_var.set(str(number))
            data_dir = self.client_data_dir()
            self.files = resources(data_dir)
            if not self.sprite_tree.get_children():
                self.scan_sprites()
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
            self.direction_var.set("0" if "0" in directions else directions[0])
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
            markers = [str(item["sound"]) for item in frames if item["sound"] != 0]
            marker_text = "、".join(markers) if markers else "无"
            self.status.configure(
                text=(
                    f"帧 {self.frame_index + 1}/{len(frames)} | 图片 {info.number} | "
                    f"{info.width}×{info.height} | 客户端调色板\n"
                    f"非零标记：{marker_text}"
                )
            )
        except Exception as error:
            self.status.configure(text=f"预览失败：{error}")


    def export_spr_batch(self) -> None:
        try:
            if not self.files:
                raise ValueError("请先选择客户端目录并读取形象")
            selected = self.sprite_tree.selection()
            if not selected:
                raise ValueError("请先在左侧列表中选择至少一个形象；可按住 Ctrl 或 Shift 多选")
            numbers = [int(self.sprite_tree.item(item, "values")[0]) for item in selected]
            directory = filedialog.askdirectory(title="选择 .spr 批量导出目录")
            if not directory:
                return
            output_dir = Path(directory)
            existing = [number for number in numbers if (output_dir / f"{number}.spr").exists()]
            overwrite = False
            if existing:
                shown = "、".join(str(number) for number in existing[:20])
                if len(existing) > 20:
                    shown += f" 等 {len(existing)} 个"
                choice = messagebox.askyesnocancel(
                    "批量导出遇到同名文件",
                    f"以下文件已经存在：\n{shown}\n\n"
                    "选择“是”覆盖；选择“否”跳过已存在文件；选择“取消”终止本次批量导出。",
                )
                if choice is None:
                    self.status.configure(text="已取消批量导出")
                    return
                overwrite = choice

            exported: list[str] = []
            skipped: list[str] = []
            failed: list[str] = []
            data_dir = self.client_data_dir()
            for index, number in enumerate(numbers, 1):
                target = output_dir / f"{number}.spr"
                self.status.configure(text=f"正在导出 {index}/{len(numbers)}：{number}.spr")
                self.update_idletasks()
                if target.exists() and not overwrite:
                    skipped.append(f"{number}.spr：文件已存在")
                    continue
                try:
                    export_package(data_dir, number, target)
                    exported.append(str(target))
                except Exception as error:
                    failed.append(f"{number}.spr：{error}")

            lines = [f"成功导出：{len(exported)} 个"]
            if skipped:
                lines.append(f"跳过：{len(skipped)} 个")
            if failed:
                lines.append(f"失败：{len(failed)} 个")
            details = skipped + failed
            if details:
                lines.append("")
                lines.extend(details[:20])
                if len(details) > 20:
                    lines.append(f"……另外 {len(details) - 20} 个未展开")
            summary = "\n".join(lines)
            self.status.configure(text=f"批量导出完成：成功 {len(exported)} 个，跳过 {len(skipped)} 个，失败 {len(failed)} 个")
            if failed:
                messagebox.showwarning("批量导出完成", summary)
            else:
                messagebox.showinfo("批量导出完成", summary)
        except Exception as error:
            self.status.configure(text=f"导出失败：{error}")
            messagebox.showerror("导出失败", str(error))

    def import_spr_batch(self) -> None:
        """Import several packages in one pass and report each result."""
        try:
            if not self.data_var.get().strip():
                raise ValueError("请先选择目标客户端目录")
            selected = filedialog.askopenfilenames(
                title="批量导入资源 .spr",
                filetypes=[("Stone Age sprite package", "*.spr")],
            )
            if not selected:
                return
            target_data = self.client_data_dir()
            packages: list[tuple[Path, int]] = []
            skipped: list[str] = []
            failed: list[str] = []
            seen_numbers: set[int] = set()
            for value in selected:
                package = Path(value)
                try:
                    number = filename_image_number(package)
                except Exception as error:
                    failed.append(f"{package.name}：{error}")
                    continue
                if number in seen_numbers:
                    failed.append(f"{package.name}：批量选择中重复了形象编号 {number}")
                    continue
                seen_numbers.add(number)
                packages.append((package, number))
            if not packages:
                summary = "没有可导入的有效 .spr 文件。"
                self.status.configure(text=summary)
                messagebox.showerror("批量导入失败", summary + ("\n" + "\n".join(failed) if failed else ""))
                return

            existing = [number for _package, number in packages if sprite_exists(target_data, number)]
            overwrite = False
            if existing:
                shown = "、".join(str(number) for number in existing[:20])
                if len(existing) > 20:
                    shown += f" 等 {len(existing)} 个"
                choice = messagebox.askyesnocancel(
                    "批量导入遇到重复编号",
                    f"以下形象编号已存在：\n{shown}\n\n"
                    "选择“是”覆盖这些编号；选择“否”跳过已存在编号；选择“取消”终止本次批量导入。",
                )
                if choice is None:
                    self.status.configure(text="已取消批量导入，未修改资源")
                    return
                overwrite = choice

            imported: list[tuple[Path, dict]] = []
            for index, (package, number) in enumerate(packages, 1):
                self.status.configure(text=f"正在导入 {index}/{len(packages)}：{package.name}")
                self.update_idletasks()
                if number in existing and not overwrite:
                    skipped.append(f"{package.name}：编号 {number} 已存在")
                    continue
                try:
                    result = import_package(package, target_data, overwrite=overwrite)
                    imported.append((package, result))
                except Exception as error:
                    failed.append(f"{package.name}：{error}")

            if imported:
                self.scan_sprites()
                last_target = imported[-1][1]["target_sprite"]
                self.sprite_var.set(str(last_target))
                self.load_sprite()
            lines = [f"成功导入：{len(imported)} 个"]
            normalized_events = sum(result.get("attack_events_normalized", 0) for _package, result in imported)
            if normalized_events:
                lines.append(f"CG 攻击标记规范化：{normalized_events} 个（此前标记 10100，最后标记 10000）")
            if skipped:
                lines.append(f"跳过：{len(skipped)} 个")
            if failed:
                lines.append(f"失败：{len(failed)} 个")
            details = skipped + failed
            if details:
                lines.append("")
                lines.extend(details[:20])
                if len(details) > 20:
                    lines.append(f"……另外 {len(details) - 20} 个未展开")
            summary = "\n".join(lines)
            self.status.configure(text=f"批量导入完成：成功 {len(imported)} 个，跳过 {len(skipped)} 个，失败 {len(failed)} 个")
            if failed:
                messagebox.showwarning("批量导入完成", summary)
            else:
                messagebox.showinfo("批量导入完成", summary)
        except Exception as error:
            self.status.configure(text=f"批量导入失败：{error}")
            messagebox.showerror("批量导入失败", str(error))

    def delete_spr_batch(self) -> None:
        try:
            if not self.data_var.get().strip():
                raise ValueError("请先选择客户端目录")
            selected = self.sprite_tree.selection()
            if not selected:
                raise ValueError("请先在左侧列表中选择至少一个形象；可按住 Ctrl 或 Shift 多选")
            numbers = [int(self.sprite_tree.item(item, "values")[0]) for item in selected]
            shown = "、".join(str(number) for number in numbers[:20])
            if len(numbers) > 20:
                shown += f" 等 {len(numbers)} 个"
            if not messagebox.askyesno(
                "确认批量删除",
                f"确定删除以下客户端形象索引吗？\n{shown}\n\n"
                "请确认你已经手动备份客户端。图片和动画数据本身不会从文件中清理。",
            ):
                self.status.configure(text="已取消批量删除，未修改资源")
                return
            result = delete_sprites(self.client_data_dir(), numbers)
            self.stop()
            self.sprite_tree.selection_set(())
            self.scan_sprites()
            self.sprite_var.set("")
            self.sprite_actions = []
            self.canvas.delete("all")
            self.status.configure(text=f"已删除 {len(result['deleted_sprites'])} 个形象索引")
            messagebox.showinfo(
                "批量删除完成",
                f"已删除形象索引：{len(result['deleted_sprites'])} 个",
            )
        except Exception as error:
            self.status.configure(text=f"批量删除失败：{error}")
            messagebox.showerror("批量删除失败", str(error))

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
    parser.add_argument("--client-dir", "--data-dir", dest="client_dir", type=Path)
    parser.add_argument("--sprite", type=int)
    args = parser.parse_args()
    Viewer(args.client_dir, args.sprite).mainloop()


if __name__ == "__main__":
    main()
