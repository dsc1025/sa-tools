"""GBK-compatible EnemyBase desktop editor; run with Python 3.10+."""
from __future__ import annotations

import copy
import base64
import hashlib
import json
import os
import re
import struct
import tempfile
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from client_data import resources
from export_sprite_preview import actions, sprite_range
from sa_resource import palette, read_image


def center_window(window, width, height):
    """Place a fixed-size Tk window at the center of the screen."""
    screen_width = window.winfo_screenwidth()
    screen_height = window.winfo_screenheight()
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    window.geometry(f'{width}x{height}+{x}+{y}')


LABELS = ['名称', '素材名称1', '素材名称2', '素材名称3', '素材名称4', '素材名称5',
          '模板编号', '初始点数 (INITNUM)', '升级点数 (LVUPPOINT)', '基础体力', '基础力量',
          '基础耐力', '基础敏捷', 'AI参数 (MODAI)', '捕捉参数 (GET)', '地属性', '水属性',
          '火属性', '风属性', '毒抗性', '麻痹抗性', '睡眠抗性', '石化抗性', '酒醉抗性',
          '混乱抗性'] + [f'技能{i}' for i in range(1, 8)] + [
          '稀有度', '暴击参数', '反击参数', '技能槽数', '形象编号', '宠物标志', '尺寸'] + [
          f'素材{i}{suffix}' for i in range(1, 6) for suffix in ('基础加成', '固定最小值', '固定最大值')
          ] + ['等级上限', '扩展字段（源码未定义）']

NEW_TEMPLATE_DEFAULTS = {
    13: '300',  # AI 参数
    14: '3',    # 捕捉参数
    32: '0',    # 稀有度
    33: '0',    # 暴击参数
    34: '0',    # 反击参数
    35: '7',    # 技能槽数
    37: '1',    # 宠物标志
    38: '1',    # 尺寸
}


@dataclass
class Line:
    text: str
    ending: str
    number: int
    fields: list[str] | None
    original: list[str] | None

    def render(self):
        return (self.text if self.fields == self.original else ','.join(self.fields or [])) + self.ending


class Document:
    def __init__(self, data: bytes, encoding: str = '自动'):
        if encoding == '自动':
            encoding = 'utf-8-sig' if data.startswith(b'\xef\xbb\xbf') else 'gbk'
            try:
                text = data.decode(encoding)
            except UnicodeDecodeError:
                encoding = 'utf-8'
                text = data.decode(encoding)
        else:
            text = data.decode(encoding)
        self.encoding = encoding
        self.digest = hashlib.sha256(data).digest()
        self.lines = []
        for n, raw in enumerate(text.splitlines(keepends=True), 1):
            ending = '\r\n' if raw.endswith('\r\n') else '\n' if raw.endswith('\n') else '\r' if raw.endswith('\r') else ''
            content = raw[:-len(ending)] if ending else raw
            fields = content.split(',') if content.strip() and not content.lstrip().startswith('#') else None
            self.lines.append(Line(content, ending, n, fields, copy.deepcopy(fields)))
        self.newline = next((line.ending for line in self.lines if line.ending), '\r\n')


    def bytes(self):
        return ''.join(line.render() for line in self.lines).encode(self.encoding)

    def records(self):
        return [line for line in self.lines if line.fields is not None]

    def issues(self):
        ids = Counter(line.fields[6].strip() for line in self.records() if len(line.fields) > 6)
        result = {}
        for line in self.records():
            f = line.fields
            errors = []
            if len(f) != 56:
                errors.append(f'字段数 {len(f)}，预期 56')
            if len(f) > 6:
                if not re.fullmatch(r'\d+', f[6].strip()):
                    errors.append('模板编号必须是非负整数')
                if ids[f[6].strip()] > 1:
                    errors.append('模板编号重复')
            for i, value in enumerate(f[6:55], 6):
                if value.strip() and not re.fullmatch(r'[+-]?\d+(?:\.\d+)?' if i == 8 else r'[+-]?\d+', value.strip()):
                    errors.append(f'第{i+1}列数字格式错误')
            if not f[0].strip():
                errors.append('名称为空')
            if errors:
                result[id(line)] = errors
        return result

    def save(self, target: Path, expected_digest=None):
        data = self.bytes()
        if target.exists() and expected_digest is not None:
            current = target.read_bytes()
            if hashlib.sha256(current).digest() != expected_digest:
                raise ValueError('文件已被其他程序修改，请另存为或重新打开。')
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=target.name + '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        self.digest = hashlib.sha256(data).digest()




@dataclass
class Reference:
    kind: str
    path: Path
    line: int
    detail: str
    source_text: str = ''


