from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from catalog import CATALOGS, cell, display


class Browser(ttk.Frame):
    def __init__(self, master, app, modes, scoped=False):
        super().__init__(master, padding=12)
        self.app = app
        self.modes = modes
        self.kind = modes[0][1]
        self.scoped = scoped
        self.total = 0
        self.scope = None
        self.rows = {}
        self.loaded = False
        self.generation = 0
        self.keyword = tk.StringVar()
        self.info = tk.StringVar(value='请先在设置中连接数据库。')
        self.mode = tk.StringVar(value=modes[0][0])
        self.buttons = []
        self.content = ttk.Frame(self)
        self.content.pack(fill='both', expand=True)
        bar = ttk.Frame(self.content)
        bar.pack(fill='x', pady=(0, 10))
        self.mode_box = ttk.Combobox(bar, values=[label for label, kind in modes],
                                     textvariable=self.mode, state='readonly', width=12)
        if len(modes) > 1:
            self.mode_box.pack(side='left', padx=(0, 10))
        self.mode_box.bind('<<ComboboxSelected>>', self.change_mode)
        self.search_entry = ttk.Entry(bar, textvariable=self.keyword)
        if not scoped:
            self.search_entry.pack(side='left', fill='x', expand=True)
        self.search_entry.bind('<Return>', lambda event: self.search())
        actions = (('刷新', self.search),) if scoped else (('搜索 / 刷新', self.search), ('清除筛选', self.clear_filter))
        for label, action in actions:
            button = ttk.Button(bar, text=label, command=action)
            button.pack(side='left', padx=(6, 0))
            self.buttons.append(button)
        listing = ttk.Frame(self.content)
        listing.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(listing, show='headings', selectmode='browse', height=5 if self.kind in ('accounts', 'characters') else 12)
        self.tree.grid(row=0, column=0, sticky='nsew')
        vertical = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=vertical.set)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        if self.kind != 'characters':
            self.tree.bind('<Double-1>', lambda event: self.detail())
        self.tree.bind('<<TreeviewSelect>>', self.selected_row)
        ttk.Label(self.content, textvariable=self.info).pack(anchor='w', pady=(6, 0))
        self.configure_columns()
        self.set_enabled(False)

    def configure_columns(self):
        columns = CATALOGS[self.kind].columns
        self.tree.configure(columns=[key for key, label in columns])
        for key, label in columns:
            self.tree.heading(key, text=label, anchor='w')
            self.tree.column(key, width=130, minwidth=20, stretch=True, anchor='w')

    def set_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for button in self.buttons:
            button.configure(state=state)
        self.search_entry.configure(state=state)
        self.mode_box.configure(state='readonly' if enabled else 'disabled')

    def clear_results(self):
        self.generation += 1
        self.loaded = False
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()
        self.total = 0

    def change_mode(self, event=None):
        self.kind = dict(self.modes)[self.mode.get()]
        self.clear_results()
        self.configure_columns()
        self.search()

    def search(self):
        self.load()

    def selected_row(self, event=None):
        selection = self.tree.selection()
        if selection and selection[0] in self.rows:
            self.app.selected_row(self, self.rows[selection[0]])

    def clear_filter(self):
        self.keyword.set('')
        if self.kind == 'accounts':
            self.scope = None
        self.search()

    def load(self):
        if not self.app.connected or self.app.busy or (self.scoped and self.scope is None):
            return
        kind, keyword, scope = self.kind, '' if self.scoped else self.keyword.get(), self.scope
        self.info.set('正在查询…')
        self.app.request(self, lambda: self.app.repository.page(kind, keyword, page=None, scope=scope), self.show_page)

    def show_page(self, result):
        self.clear_results()
        self.loaded = True
        self.total = result['total']
        for row in result['rows']:
            identity = str(row['id'])
            self.rows[identity] = row
            self.tree.insert('', 'end', iid=identity,
                             values=[cell(row, key, CATALOGS[self.kind]) for key, label in CATALOGS[self.kind].columns])
        scope = ' · 关联筛选' if self.scope else ''
        self.info.set(f'共 {self.total} 条{scope}')
        if self.kind == 'characters':
            self.app.show_roles(result['rows'])

    def detail(self):
        if not self.app.connected or self.app.busy or not self.tree.selection():
            return
        identity = self.rows[self.tree.selection()[0]]['id']
        kind = self.kind
        self.app.request(self, lambda: self.app.repository.detail(kind, identity),
                         lambda result: self.show_detail(result, identity))

    def show_detail(self, result, identity):
        lines = []
        for title, rows, attributes in result['sections']:
            lines.append(f'【{title}】')
            if not rows:
                lines.append('无记录')
            for row in rows:
                if attributes:
                    lines.append(f"{display(row['field_key'])}：{display(row['field_value'])}")
                else:
                    lines.extend(f'{key}：{display(value)}' for key, value in row.items())
                lines.append('')
            lines.append('')
        window = tk.Toplevel(self)
        window.title(f'{result["sections"][0][0]} #{identity} · 详细资料')
        window.geometry('660x560')
        text = tk.Text(window, wrap='word', padx=12, pady=12)
        text.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(window, command=text.yview)
        scroll.pack(side='right', fill='y')
        text.configure(yscrollcommand=scroll.set)
        text.insert('1.0', '\n'.join(lines))
        text.configure(state='disabled')


class CharacterPanel(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app
        self.kind = 'characters'
        self.scope = None
        self.loaded = False
        self.generation = 0
        self.keyword = tk.StringVar()
        self.info = tk.StringVar(value='请选择账户和角色。')
        self.rows = {}
        self.current = None
        self.fields = {}
        ttk.Label(self, textvariable=self.info).pack(anchor='w', pady=(0, 12))
        form = ttk.Frame(self)
        form.pack(fill='x')
        fields = (('name', '角色名称'), ('username', '账号'), ('level', '等级'),
                  ('gold', '金币'), ('bank_gold', '银行金币'), ('hp', '生命'),
                  ('map_id', '地图'), ('x', '坐标 X'), ('y', '坐标 Y'),
                  ('revision', '存档版本'), ('saved_at', '存档时间'))
        for index, (key, label) in enumerate(fields):
            row, column = divmod(index, 2)
            value = tk.StringVar(value='—')
            self.fields[key] = value
            ttk.Label(form, text=label).grid(row=row, column=column * 2, sticky='w', padx=(0, 12), pady=9)
            ttk.Label(form, textvariable=value).grid(row=row, column=column * 2 + 1, sticky='w', padx=(0, 28))
        self.refresh = ttk.Button(self, text='刷新角色资料', command=self.search)
        self.refresh.pack(side='left', anchor='n', pady=14)
        self.set_enabled(False)

    def set_enabled(self, enabled):
        self.refresh.configure(state='normal' if enabled else 'disabled')

    def clear_results(self):
        self.generation += 1
        self.loaded = False
        self.rows.clear()
        self.current = None
        for variable in self.fields.values():
            variable.set('—')

    def search(self):
        if self.scope and self.app.connected and not self.app.busy:
            scope = self.scope
            self.app.request(self, lambda: self.app.repository.page('characters', '', scope=scope), self.show_page)

    def show_page(self, result):
        self.loaded = True
        self.app.show_roles(result['rows'])

    def show_character(self, row):
        self.current = row
        for key, variable in self.fields.items():
            variable.set(display(row.get(key)))
        self.info.set('角色资料 · 只读')
