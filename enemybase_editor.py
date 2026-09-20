"""GBK-compatible EnemyBase desktop editor; run with Python 3.10+."""
from __future__ import annotations

import copy
import hashlib
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

LABELS = ['名称', '素材名称1', '素材名称2', '素材名称3', '素材名称4', '素材名称5',
          '模板编号', '初始点数 (INITNUM)', '升级点数 (LVUPPOINT)', '基础体力', '基础力量',
          '基础耐力', '基础敏捷', 'AI参数 (MODAI)', '捕捉参数 (GET)', '地属性', '水属性',
          '火属性', '风属性', '毒抗性', '麻痹抗性', '睡眠抗性', '石化抗性', '酒醉抗性',
          '混乱抗性'] + [f'技能{i}' for i in range(1, 8)] + [
          '稀有度', '暴击参数', '反击参数', '技能槽数', '形象编号', '宠物标志', '尺寸'] + [
          f'素材{i}{suffix}' for i in range(1, 6) for suffix in ('基础加成', '固定最小值', '固定最大值')
          ] + ['等级上限', '扩展字段（源码未定义）']


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
        if target.exists():
            current = target.read_bytes()
            if expected_digest is not None and hashlib.sha256(current).digest() != expected_digest:
                raise ValueError('文件已被其他程序修改，请另存为或重新打开。')
            # Keep one rolling backup: each save replaces it with the version
            # that was on disk immediately before this save.
            backup = target.with_name(target.name + '.bak')
            backup.write_bytes(current)
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
        for number, _text, fields in read_data_lines(enemy_path):
            # This server build has ACT_CONDITION in column 3, followed by
            # ENEMY_ID and ENEMY_TEMPNO in columns 4 and 5.
            if len(fields) >= 5 and fields[4] == template_id:
                enemy_ids.add(fields[3])
                references.append(Reference('实例', enemy_path, number,
                                             f'ENEMY_ID={fields[3]}，名称={fields[0]}，等级={fields[5]}–{fields[6]}'))
    else:
        warnings.append('同目录未找到 enemy.txt')

    if group_path.exists():
        for number, _text, fields in read_data_lines(group_path):
            used = sorted(enemy_ids.intersection(fields[4:14]),
                          key=lambda value: (0, int(value)) if value.isdigit() else (1, value))
            if used:
                group_ids.add(fields[1])
                references.append(Reference('遇敌组', group_path, number,
                                             f'GROUP_ID={fields[1]}，名称={fields[0]}，引用 ENEMY_ID={"、".join(used)}'))
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