def read_data_lines(path: Path):
    """Read a server data file and yield (line number, text, CSV fields)."""
    data = path.read_bytes()
    for encoding in ('gbk', 'utf-8-sig', 'utf-8'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = data.decode('gbk', errors='replace')
    for number, text_line in enumerate(text.splitlines(), 1):
        if text_line.strip() and not text_line.lstrip().startswith('#'):
            yield number, text_line, [value.strip() for value in text_line.split(',')]


def find_client_data_directory(client_root: Path) -> Path:
    """Find data only in the selected client folder or its direct children."""
    root = client_root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(f'不是文件夹：{root}')
    if root.name.casefold() == 'data':
        return root

    try:
        candidates = sorted(
            (path for path in root.iterdir() if path.is_dir() and path.name.casefold() == 'data'),
            key=lambda path: str(path).casefold(),
        )
    except OSError as exc:
        raise OSError(f'无法读取所选客户端目录：{root}') from exc
    if not candidates:
        raise FileNotFoundError(f'所选目录的直接子目录中没有找到 data 文件夹：{root}')
    return candidates[0].resolve()


def client_image_numbers(data_directory: Path) -> set[int]:
    """Read image numbers listed by the client's sprite animation indexes."""
    index_files = sorted(data_directory.glob('spradrn_*.bin'))
    if not index_files:
        raise FileNotFoundError(f'客户端 data 中没有 spradrn_*.bin：{data_directory}')
    image_numbers = set()
    for index_file in index_files:
        data = index_file.read_bytes()
        if len(data) % 12:
            raise ValueError(f'形象索引文件长度异常：{index_file.name}')
        image_numbers.update(image for image, _offset, _flags in struct.iter_unpack('<III', data))
    return image_numbers


def model_preview_png(data_directory: Path, image_number: int) -> tuple[bytes, int, int]:
    """Render the first available client sprite frame as a transparent PNG."""
    files = resources(data_directory)
    start, end = sprite_range(files['spradrn'], files['spr'], image_number)
    sprite_actions = actions(files['spr'], start, end)
    if not sprite_actions:
        raise ValueError('该形象没有可预览动作。')
    action = min(sprite_actions, key=lambda item: (item['direction'] != 0, item['direction'], item['action']))
    if not action['frames']:
        raise ValueError('该形象没有可预览帧。')
    frame = action['frames'][0]
    info, pixels = read_image(files['adrn'], files['real'], frame['bitmap'])
    palette_dir = data_directory / 'pal'
    palette_path = next((path for path in palette_dir.iterdir() if path.name.casefold() == 'palet_1.sap'), None)
    if palette_path is None:
        raise FileNotFoundError(f'找不到客户端调色板：{palette_dir / "Palet_1.sap"}')
    colours = palette(palette_path)

    offset_x = info.x + frame['x']
    offset_y = info.y + frame['y']
    left_bound, top_bound = min(0, offset_x), min(0, offset_y)
    right_bound = max(0, offset_x + info.width)
    bottom_bound = max(0, offset_y + info.height)
    width = max(1, right_bound - left_bound)
    height = max(1, bottom_bound - top_bound)
    rgba = bytearray(width * height * 4)
    visible_left, visible_top = width, height
    visible_right = visible_bottom = -1
    for source_y in range(info.height):
        for source_x in range(info.width):
            colour_index = pixels[(info.height - 1 - source_y) * info.width + source_x]
            if colour_index == 253:
                continue
            x, y = offset_x - left_bound + source_x, offset_y - top_bound + source_y
            at = (y * width + x) * 4
            rgba[at:at + 4] = bytes((*colours[colour_index], 255))
            visible_left = min(visible_left, x)
            visible_top = min(visible_top, y)
            visible_right = max(visible_right, x)
            visible_bottom = max(visible_bottom, y)

    if visible_right < visible_left or visible_bottom < visible_top:
        raise ValueError('该形象没有可见像素。')
    if (visible_left, visible_top, visible_right, visible_bottom) != (0, 0, width - 1, height - 1):
        cropped_width = visible_right - visible_left + 1
        cropped_height = visible_bottom - visible_top + 1
        cropped_rgba = bytearray(cropped_width * cropped_height * 4)
        for y in range(cropped_height):
            source_start = ((visible_top + y) * width + visible_left) * 4
            source_end = source_start + cropped_width * 4
            target_start = y * cropped_width * 4
            cropped_rgba[target_start:target_start + cropped_width * 4] = rgba[source_start:source_end]
        rgba = cropped_rgba
        width, height = cropped_width, cropped_height

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF))

    rows = b''.join(b'\0' + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(rows))
           + chunk(b'IEND', b''))
    return png, width, height


def find_references(enemybase_path: Path, template_id: str):
    """Follow enemybase TEMPNO -> enemy ID -> group ID -> encounter/NPC uses."""
    folder = enemybase_path.parent
    references = []
    warnings = []
    enemy_ids = set()
    group_ids = set()
    enemy_path = folder / 'enemy.txt'
    group_path = folder / 'group1.txt'
    encount_path = folder / 'encount.txt'

    if enemy_path.exists():
        for number, text_line, fields in read_data_lines(enemy_path):
            # This server build has ACT_CONDITION in column 3, followed by
            # ENEMY_ID and ENEMY_TEMPNO in columns 4 and 5.
            if len(fields) >= 5 and fields[4] == template_id:
                enemy_ids.add(fields[3])
                references.append(Reference('实例', enemy_path, number,
                                             f'ENEMY_ID={fields[3]}，名称={fields[0]}，等级={fields[5]}–{fields[6]}',
                                             text_line))
    else:
        warnings.append('同目录未找到 enemy.txt')

    if group_path.exists():
        for number, text_line, fields in read_data_lines(group_path):
            used = sorted(enemy_ids.intersection(fields[4:14]),
                          key=lambda value: (0, int(value)) if value.isdigit() else (1, value))
            if used:
                group_ids.add(fields[1])
                references.append(Reference('遇敌组', group_path, number,
                                             f'GROUP_ID={fields[1]}，名称={fields[0]}，引用 ENEMY_ID={"、".join(used)}',
                                             text_line))
    else:
        warnings.append('同目录未找到 group1.txt')

    if encount_path.exists():
        for number, _text, fields in read_data_lines(encount_path):
            used = sorted(group_ids.intersection(fields[10:20]),
                          key=lambda value: (0, int(value)) if value.isdigit() else (1, value))
            if used:
                references.append(Reference('地图遇敌', encount_path, number,
                                             f'地图={fields[1]}，区域=({fields[2]},{fields[3]})–({fields[4]},{fields[5]})，引用 GROUP_ID={"、".join(used)}'))
    else:
        warnings.append('同目录未找到 encount.txt')

    npc_root = folder / 'npc'
    if npc_root.is_dir():
        template_pattern = re.compile(rf'PETTEMPNO\s*[:=]\s*{re.escape(template_id)}(?:\D|$)', re.I)
        addpet_pattern = re.compile(r'AddPet\s*[:=]\s*([^\r\n]+)', re.I)
        pet_patterns = [re.compile(rf'PET\s*=\s*\d+\s*-\s*{re.escape(enemy_id)}(?:\D|$)', re.I)
                        for enemy_id in enemy_ids if enemy_id]
        for npc_path in npc_root.rglob('*'):
            try:
                if not npc_path.is_file() or npc_path.stat().st_size > 2_000_000:
                    continue
                data = npc_path.read_bytes()
                if b'\0' in data:
                    continue
                try:
                    content = data.decode('gbk')
                except UnicodeDecodeError:
                    content = data.decode('utf-8', errors='replace')
            except OSError:
                continue
            for number, text_line in enumerate(content.splitlines(), 1):
                reasons = []
                if template_pattern.search(text_line):
                    reasons.append(f'PETTEMPNO={template_id}')
                match = addpet_pattern.search(text_line)
                if match:
                    values = set(re.findall(r'(?<!\d)\d+(?!\d)', match.group(1)))
                    used = sorted(enemy_ids.intersection(values))
                    if used:
                        reasons.append('AddPet ENEMY_ID=' + '、'.join(used))
                if any(pattern.search(text_line) for pattern in pet_patterns):
                    reasons.append('PET 条件引用实例')
                if reasons:
                    excerpt = text_line.strip()
                    if len(excerpt) > 130:
                        excerpt = excerpt[:127] + '...'
                    references.append(Reference('NPC数据', npc_path, number,
                                                 '；'.join(reasons) + '｜' + excerpt))
    else:
        warnings.append('同目录未找到 npc 目录')
    return references, warnings


