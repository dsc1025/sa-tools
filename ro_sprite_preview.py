#!/usr/bin/env python3
"""Read RO SPR/ACT 2.5 resources and export composited animation previews."""
from __future__ import annotations

import argparse
import struct
from pathlib import Path

from PIL import Image


def read_spr(path: Path) -> list[Image.Image]:
    raw = path.read_bytes()
    if raw[:2] != b"SP" or raw[2:4] != b"\x01\x02":
        raise ValueError("only RO SPR 2.1 indexed sprites are currently supported")
    count, rgba = struct.unpack_from("<HH", raw, 4)
    if rgba:
        raise ValueError("RGBA RO sprite frames are not yet supported")
    palette_offset = len(raw) - 1024
    palette = raw[palette_offset:]
    cursor = 8
    result: list[Image.Image] = []
    for _ in range(count):
        width, height, compressed_size = struct.unpack_from("<HHH", raw, cursor)
        cursor += 6
        compressed = raw[cursor:cursor + compressed_size]
        cursor += compressed_size
        pixels = bytearray()
        index = 0
        while index < len(compressed):
            value = compressed[index]
            index += 1
            if value == 0:
                if index >= len(compressed):
                    raise ValueError("truncated RO RLE run")
                pixels.extend(b"\0" * compressed[index])
                index += 1
            else:
                pixels.append(value)
        if len(pixels) != width * height:
            raise ValueError(f"RO frame decoded {len(pixels)} pixels; expected {width * height}")
        rgba_pixels = bytearray()
        for value in pixels:
            red, green, blue, _alpha = palette[value * 4:value * 4 + 4]
            rgba_pixels.extend((red, green, blue, 0 if value == 0 else 255))
        result.append(Image.frombytes("RGBA", (width, height), bytes(rgba_pixels)))
    return result


def read_act(path: Path, with_intervals=False):
    raw = path.read_bytes()
    if raw[:2] != b"AC" or raw[2:4] != b"\x05\x02":
        raise ValueError("only RO ACT 2.5 is currently supported")
    actions = struct.unpack_from("<H", raw, 4)[0]
    cursor = 16
    result = []
    for _ in range(actions):
        frame_count = struct.unpack_from("<I", raw, cursor)[0]
        cursor += 4
        frames = []
        for _ in range(frame_count):
            cursor += 32
            layers = struct.unpack_from("<I", raw, cursor)[0]
            cursor += 4
            output_layers = []
            for _ in range(layers):
                x, y, sprite, flags = struct.unpack_from("<iiii", raw, cursor)
                tint = tuple(raw[cursor + 16:cursor + 20])
                scale_x, scale_y = struct.unpack_from("<ff", raw, cursor + 20)
                rotation, sprite_type = struct.unpack_from("<ii", raw, cursor + 28)
                if sprite_type != 0 and sprite >= 0:
                    raise ValueError("ACT refers to unsupported true-colour sprite")
                output_layers.append((x, y, sprite, flags, scale_x, scale_y, rotation, tint))
                cursor += 44
            cursor += 4
            anchors = struct.unpack_from("<I", raw, cursor)[0]
            cursor += 4 + anchors * 16
            frames.append(output_layers)
        result.append(frames)
    events = struct.unpack_from("<I", raw, cursor)[0]
    cursor += 4 + events * 40
    intervals = list(struct.unpack_from(f"<{actions}f", raw, cursor))
    cursor += actions * 4
    if cursor != len(raw):
        raise ValueError("ACT parser did not consume the complete file")
    return (result, intervals) if with_intervals else result


def composite_frame(layers, sprites):
    """Flatten every layer without clipping; return its top-left relative to RO origin."""
    transformed = []
    for x, y, sprite, flags, scale_x, scale_y, rotation, tint in layers:
        if sprite < 0:
            continue
        if sprite >= len(sprites):
            raise ValueError(f"invalid SPR reference {sprite}")
        image = sprites[sprite]
        if flags & 1:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if scale_x == 0 or scale_y == 0:
            continue
        if scale_x != 1 or scale_y != 1:
            image = image.resize((max(1, round(image.width * abs(scale_x))), max(1, round(image.height * abs(scale_y)))), Image.Resampling.NEAREST)
        if scale_x < 0:
            image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if scale_y < 0:
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        if rotation:
            image = image.rotate(-rotation, resample=Image.Resampling.NEAREST, expand=True)
        if tint != (255, 255, 255, 255):
            image = Image.merge("RGBA", tuple(channel.point([value * factor // 255 for value in range(256)]) for channel, factor in zip(image.split(), tint)))
        left, top = x - image.width // 2, y - image.height // 2
        transformed.append((image, left, top))
    if not transformed:
        return Image.new("RGBA", (1, 1)), (0, 0)
    left = min(item[1] for item in transformed)
    top = min(item[2] for item in transformed)
    right = max(item[1] + item[0].width for item in transformed)
    bottom = max(item[2] + item[0].height for item in transformed)
    canvas = Image.new("RGBA", (right - left, bottom - top))
    for image, x, y in transformed:
        canvas.alpha_composite(image, (x - left, y - top))
    bbox = canvas.getchannel("A").getbbox()
    if bbox is None:
        return Image.new("RGBA", (1, 1)), (0, 0)
    return canvas.crop(bbox), (left + bbox[0], top + bbox[1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("spr", type=Path)
    parser.add_argument("act", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sprites, actions = read_spr(args.spr), read_act(args.act)
    args.output.mkdir(parents=True, exist_ok=True)
    for group in range(len(actions) // 8):
        composed = [composite_frame(layers, sprites) for layers in actions[group * 8]]
        left = min(offset[0] for image, offset in composed) - 8
        top = min(offset[1] for image, offset in composed) - 8
        right = max(offset[0] + image.width for image, offset in composed) + 8
        bottom = max(offset[1] + image.height for image, offset in composed) + 8
        width, height = right - left, bottom - top
        frames = []
        for image, offset in composed:
            frame = Image.new("RGBA", (width, height))
            frame.alpha_composite(image, (offset[0] - left, offset[1] - top))
            frames.append(frame)
        sheet = Image.new("RGBA", (width * len(frames), height))
        for index, image in enumerate(frames):
            sheet.alpha_composite(image, (index * width, 0))
        sheet.save(args.output / f"group_{group}_direction_0.png")
    print(f"exported {len(actions)} actions across {len(actions) // 8} groups to {args.output}")


if __name__ == "__main__":
    main()