class Editor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('EnemyBase 编辑器')
        self.geometry('1250x850')
        self.minsize(1050, 700)
        self.doc = None
        self.path = None
        self.baseline = b''
        self.history = []
        self.active = None
        self.form_original = []
        self.visible = {}
        self.protocol('WM_DELETE_WINDOW', self.close)
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill='x')
        for label, callback in [('打开文件', self.open), ('保存', self.save), ('另存为', lambda: self.save(True)),
                                ('新增', self.add), ('复制', lambda: self.add(True)), ('删除', self.delete),
                                ('撤销', self.undo), ('检查', self.check),
                                ('按编号整理', self.organize), ('查看引用', self.show_references)]:
            ttk.Button(bar, text=label, command=callback).pack(side='left', padx=2)
        self.encoding = tk.StringVar(value='自动')
        ttk.Combobox(bar, textvariable=self.encoding, values=['自动', 'gbk', 'utf-8', 'utf-8-sig'], state='readonly', width=10).pack(side='right')
        ttk.Label(bar, text='打开编码：').pack(side='right')
        self.path_label = ttk.Label(self, text='请点击「打开文件」自行选择 enemybase.txt', padding=8)
        self.path_label.pack(fill='x')
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
        right = ttk.Frame(pane)
        pane.add(right, weight=3)
        ttk.Label(right, text='修改后点击「应用修改」。空值保持为空；扩展字段按原文保存。', wraplength=570).pack(pady=5)
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill='both', expand=True)
        self.vars = [tk.StringVar() for _ in LABELS]
        groups = [('基本信息', [0] + list(range(6, 15)) + list(range(32, 39)) + [54, 55]),
                  ('属性与抗性', list(range(15, 25))), ('技能', list(range(25, 32))),
                  ('素材', list(range(1, 6)) + list(range(39, 54)))]
        for title, indices in groups:
            frame = ttk.Frame(self.notebook, padding=8)
            self.notebook.add(frame, text=title)
            for row, i in enumerate(indices):
                ttk.Label(frame, text=f'{i+1}. {LABELS[i]}').grid(row=row, column=0, sticky='w', pady=2)
                ttk.Entry(frame, textvariable=self.vars[i], width=34).grid(row=row, column=1, sticky='ew', padx=8, pady=2)
            frame.columnconfigure(1, weight=1)
        raw_frame = ttk.Frame(self.notebook)
        self.notebook.add(raw_frame, text='原始行 / 检查')
        self.raw = tk.Text(raw_frame, wrap='word', height=12)
        self.raw.pack(fill='both', expand=True)
        self.raw.configure(state='disabled')
        ttk.Button(right, text='应用修改', command=self.apply).pack(pady=8)
        self.status = ttk.Label(self, text='尚未打开文件', padding=8)
        self.status.pack(fill='x')
        self.bind('<Control-s>', lambda e: self.save())
        self.bind('<Control-o>', lambda e: self.open())

    def form_dirty(self):
        return self.active is not None and [v.get() for v in self.vars] != self.form_original

    def resolve_form(self):
        if not self.form_dirty():
            return True
        choice = messagebox.askyesnocancel('未应用的修改', '是否应用右侧表单修改？\n选择“否”放弃表单修改。')
        if choice is None:
            return False
        if choice:
            return self.apply()
        self.load_form(self.active)
        return True

    def leave(self):
        if not self.resolve_form():
            return False
        if self.doc and self.doc.bytes() != self.baseline:
            answer = messagebox.askyesnocancel('未保存', '是否保存当前文件修改？')
            return self.save() if answer else answer is not None
        return True

    def open(self):
        if not self.leave():
            return
        name = filedialog.askopenfilename(title='选择 enemybase.txt', filetypes=[('文本文件', '*.txt'), ('所有文件', '*.*')])
        if not name:
            return
        try:
            path = Path(name)
            data = path.read_bytes()
            doc = Document(data, self.encoding.get())
        except Exception as exc:
            messagebox.showerror('读取失败', str(exc))
            return
        self.doc, self.path, self.baseline = doc, path, data
        self.history.clear()
        self.active = None
        self.path_label.configure(text=str(path))
        self.load_form(None)
        self.refresh()

    def snapshot(self):
        self.history.append(copy.deepcopy(self.doc.lines))
        self.history = self.history[-100:]

    def refresh_filter(self):
        if self.resolve_form():
            self.active = None
            self.load_form(None)
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
        if not self.resolve_form():
            for key, line in self.visible.items():
                if line is self.active:
                    self.tree.selection_set(key)
                    break
            return
        self.load_form(target)

    def load_form(self, line):
        self.active = line
        fields = line.fields if line else []
        for i, variable in enumerate(self.vars):
            variable.set(fields[i] if i < len(fields) else '')
        self.form_original = [v.get() for v in self.vars]
        self.raw.configure(state='normal')
        self.raw.delete('1.0', 'end')
        if line:
            errors = self.doc.issues().get(id(line), [])
            self.raw.insert('end', ','.join(fields) + '\n\n' + ('\n'.join(errors) or '检查通过'))
        self.raw.configure(state='disabled')

    def apply(self):
        if self.active is None:
            return True
        f = [v.get() for v in self.vars]
        if f == self.form_original:
            return True
        if any(any(c in value for c in ',\r\n') for value in f):
            messagebox.showerror('格式错误', '字段内容不能包含逗号或换行。')
            return False
        for i, value in enumerate(f[6:55], 6):
            if value.strip() and not re.fullmatch(r'[+-]?\d+(?:\.\d+)?' if i == 8 else r'[+-]?\d+', value.strip()):
                messagebox.showerror('格式错误', f'{LABELS[i]}必须为数字，或留空。')
                return False
        if not re.fullmatch(r'\d+', f[6].strip()) or not f[0].strip():
            messagebox.showerror('格式错误', '名称不能为空；模板编号必须是非负整数。')
            return False
        if any(line is not self.active and len(line.fields) > 6 and line.fields[6].strip() == f[6].strip() for line in self.doc.records()):
            messagebox.showerror('编号冲突', '模板编号已存在，请使用其他编号。')
            return False
        try:
            ','.join(f).encode(self.doc.encoding)
        except UnicodeEncodeError:
            messagebox.showerror('编码错误', '当前文件编码无法保存输入的字符。')
            return False
        old = self.active.fields
        if len(old) != 56 and not messagebox.askyesno('修复字段数量', f'此记录有 {len(old)} 列，应用表单将调整为 56 列。是否继续？'):
            return False
        self.snapshot()
        self.active.fields = f
        self.load_form(self.active)
        self.refresh()
        return True

    def add(self, duplicate=False):
        if not self.doc or not self.resolve_form():
            return
        if duplicate and self.active is None:
            messagebox.showinfo('复制', '请先选择一条记录。')
            return
        used = {int(line.fields[6]) for line in self.doc.records() if len(line.fields) > 6 and line.fields[6].strip().isdigit()}
        number = 1
        while number in used:
            number += 1
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
        self.load_form(line)
        self.refresh()
        self.tree.see(str(len(self.doc.lines)-1))

    def delete(self):
        if not self.doc or not self.resolve_form():
            return
        selected = [self.visible[key] for key in self.tree.selection() if key in self.visible]
        if not selected or not messagebox.askyesno('删除记录', f'删除选中的 {len(selected)} 条记录？可使用撤销恢复。'):
            return
        self.snapshot()
        selected_ids = {id(line) for line in selected}
        self.doc.lines = [line for line in self.doc.lines if id(line) not in selected_ids]
        self.load_form(None)
        self.refresh()

    def undo(self):
        if self.doc and self.history and self.resolve_form():
            self.doc.lines = self.history.pop()
            self.load_form(None)
            self.refresh()

    def check(self):
        if not self.doc or not self.resolve_form():
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
        window.geometry('800x500')
        text = tk.Text(window, wrap='word')
        text.pack(fill='both', expand=True)
        text.insert('end', '\n'.join(report) if issues else '字段与编号检查通过。\n' + report[-1])
        text.configure(state='disabled')
        self.refresh()

    def show_references(self):
        if not self.doc or self.active is None:
            messagebox.showinfo('查看引用', '请先选择一个宠物基板。')
            return
        if not self.resolve_form():
            return
        fields = self.active.fields
        if len(fields) <= 6 or not fields[6].strip().isdigit():
            messagebox.showerror('查看引用', '当前基板没有有效的模板编号。')
            return
        template_id = fields[6].strip()
        try:
            references, warnings = find_references(self.path, template_id)
        except Exception as exc:
            messagebox.showerror('引用检查失败', str(exc))
            return
        window = tk.Toplevel(self)
        window.title(f'基板引用：{fields[0]}（TEMPNO={template_id}）')
        window.geometry('1050x600')
        counts = Counter(reference.kind for reference in references)
        summary = (f'实例 {counts["实例"]} 条｜遇敌组 {counts["遇敌组"]} 条｜'
                   f'地图遇敌 {counts["地图遇敌"]} 条｜NPC数据 {counts["NPC数据"]} 条')
        ttk.Label(window, text=summary, padding=8).pack(fill='x')
        if warnings:
            ttk.Label(window, text='；'.join(warnings), foreground='#b45309',
                      padding=(8, 0, 8, 8)).pack(fill='x')
        frame = ttk.Frame(window, padding=(8, 0, 8, 8))
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=('kind', 'file', 'line', 'detail'), show='headings')
        for key, title, width in [('kind', '类型', 80), ('file', '文件', 260),
                                  ('line', '行号', 60), ('detail', '引用详情', 600)]:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=50)
        yscroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        xscroll = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side='right', fill='y')
        xscroll.pack(side='bottom', fill='x')
        tree.pack(fill='both', expand=True)
        for reference in references:
            try:
                shown_path = str(reference.path.relative_to(self.path.parent))
            except ValueError:
                shown_path = str(reference.path)
            tree.insert('', 'end', values=(reference.kind, shown_path, reference.line, reference.detail))
        if not references:
            tree.insert('', 'end', values=('未发现', '', '', '在当前支持的服务器数据链中没有找到调用。'))

    def organize(self):
        if not self.doc or not self.resolve_form():
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
        self.load_form(None)
        self.refresh()

    def save(self, save_as=False):
        if not self.doc or not self.resolve_form():
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
            if not messagebox.askyesno('保存变更', f'保存到：{target}\n当前 {len(self.doc.records())} 条记录；新增 {new} 条，字段修改 {changed} 条。\n覆盖已有文件前会创建 .bak 备份。是否保存？'):
                return False
            self.doc.save(target, self.doc.digest if target.resolve() == self.path.resolve() else None)
            self.path = target
            data = target.read_bytes()
            self.doc = Document(data, self.doc.encoding)
            self.baseline = data
            self.history.clear()
            self.load_form(None)
            self.path_label.configure(text=str(target))
            self.refresh()
            return True
        except Exception as exc:
            messagebox.showerror('保存失败', str(exc))
            return False

    def close(self):
        if self.leave():
            self.destroy()


if __name__ == '__main__':
    Editor().mainloop()