def create_level_one_instance(enemy_path: Path, template_id: str, name: str) -> str:
    """Append a level-one enemy instance and return its newly assigned ENEMY_ID."""
    if not enemy_path.is_file():
        raise FileNotFoundError(f'未找到实例文件：{enemy_path}')
    enemy_doc = Document(enemy_path.read_bytes())
    records = enemy_doc.records()
    if not records:
        raise ValueError('enemy.txt 没有可用记录，无法确认当前实例格式。')
    counts = Counter(len(line.fields) for line in records)
    field_count, frequency = counts.most_common(1)[0]
    if field_count != 34 or frequency != len(records):
        raise ValueError('当前 enemy.txt 不是本工具支持的 34 列实例格式。')
    try:
        highest_id = max(int(line.fields[3]) for line in records if line.fields[3].isdigit())
    except ValueError as exc:
        raise ValueError('enemy.txt 没有有效的实例编号。') from exc
    enemy_id = str(highest_id + 1)
    fields = [''] * 34
    fields[:14] = [
        name, 'at:10;1;1|gu:1|es:1|wa:0;0;0;0;0;0;0;', '', enemy_id,
        template_id, '1', '1', '1', '1', '1', '-1', '-1', '0', '1',
    ]
    if enemy_doc.lines and not enemy_doc.lines[-1].ending:
        enemy_doc.lines[-1].ending = enemy_doc.newline
    enemy_doc.lines.append(Line('', enemy_doc.newline, 0, fields, None))
    enemy_doc.save(enemy_path, enemy_doc.digest)
    return enemy_id



