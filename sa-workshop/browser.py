from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from catalog import CATALOGS, cell, display
from connection import ConnectionFailure, QueryFailure
from cache import CACHED_KINDS
from account_editor import AccountEditor
from player_editor import PlayerEditor
from template_editor import TemplateEditor


class Browser(ttk.Frame):
    def __init__(self, master, app, modes, scoped=False, refresh_parent=None):
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
        if refresh_parent is None:
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
        if self.kind in CACHED_KINDS:
            actions = (('搜索', self.search), ('清除筛选', self.clear_filter),
                       ('更新本地缓存', lambda: self.load(refresh=True)))
        for label, action in actions:
            button = ttk.Button(refresh_parent if refresh_parent is not None else bar,
                                text='刷新角色' if refresh_parent is not None else label, command=action)
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

    def load(self, refresh=False):
        if not self.app.connected or self.app.busy or (self.scoped and self.scope is None):
            return
        kind, keyword, scope = self.kind, '' if self.scoped else self.keyword.get(), self.scope
        self.info.set('正在查询…')
        self.app.request(self, lambda: self.app.repository.page(kind, keyword, page=None, scope=scope,
                                                                refresh=refresh), self.show_page)

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
        cached = ' · 优先使用本地缓存，数据变更后请更新缓存' if self.kind in CACHED_KINDS else ''
        self.info.set(f'共 {self.total} 条{scope}{cached}')
        if self.kind == 'characters':
            self.app.show_roles(result['rows'])
        elif self.kind == 'accounts' and str(self.app.selected_account) in self.rows:
            self.tree.selection_set(str(self.app.selected_account))

    def detail(self):
        if not self.app.connected or self.app.busy or not self.tree.selection():
            return
        identity = self.rows[self.tree.selection()[0]]['id']
        kind = self.kind
        if kind == 'accounts':
            self.app.request(self, lambda: self.app.repository.account_form(identity),
                             lambda result: AccountEditor(self, result))
            return
        if kind == 'characters':
            self.app.request(self, lambda: self.app.repository.player_form(kind, identity),
                             lambda result: PlayerEditor(self, result))
            return
        if kind == 'pet_templates':
            self.app.request(self, lambda: self.app.repository.pet_template_form(identity),
                             lambda result: TemplateEditor(self, result))
            return
        if kind == 'enemy_templates':
            self.app.request(self, lambda: self.app.repository.enemy_template_form(identity),
                             lambda result: self.show_instance_form(result, identity))
            return
        self.app.request(self, lambda: self.app.repository.detail(kind, identity),
                         lambda result: self.show_detail(result, identity))

    def show_instance_form(self, result, identity):
        original = result['sections'][0][1][0]
        editable = ('name', 'lv_min', 'lv_max', 'createminnum', 'createmaxnum')
        fields = {}
        saving = False
        window = tk.Toplevel(self)
        window.generation = 0
        window.loaded = True
        window.info = tk.StringVar(value='')
        window.title(f'宠物实例 #{identity}')
        window.transient(self.app)
        self.app.center_window(window, 660, 560)
        content = ttk.Frame(window)
        content.pack(fill='both', expand=True)
        canvas = tk.Canvas(content, highlightthickness=0)
        canvas.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(content, orient='vertical', command=canvas.yview)
        scroll.pack(side='right', fill='y')
        canvas.configure(yscrollcommand=scroll.set)
        form = ttk.Frame(canvas, padding=12)
        form_id = canvas.create_window((0, 0), window=form, anchor='nw')
        form.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(form_id, width=event.width))
        form.columnconfigure(2, weight=1)
        wheel_delta = 0

        def wheel(event):
            nonlocal wheel_delta
            if canvas.bbox('all') and canvas.bbox('all')[3] > canvas.winfo_height():
                wheel_delta += event.delta
                steps = int(wheel_delta / 120)
                wheel_delta -= steps * 120
                canvas.yview_scroll(-steps * 3, 'units')
            return 'break'

        window.bind('<MouseWheel>', wheel)
        meanings = {
            'source_order': '记录加载顺序。',
            'name': '实例名称，留空使用基板名称。',
            'tacticsoption': '战术选项配置，按原始格式显示。',
            'act_condition': '行动条件配置，按原始格式显示。',
            'id': '实例编号。',
            'tempno': '关联的宠物基板编号。',
            'lv_min': '最低等级，0表示使用最高等级。',
            'lv_max': '最高等级。',
            'createmaxnum': '最大生成数量。',
            'createminnum': '最小生成数量。',
            'tactics': '战术配置值。',
            'exp': '经验配置值；特殊值含义以服务端为准。',
            'duelpoint': '决斗积分配置值；特殊值含义以服务端为准。',
            'style': '样式配置值，具体含义以服务端为准。',
            'petflg': '实例宠物标记。',
        }
        meanings.update({f'item{index}': f'第 {index} 个道具编号。'
                         for index in range(1, 11)})
        meanings.update({f'itemprob{index}': f'第 {index} 个道具的概率配置原始值。'
                         for index in range(1, 11)})
        row_index = 0
        for title, rows, attributes in result['sections']:
            ttk.Label(form, text=title).grid(row=row_index, column=0, columnspan=3,
                                            sticky='w', pady=(0, 8))
            row_index += 1
            if not rows:
                ttk.Label(form, text='无记录').grid(row=row_index, column=0, sticky='w')
                row_index += 1
            for row in rows:
                for key, value in row.items():
                    ttk.Label(form, text=key).grid(row=row_index, column=0, sticky='w',
                                                   padx=(0, 12), pady=5)
                    entry = ttk.Entry(form, width=14)
                    entry.insert(0, display(value))
                    entry.configure(state='normal' if key in editable else 'disabled')
                    fields[key] = entry
                    entry.grid(row=row_index, column=1, sticky='w', padx=(0, 12), pady=5)
                    ttk.Label(form, text=meanings.get(key, '原始字段，当前仅支持查看。'),
                              wraplength=340).grid(row=row_index, column=2, sticky='w', pady=5)
                    row_index += 1
        actions = ttk.Frame(window, padding=12)
        actions.pack(fill='x')

        def close():
            if not saving:
                window.generation += 1
                window.destroy()

        def finish():
            nonlocal saving
            saving = False
            for key, entry in fields.items():
                entry.configure(state='normal' if key in editable else 'disabled')
            save_button.configure(state='normal')
            close_button.configure(state='normal')

        def saved(data):
            nonlocal original
            original = data
            for key in editable:
                entry = fields[key]
                entry.configure(state='normal')
                entry.delete(0, 'end')
                entry.insert(0, display(data[key]))
            finish()
            window.info.set('已保存。服务端加载后的生效时间取决于其模板重载机制。')
            self.app.after_idle(lambda: self.load(refresh=True))

        def failed(error):
            if not window.winfo_exists():
                return
            finish()
            window.info.set(str(error) if isinstance(error, (ConnectionFailure, QueryFailure, ValueError))
                            else '保存失败，请关闭表单并重新读取确认状态。')

        def save():
            nonlocal saving
            if saving or self.app.busy or not self.app.connected:
                return
            values = {key: fields[key].get() for key in editable}
            saving = True
            for entry in fields.values():
                entry.configure(state='disabled')
            save_button.configure(state='disabled')
            close_button.configure(state='disabled')
            window.info.set('正在保存…')
            self.app.request(window, lambda: self.app.repository.save_enemy_template(original, values),
                             saved, on_error=failed)

        ttk.Label(actions, textvariable=window.info, wraplength=450).pack(side='left')
        close_button = ttk.Button(actions, text='关闭', command=close)
        close_button.pack(side='right')
        save_button = ttk.Button(actions, text='保存', command=save)
        save_button.pack(side='right', padx=8)
        window.protocol('WM_DELETE_WINDOW', close)

    def show_detail(self, result, identity):
        if self.kind == 'enemy_templates':
            self.show_instance_form(result, identity)
            return
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
        self.app.center_window(window, 660, 560)
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