class Editor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('EnemyBase 编辑器')
        self.minsize(1050, 700)
        center_window(self, 1250, 850)
        self.doc = None
        self.path = None
        self.client_data_path = None
        self.client_root_path = None
        self.settings_path = self.settings_file_path()
        self.saved_paths = self.read_saved_paths()
        self.baseline = b''
        self.history = []
        self.active = None
        self.visible = {}
        self.reference_after = None
        self.reference_cache = {}
        self.protocol('WM_DELETE_WINDOW', self.close)
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill='x')
        for label, callback in [('保存', self.save), ('另存为', lambda: self.save(True)),
                                ('新增', self.add), ('复制', lambda: self.add(True)), ('删除', self.delete),
                                ('撤销', self.undo), ('检查', self.check), ('按编号整理', self.organize)]:
            ttk.Button(bar, text=label, command=callback).pack(side='left', padx=2)
        self.encoding = tk.StringVar(value='自动')
        ttk.Combobox(bar, textvariable=self.encoding, values=['自动', 'gbk', 'utf-8', 'utf-8-sig'], state='readonly', width=10).pack(side='right')
        ttk.Label(bar, text='打开编码：').pack(side='right')
        selectors = ttk.Frame(self, padding=(8, 0, 8, 4))
        selectors.pack(fill='x')
        ttk.Button(selectors, text='打开enemyBase', command=self.open_enemybase).grid(row=0, column=0, sticky='w', pady=2)
        self.enemybase_path_label = ttk.Label(selectors, text='请选择 enemybase.txt', anchor='w')
        self.enemybase_path_label.grid(row=0, column=1, sticky='ew', padx=8, pady=2)
        ttk.Button(selectors, text='打开clientData', command=self.open_client_data).grid(row=1, column=0, sticky='w', pady=2)
        self.client_data_path_label = ttk.Label(
            selectors,
            text='请选择客户端文件夹',
            anchor='w',
        )
        self.client_data_path_label.grid(row=1, column=1, sticky='ew', padx=8, pady=2)
        selectors.columnconfigure(1, weight=1)
        search = ttk.Frame(self, padding=8)
        search.pack(fill='x')
        ttk.Label(search, text='搜索名称 / 编号 / 形象：').pack(side='left')
        self.query = tk.StringVar()
        ttk.Entry(search, textvariable=self.query, width=35).pack(side='left')
        self.filter = tk.StringVar(value='全部')
        box = ttk.Combobox(search, textvariable=self.filter, values=['全部', '异常', '已修改'], state='readonly', width=10)
        box.pack(side='left', padx=8)
        ttk.Button(search, text='筛选', command=self.refresh_filter).pack(side='left')
        pane = ttk.Panedwindow(self, orient='horizontal')
        pane.pack(fill='both', expand=True, padx=8)
        left = ttk.Frame(pane)
        pane.add(left, weight=2)
        self.tree = ttk.Treeview(left, columns=('line', 'id', 'name', 'image', 'state'), show='headings', selectmode='extended')
        for key, label, width in [('line', '原行号', 60), ('id', '模板编号', 80), ('name', '名称', 130), ('image', '形象编号', 85), ('state', '状态', 80)]:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=45)
        scroll = ttk.Scrollbar(left, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        self.tree.bind('<Double-1>', self.open_properties_from_row)
        right = ttk.Frame(pane)
        pane.add(right, weight=3)
        ttk.Label(right, text='查看引用', font=('TkDefaultFont', 11, 'bold')).pack(anchor='w', padx=8, pady=(4, 0))
        self.reference_summary = ttk.Label(right, text='请选择左侧基板。', padding=(8, 4), wraplength=600)
        self.reference_summary.pack(fill='x')
        self.reference_warning = ttk.Label(right, text='', foreground='#b45309',
                                           padding=(8, 0), wraplength=600)
        reference_frame = ttk.Frame(right, padding=(8, 0, 8, 0))
        reference_frame.pack(fill='both', expand=True)
        reference_actions = ttk.Frame(reference_frame)
        reference_actions.pack(fill='x', pady=(0, 4))
        ttk.Button(reference_actions, text='新增实例', command=self.add_instance).pack(side='left', padx=(0, 6))
        ttk.Button(reference_actions, text='新增遇敌组', state='disabled').pack(side='left')
        self.reference_tree = ttk.Treeview(reference_frame, columns=('kind', 'file', 'line', 'detail'), show='headings')
        for key, title, width, minwidth, stretch in [
            ('kind', '类型', 70, 50, False), ('file', '文件', 150, 90, True),
            ('line', '行号', 55, 45, False), ('detail', '引用详情', 360, 150, True),
        ]:
            self.reference_tree.heading(key, text=title)
            self.reference_tree.column(key, width=width, minwidth=minwidth, stretch=stretch)
        yscroll = ttk.Scrollbar(reference_frame, orient='vertical', command=self.reference_tree.yview)
        self.reference_tree.configure(yscrollcommand=yscroll.set)
        yscroll.pack(side='right', fill='y')
        self.reference_tree.pack(fill='both', expand=True)
        self.reference_rows = {}
        self.reference_tree.bind('<Button-3>', self.reference_context_menu)
        self.status = ttk.Label(self, text='尚未打开文件', padding=8)
        self.status.pack(fill='x')
        self.bind('<Control-s>', lambda e: self.save())
        self.bind('<Control-o>', lambda e: self.open_enemybase())
        self.after_idle(self.restore_last_paths)

    @staticmethod
    def settings_file_path():
        return Path(__file__).resolve().with_name('enemybase_editor.settings.json')

    @staticmethod
    def legacy_settings_file_path():
        app_data = os.environ.get('APPDATA')
        return Path(app_data) / 'EnemyBaseEditor' / 'settings.json' if app_data else None

    def read_saved_paths(self):
        try:
            settings = json.loads(self.settings_path.read_text(encoding='utf-8'))
            return settings if isinstance(settings, dict) else {}
        except (OSError, ValueError, TypeError):
            pass

        # Migrate the previous per-user settings file beside this script.
        legacy_path = self.legacy_settings_file_path()
        if legacy_path:
            try:
                settings = json.loads(legacy_path.read_text(encoding='utf-8'))
                if isinstance(settings, dict):
                    self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                    self.settings_path.write_text(
                        json.dumps(settings, ensure_ascii=False, indent=2),
                        encoding='utf-8',
                    )
                    return settings
            except (OSError, ValueError, TypeError):
                pass
        return {}

    def remember_paths(self):
        settings = {}
        if self.path:
            settings['enemybase_path'] = str(self.path)
        if self.client_root_path:
            settings['client_root_path'] = str(self.client_root_path)
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )
        except OSError as exc:
            messagebox.showwarning('无法记住路径', f'路径已打开，但无法保存记忆设置：\n{exc}')

    def restore_last_paths(self):
        """Restore previously selected locations without opening file dialogs."""
        enemybase_name = self.saved_paths.get('enemybase_path')
        if isinstance(enemybase_name, str):
            path = Path(enemybase_name)
            if path.is_file() and path.name.casefold() == 'enemybase.txt':
                self.load_enemybase_path(path, remember=False, show_error=False)

        client_root_name = self.saved_paths.get('client_root_path')
        if isinstance(client_root_name, str):
            root = Path(client_root_name)
            if root.is_dir():
                self.load_client_root(root, remember=False, show_error=False)

    def leave(self):
        if self.doc and self.doc.bytes() != self.baseline:
            answer = messagebox.askyesnocancel('未保存', '是否保存当前文件修改？')
            return self.save() if answer else answer is not None
        return True

    def open_enemybase(self):
        if not self.leave():
            return
        name = filedialog.askopenfilename(
            title='选择 enemybase.txt',
            initialdir=str(self.path.parent) if self.path else None,
            filetypes=[('enemybase.txt', 'enemybase.txt'), ('文本文件', '*.txt'), ('所有文件', '*.*')],
        )
        if not name:
            return
        self.load_enemybase_path(Path(name))

    def load_enemybase_path(self, path, remember=True, show_error=True):
        try:
            if path.name.casefold() != 'enemybase.txt':
                raise ValueError('请选择文件名为 enemybase.txt 的文件。')
            data = path.read_bytes()
            doc = Document(data, self.encoding.get())
        except Exception as exc:
            if show_error:
                messagebox.showerror('读取失败', str(exc))
            return False
        self.doc, self.path, self.baseline = doc, path, data
        self.history.clear()
        self.reference_cache.clear()
        self.active = None
        self.enemybase_path_label.configure(text=str(path))
        self.load_record(None)
        self.refresh()
        if remember:
            self.remember_paths()
        return True

    def open_client_data(self):
        selected = filedialog.askdirectory(
            title='选择客户端文件夹',
            initialdir=str(self.client_root_path) if self.client_root_path else None,
            mustexist=True,
        )
        if not selected:
            return
        self.load_client_root(Path(selected))

    def load_client_root(self, root, remember=True, show_error=True):
        try:
            data_path = find_client_data_directory(root)
        except Exception as exc:
            if show_error:
                messagebox.showerror('定位客户端 data 失败', str(exc))
            return False
        self.client_root_path = root.resolve()
        self.client_data_path = data_path
        self.client_data_path_label.configure(text=str(data_path))
        if remember:
            self.remember_paths()
        return True

    def snapshot(self):
        self.history.append(copy.deepcopy(self.doc.lines))
        self.history = self.history[-100:]

    def refresh_filter(self):
        self.load_record(None)
        self.refresh()

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.visible = {}
        if not self.doc:
            return
        issues = self.doc.issues()
        for index, line in enumerate(self.doc.lines):
            if line.fields is None:
                continue
            f = line.fields
            get = lambda i: f[i] if len(f) > i else ''
            changed = f != line.original
            if self.query.get().lower() not in ' '.join([get(0), get(6), get(36)]).lower():
                continue
            if self.filter.get() == '异常' and id(line) not in issues:
                continue
            if self.filter.get() == '已修改' and not changed:
                continue
            key = str(index)
            self.visible[key] = line
            state = '异常' if id(line) in issues else '新增' if line.number == 0 else '已修改' if changed else ''
            self.tree.insert('', 'end', iid=key, values=(line.number or '新增', get(6), get(0), get(36), state))
            if line is self.active:
                self.tree.selection_set(key)
        dirty = self.doc.bytes() != self.baseline
        self.status.configure(text=f'共 {len(self.doc.records())} 条 / 显示 {len(self.visible)} 条 / 异常 {len(issues)} 条 / 编码 {self.doc.encoding} / '+('有未保存修改' if dirty else '已保存')+f' / 可撤销 {len(self.history)} 步')

    def select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        target = self.visible.get(selection[0])
        if target is self.active:
            return
        self.load_record(target)

    def open_properties_from_row(self, event):
        item = self.tree.identify_row(event.y)
        target = self.visible.get(item)
        if target is None:
            return 'break'
        if target is not self.active:
            self.load_record(target)
        self.tree.selection_set(item)
        self.tree.focus(item)
        self.new_template_dialog(target)
        return 'break'

    def load_record(self, line):
        self.active = line
        if self.reference_after is not None:
            self.after_cancel(self.reference_after)
            self.reference_after = None
        self.reference_tree.delete(*self.reference_tree.get_children())
        self.reference_rows.clear()
        self.reference_warning.configure(text='')
        self.reference_warning.pack_forget()
        if line is None:
            self.reference_summary.configure(text='请选择左侧基板。')
        else:
            name = line.fields[0] if line.fields else ''
            self.reference_summary.configure(text=f'{name}：正在查找引用…')
            self.reference_after = self.after(120, lambda: self.update_references(line))

    def update_references(self, line, force=False):
        self.reference_after = None
        if line is not self.active or not self.doc or not self.path:
            return
        fields = line.fields
        if len(fields) <= 6 or not fields[6].strip().isdigit():
            self.reference_summary.configure(text='当前基板没有有效的模板编号。')
            return
        template_id = fields[6].strip()
        try:
            if force or template_id not in self.reference_cache:
                self.reference_cache[template_id] = find_references(self.path, template_id)
            references, warnings = self.reference_cache[template_id]
        except Exception as exc:
            self.reference_summary.configure(text=f'引用检查失败：{exc}')
            return
        counts = Counter(reference.kind for reference in references)
        self.reference_summary.configure(
            text=f'{fields[0]}（TEMPNO={template_id}）｜实例 {counts["实例"]} 条｜'
                 f'遇敌组 {counts["遇敌组"]} 条｜地图遇敌 {counts["地图遇敌"]} 条｜'
                 f'NPC数据 {counts["NPC数据"]} 条')
        warning_text = '；'.join(warnings)
        self.reference_warning.configure(text=warning_text)
        if warning_text:
            self.reference_warning.pack(fill='x', after=self.reference_summary, pady=(0, 4))
        else:
            self.reference_warning.pack_forget()
        self.reference_tree.delete(*self.reference_tree.get_children())
        self.reference_rows.clear()
        for reference in references:
            try:
                shown_path = str(reference.path.relative_to(self.path.parent))
            except ValueError:
                shown_path = str(reference.path)
            item = self.reference_tree.insert('', 'end', values=(reference.kind, shown_path,
                                                                  reference.line, reference.detail))
            self.reference_rows[item] = reference
        if not references:
            self.reference_tree.insert('', 'end', values=('未发现', '', '',
                                                          '在当前支持的服务器数据链中没有找到调用。'))

    def reference_context_menu(self, event):
        item = self.reference_tree.identify_row(event.y)
        reference = self.reference_rows.get(item)
        if reference is None or reference.kind not in ('实例', '遇敌组'):
            return
        self.reference_tree.selection_set(item)
        self.reference_tree.focus(item)
        action = '删除实例' if reference.kind == '实例' else '删除遇敌组'
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=action, command=lambda selected=reference: self.delete_reference(selected))
        menu.tk_popup(event.x_root, event.y_root)
        return 'break'

    def delete_reference(self, reference):
        if not self.path or self.active is None or len(self.active.fields) <= 6:
            return
        expected_name = {'实例': 'enemy.txt', '遇敌组': 'group1.txt'}.get(reference.kind)
        if not expected_name or reference.path.name.casefold() != expected_name:
            messagebox.showerror('删除失败', '不支持删除这类引用记录。')
            return
        if reference.path.parent.resolve() != self.path.parent.resolve():
            messagebox.showerror('删除失败', '引用文件不在当前 enemybase.txt 所在目录。')
            return
        try:
            document = Document(reference.path.read_bytes())
            index = reference.line - 1
            if index < 0 or index >= len(document.lines):
                raise ValueError('引用行号已失效，请重新选择基板后再试。')
            line = document.lines[index]
            fields = line.fields or []
            if line.text != reference.source_text:
                raise ValueError('文件内容已变化，引用行号已失效；请刷新后重试。')
            template_id = self.active.fields[6].strip()
            if reference.kind == '实例':
                if len(fields) < 5 or fields[4].strip() != template_id:
                    raise ValueError('该实例已不再引用当前基板，请刷新后重试。')
                effect = '此操作只删除 enemy.txt 中的实例行，不会自动清理遇敌组或 NPC 中的关联内容。'
            else:
                enemy_path = self.path.parent / 'enemy.txt'
                enemy_ids = {
                    values[3]
                    for _number, _text, values in read_data_lines(enemy_path)
                    if len(values) >= 5 and values[4] == template_id
                } if enemy_path.exists() else set()
                if not enemy_ids.intersection(value.strip() for value in fields[4:14]):
                    raise ValueError('该遇敌组已不再引用当前基板的实例，请刷新后重试。')
                effect = '此操作只删除 group1.txt 中的遇敌组行，不会自动清理地图遇敌中的关联内容。'
        except Exception as exc:
            messagebox.showerror('删除失败', str(exc))
            self.reference_cache.clear()
            self.show_references()
            return

        if not messagebox.askyesno(
            f'删除{reference.kind}',
            f'删除 {reference.path.name} 第 {reference.line} 行？\n'
            f'{reference.detail}\n\n{effect}\n此操作不能撤销。是否继续？',
        ):
            return
        document.lines.pop(index)
        try:
            document.save(reference.path, document.digest)
        except Exception as exc:
            messagebox.showerror('删除失败', str(exc))
            return
        self.reference_cache.clear()
        self.show_references()

    def add(self, duplicate=False):
        if not self.doc:
            return
        if duplicate and self.active is None:
            messagebox.showinfo('复制', '请先选择一条记录。')
            return
        if not duplicate:
            self.new_template_dialog()
            return
        used = {int(line.fields[6]) for line in self.doc.records() if len(line.fields) > 6 and line.fields[6].strip().isdigit()}
        number = max(used, default=0) + 1
        fields = list(self.active.fields) if duplicate else [''] * 56
        if len(fields) != 56:
            messagebox.showerror('复制失败', '请先修复记录字段数量。')
            return
        fields[6] = str(number)
        if not duplicate:
            fields[0] = '新模板'
        self.snapshot()
        if self.doc.lines and not self.doc.lines[-1].ending:
            self.doc.lines[-1].ending = self.doc.newline
        line = Line('', self.doc.newline, 0, fields, None)
        self.doc.lines.append(line)
        self.query.set('')
        self.filter.set('全部')
        self.load_record(line)
        self.refresh()
        self.tree.see(str(len(self.doc.lines)-1))

    def new_template_dialog(self, edit_line=None):
        editing = edit_line is not None
        edit_fields = list(edit_line.fields) if editing else None
        if editing and len(edit_fields) != 56:
            if not messagebox.askyesno(
                '修复字段数量',
                f'此记录有 {len(edit_fields)} 列。应用修改时将调整为 56 列，是否继续？',
            ):
                return
            edit_fields = (edit_fields + [''] * 56)[:56]
        if editing:
            template_id = edit_fields[6]
        else:
            numeric_ids = [int(line.fields[6]) for line in self.doc.records()
                           if len(line.fields) > 6 and line.fields[6].strip().isdigit()]
            template_id = str(max(numeric_ids, default=0) + 1)
        used_images = {int(line.fields[36].strip()) for line in self.doc.records()
                       if len(line.fields) > 36 and line.fields[36].strip().isdigit()}
        image_ids = []
        image_source_message = '未选择客户端 data；可以手动填写形象编号。'
        if self.client_data_path:
            try:
                client_images = client_image_numbers(self.client_data_path)
                image_ids = sorted(client_images if editing else client_images - used_images)
                image_source_message = (f'客户端形象：{len(image_ids)} 个' if editing else
                                        f'客户端存在且 enemybase 未使用的形象：{len(image_ids)} 个')
            except Exception as exc:
                image_source_message = f'读取客户端形象失败：{exc}；可以手动填写。'

        dialog = tk.Toplevel(self)
        dialog.title('编辑宠物基板' if editing else '新增宠物基板')
        dialog.minsize(700, 640)
        dialog.transient(self)
        center_window(dialog, 760, 700)
        dialog.grab_set()

        # Keep the action bar visible even when the form is taller than the screen.
        buttons = ttk.Frame(dialog, padding=12)
        buttons.pack(side='bottom', fill='x')
        body = ttk.Frame(dialog)
        body.pack(fill='both', expand=True)
        form_canvas = tk.Canvas(body, highlightthickness=0, borderwidth=0)
        form_scrollbar = ttk.Scrollbar(body, orient='vertical', command=form_canvas.yview)
        form_canvas.configure(yscrollcommand=form_scrollbar.set)
        form_scrollbar.pack(side='right', fill='y')
        form_canvas.pack(side='left', fill='both', expand=True)
        content = ttk.Frame(form_canvas, padding=12)
        content_window = form_canvas.create_window((0, 0), window=content, anchor='nw')
        content.bind('<Configure>', lambda _event: form_canvas.configure(scrollregion=form_canvas.bbox('all')))
        form_canvas.bind('<Configure>', lambda event: form_canvas.itemconfigure(content_window, width=event.width))
        form = ttk.Frame(content)
        form.pack(side='left', fill='both', expand=True)
        preview_panel = ttk.LabelFrame(content, text='客户端模型预览', padding=5)
        preview_panel.pack(side='right', anchor='n', padx=(10, 0))
        preview_canvas = tk.Canvas(preview_panel, width=260, height=350, bg='#3c3c3c', highlightthickness=0)
        preview_canvas.pack()
        preview_state = {'photo': None}

        ttk.Label(form, text='模板编号').grid(row=0, column=0, sticky='w', pady=4)
        ttk.Label(form, text=template_id).grid(row=0, column=1, sticky='w', padx=8, pady=4)
        ttk.Label(form, text='名称').grid(row=1, column=0, sticky='w', pady=4)
        name_var = tk.StringVar(value=edit_fields[0] if editing else '')
        name_entry = ttk.Entry(form, textvariable=name_var, width=24)
        name_entry.grid(row=1, column=1, columnspan=3, sticky='ew', padx=8, pady=4)
        ttk.Label(form, text='形象编号').grid(row=2, column=0, sticky='w', pady=4)
        image_var = tk.StringVar(value=edit_fields[36] if editing else '')
        image_box = ttk.Combobox(form, textvariable=image_var, values=[str(i) for i in image_ids], width=20)
        image_box.grid(row=2, column=1, columnspan=3, sticky='ew', padx=8, pady=4)
        ttk.Label(form, text=image_source_message, wraplength=340).grid(
            row=3, column=0, columnspan=4, sticky='w', padx=2, pady=(0, 8))

        def update_model_preview(_event=None):
            # Use the canvas's fixed requested dimensions. During the first
            # idle callback, winfo_width/height can still be 1, placing the
            # placeholder and model at the upper-left corner.
            center_x = preview_canvas.winfo_reqwidth() // 2
            center_y = preview_canvas.winfo_reqheight() // 2
            preview_canvas.delete('all')
            image_text = image_var.get().strip()
            if not image_text.isdigit():
                preview_canvas.create_text(center_x, center_y, text='选择形象编号查看模型', fill='white')
                preview_state['photo'] = None
                return
            if self.client_data_path is None:
                preview_canvas.create_text(center_x, center_y, text='请先打开clientData', fill='white')
                preview_state['photo'] = None
                return
            try:
                png, _width, _height = model_preview_png(self.client_data_path, int(image_text))
                photo = tk.PhotoImage(data=base64.b64encode(png))
                preview_state['photo'] = photo
                preview_canvas.create_image(
                    center_x,
                    center_y,
                    image=photo,
                    anchor='center',
                )
            except Exception:
                preview_state['photo'] = None
                preview_canvas.create_text(center_x, center_y, text='模型预览不可用', fill='white')

        image_box.bind('<<ComboboxSelected>>', update_model_preview)
        image_box.bind('<Return>', update_model_preview)
        image_box.bind('<FocusOut>', update_model_preview)

        base_vars = {}
        attribute_vars = {}
        resistance_vars = {}
        skill_vars = {}

        def add_field_group(title, fields, variables, start_row, columns=2):
            group = ttk.LabelFrame(form, text=title, padding=8)
            group.grid(row=start_row, column=0, columnspan=4, sticky='ew', pady=5)
            for index, (field_index, label, default) in enumerate(fields):
                pair = index % columns
                line = index // columns
                col = pair * 2
                ttk.Label(group, text=label).grid(row=line, column=col, sticky='w', padx=(2, 5), pady=3)
                variable = tk.StringVar(value=edit_fields[field_index] if editing else default)
                variables[field_index] = variable
                ttk.Entry(group, textvariable=variable, width=10).grid(
                    row=line, column=col + 1, sticky='w', padx=(0, 12), pady=3)
            return group

        base_group = add_field_group('基础属性', [
            (7, '初始点数', '40'), (8, '升级点数', '4.5'),
            (9, '体力', ''), (10, '力量', ''),
            (11, '耐力', ''), (12, '敏捷', ''),
        ], base_vars, 4)
        add_field_group('属性', [
            (15, '地属性', '0'), (16, '水属性', '0'), (17, '火属性', '0'), (18, '风属性', '0'),
        ], attribute_vars, 5)
        add_field_group('抗性', [
            (19, '毒抗', '0'), (20, '麻痹抗', '0'), (21, '睡眠抗', '0'),
            (22, '石化抗', '0'), (23, '酒醉抗', '0'), (24, '混乱抗', '0'),
        ], resistance_vars, 6)
        add_field_group('技能', [
            (25, '技能 1', '1'), (26, '技能 2', '2'), (27, '技能 3', ''),
            (28, '技能 4', ''), (29, '技能 5', ''), (30, '技能 6', ''), (31, '技能 7', ''),
        ], skill_vars, 7, columns=2)

        def confirm():
            name = name_var.get().strip()
            image_text = image_var.get().strip()
            if not name:
                messagebox.showerror('输入不完整', '请填写宠物名称。', parent=dialog)
                name_entry.focus_set()
                return
            if not re.fullmatch(r'\d+', image_text):
                messagebox.showerror('输入不完整', '请选择或填写有效的形象编号。', parent=dialog)
                image_box.focus_set()
                return
            fields = list(edit_fields) if editing else [''] * 56
            fields[0], fields[6], fields[36] = name, template_id, image_text
            if not editing:
                for field_index, value in NEW_TEMPLATE_DEFAULTS.items():
                    fields[field_index] = value
            for field_index, variable in base_vars.items():
                value = variable.get().strip()
                number_pattern = r'[+-]?\d+(?:\.\d+)?' if field_index == 8 else r'[+-]?\d+'
                if value and not re.fullmatch(number_pattern, value):
                    messagebox.showerror('格式错误', f'{LABELS[field_index]}格式不正确。', parent=dialog)
                    return
                fields[field_index] = value
            for field_index, variable in {**attribute_vars, **resistance_vars}.items():
                value = variable.get().strip()
                if value and not re.fullmatch(r'[+-]?\d+', value):
                    messagebox.showerror('格式错误', f'{LABELS[field_index]}必须填写整数。', parent=dialog)
                    return
                fields[field_index] = value if editing else value or '0'
            for field_index, variable in skill_vars.items():
                value = variable.get().strip()
                if value and not re.fullmatch(r'\d+', value):
                    messagebox.showerror('格式错误', f'{LABELS[field_index]}必须填写非负整数。', parent=dialog)
                    return
                fields[field_index] = value
            if any(any(char in value for char in ',\r\n') for value in fields):
                messagebox.showerror('格式错误', '字段内容不能包含逗号或换行。', parent=dialog)
                return
            try:
                ','.join(fields).encode(self.doc.encoding)
            except UnicodeEncodeError:
                messagebox.showerror('编码错误', f'当前文件编码无法保存名称「{name}」。', parent=dialog)
                return

            if editing and fields == edit_line.fields:
                dialog.destroy()
                return
            self.snapshot()
            if editing:
                line = edit_line
                line.fields = fields
            else:
                if self.doc.lines and not self.doc.lines[-1].ending:
                    self.doc.lines[-1].ending = self.doc.newline
                line = Line('', self.doc.newline, 0, fields, None)
                self.doc.lines.append(line)
                self.query.set('')
                self.filter.set('全部')
            self.load_record(line)
            self.refresh()
            item = next((key for key, value in self.visible.items() if value is line), None)
            if item is None:
                self.query.set('')
                self.filter.set('全部')
                self.refresh()
                item = next((key for key, value in self.visible.items() if value is line), None)
            if item is not None:
                self.tree.selection_set(item)
                self.tree.focus(item)
                self.tree.see(item)
            dialog.destroy()

        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='right', padx=4)
        ttk.Button(buttons, text='应用修改' if editing else '新增基板', command=confirm).pack(side='right', padx=4)
        form.columnconfigure(1, weight=1)
        name_entry.focus_set()

        def align_preview_with_base_stats():
            content.update_idletasks()
            target_y = max(0, base_group.winfo_rooty() - content.winfo_rooty())
            preview_panel.pack_configure(pady=(target_y, 0))
            dialog.update_idletasks()
            needed_height = content.winfo_reqheight() + buttons.winfo_reqheight()
            available_height = max(640, dialog.winfo_screenheight() - 100)
            center_window(dialog, 760, min(max(700, needed_height), available_height))
            form_canvas.configure(scrollregion=form_canvas.bbox('all'))
            update_model_preview()

        dialog.after_idle(align_preview_with_base_stats)

    def delete(self):
        if not self.doc:
            return
        selected = [self.visible[key] for key in self.tree.selection() if key in self.visible]
        if not selected or not messagebox.askyesno('删除记录', f'删除选中的 {len(selected)} 条记录？可使用撤销恢复。'):
            return
        self.snapshot()
        selected_ids = {id(line) for line in selected}
        self.doc.lines = [line for line in self.doc.lines if id(line) not in selected_ids]
        self.load_record(None)
        self.refresh()

    def undo(self):
        if self.doc and self.history:
            self.doc.lines = self.history.pop()
            self.load_record(None)
            self.refresh()

    def check(self):
        if not self.doc:
            return
        issues = self.doc.issues()
        report = []
        exact = Counter(tuple(line.fields) for line in self.doc.records())
        for line in self.doc.records():
            if id(line) in issues:
                report.append(f'原行 {line.number or "新增"} / {line.fields[0]}：'+ '；'.join(issues[id(line)]))
        report.append(f'完全重复记录组数：{sum(n > 1 for n in exact.values())}')
        window = tk.Toplevel(self)
        window.title('检查报告')
        window.transient(self)
        center_window(window, 800, 500)
        text = tk.Text(window, wrap='word')
        text.pack(fill='both', expand=True)
        text.insert('end', '\n'.join(report) if issues else '字段与编号检查通过。\n' + report[-1])
        text.configure(state='disabled')
        self.refresh()

    def add_instance(self):
        if not self.doc or self.active is None:
            messagebox.showinfo('新增实例', '请先选择一个宠物基板。')
            return
        if self.doc.bytes() != self.baseline:
            messagebox.showinfo('新增实例', '请先保存 enemybase.txt，再为该基板新增实例。')
            return
        fields = self.active.fields
        if len(fields) <= 6 or not fields[6].strip().isdigit() or not fields[0].strip():
            messagebox.showerror('新增实例', '当前基板需要有效的名称和模板编号。')
            return
        enemy_path = self.path.parent / 'enemy.txt'
        if not messagebox.askyesno(
            '新增实例',
            f'将向 {enemy_path.name} 新增「{fields[0]}」的实例。\n'
            '等级固定为 1–1，实例编号自动使用当前最大编号加 1。是否继续？',
        ):
            return
        try:
            enemy_id = create_level_one_instance(enemy_path, fields[6].strip(), fields[0].strip())
        except Exception as exc:
            messagebox.showerror('新增实例失败', str(exc))
            return
        self.reference_cache.clear()
        self.show_references()
        messagebox.showinfo('新增实例完成', f'已新增 ENEMY_ID={enemy_id}，等级为 1–1。')

    def show_references(self):
        if not self.doc or self.active is None:
            messagebox.showinfo('查看引用', '请先选择一个宠物基板。')
            return
        if self.reference_after is not None:
            self.after_cancel(self.reference_after)
            self.reference_after = None
        self.reference_summary.configure(text='正在查找引用…')
        self.reference_warning.configure(text='')
        self.reference_warning.pack_forget()
        self.reference_tree.delete(*self.reference_tree.get_children())
        self.reference_rows.clear()
        line = self.active
        self.reference_after = self.after(0, lambda: self.update_references(line, force=True))

    def organize(self):
        if not self.doc:
            return
        if any(len(line.fields) <= 6 or not line.fields[6].strip().isdigit() for line in self.doc.records()):
            messagebox.showerror('无法整理', '请先修复缺失或非法模板编号。')
            return
        records = sorted(self.doc.records(), key=lambda line: int(line.fields[6]))
        positions = [i for i, line in enumerate(self.doc.lines) if line.fields is not None]
        moved = sum(self.doc.lines[i] is not line for i, line in zip(positions, records))
        if not moved:
            messagebox.showinfo('整理', '当前已按模板编号升序排列。')
            return
        if not messagebox.askyesno('整理预览', f'{moved} 条记录将改变位置。\n注释和空行保留原位置，编号保持不变。\n是否执行？'):
            return
        self.snapshot()
        endings = [self.doc.lines[i].ending for i in positions]
        for i, line, ending in zip(positions, records, endings):
            line.ending = ending
            self.doc.lines[i] = line
        self.load_record(None)
        self.refresh()

    def save(self, save_as=False):
        if not self.doc:
            return False
        issues = self.doc.issues()
        if issues:
            messagebox.showerror('保存前检查', f'有 {len(issues)} 条异常记录，请点击「检查」修复后保存。')
            return False
        target = self.path
        if save_as:
            name = filedialog.asksaveasfilename(title='另存为', initialfile='enemybase.txt', defaultextension='.txt', filetypes=[('文本文件', '*.txt')])
            if not name:
                return False
            target = Path(name)
        try:
            new = sum(line.number == 0 for line in self.doc.records())
            changed = sum(line.number != 0 and line.fields != line.original for line in self.doc.records())
            if not messagebox.askyesno('保存变更', f'保存到：{target}\n当前 {len(self.doc.records())} 条记录；新增 {new} 条，字段修改 {changed} 条。是否保存？'):
                return False
            self.doc.save(target, self.doc.digest if target.resolve() == self.path.resolve() else None)
            self.path = target
            data = target.read_bytes()
            self.doc = Document(data, self.doc.encoding)
            self.baseline = data
            self.history.clear()
            self.reference_cache.clear()
            self.load_record(None)
            self.enemybase_path_label.configure(text=str(target))
            self.refresh()
            self.remember_paths()
            return True
        except Exception as exc:
            messagebox.showerror('保存失败', str(exc))
            return False

    def close(self):
        if self.leave():
            self.destroy()


if __name__ == '__main__':
    Editor().mainloop()
