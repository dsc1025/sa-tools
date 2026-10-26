from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from catalog import CATALOGS, ENEMY_GROUP_FIELDS, ENCOUNTER_AREA_FIELDS, ITEM_TEMPLATE_FIELDS, cell, display
from connection import ConnectionFailure, QueryFailure
from cache import CACHED_KINDS
from account_editor import AccountEditor
from player_editor import PlayerEditor
from template_editor import TemplateEditor
from black_shop_editor import BlackShopEditor


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
        self.sort_key = None
        self.sort_descending = False
        self.loaded = False
        self.generation = 0
        self.keyword = tk.StringVar()
        self.info = tk.StringVar(value='请先在设置中连接数据库。')
        self.mode = tk.StringVar(value=modes[0][0])
        self.buttons = []
        self.content = ttk.Frame(self)
        self.content.pack(fill='both', expand=True)
        bar = ttk.Frame(self.content)
        if refresh_parent is None and self.kind not in ('pets', 'items'):
            bar.pack(fill='x', pady=(0, 10))
        self.mode_box = ttk.Combobox(bar, values=[label for label, kind in modes],
                                     textvariable=self.mode, state='readonly', width=12)
        if len(modes) > 1:
            self.mode_box.pack(side='left', padx=(0, 10))
        self.mode_box.bind('<<ComboboxSelected>>', self.change_mode)
        pet_page = self.kind in ('pet_templates', 'enemy_templates')
        pet_toolbar = self.kind in ('pet_templates', 'enemy_templates', 'enemy_groups',
                                    'encounter_areas', 'pet_capture_requirements', 'item_templates',
                                    'black_shop_items')
        self.search_entry = ttk.Entry(bar, textvariable=self.keyword)
        if pet_toolbar:
            self.search_entry.configure(width=24)
            self.search_entry.pack(side='left')
        elif not scoped:
            self.search_entry.pack(side='left', fill='x', expand=True)
        self.search_entry.bind('<Return>', lambda event: self.search())
        actions = (('刷新', self.search),) if scoped else (('搜索 / 刷新', self.search), ('清除筛选', self.clear_filter))
        if self.kind in CACHED_KINDS:
            actions = (('搜索', self.search), ('清除筛选', self.clear_filter),
                       ('同步数据库', lambda: self.load(refresh=True)))
        if pet_toolbar:
            actions = (('搜索', self.search), ('新增', self.new_template if pet_page else None),
                       ('删除', self.delete_template if pet_page else None),
                       ('同步数据库', lambda: self.load(refresh=True)))
        if self.kind == 'black_shop_items':
            actions = (('搜索', self.search), ('新增', self.new_black_shop),
                       ('删除', self.delete_black_shop),
                       ('同步数据库', lambda: self.load(refresh=True)))
        if self.kind in ('pets', 'items'):
            actions = ()
        for label, action in actions:
            button = ttk.Button(refresh_parent if refresh_parent is not None else bar,
                                text='刷新角色' if refresh_parent is not None else label, command=action)
            if pet_toolbar:
                button.configure(width=10)
            button.pack(side='right' if pet_toolbar and label == '同步数据库' else 'left',
                        padx=(8 if pet_toolbar else 6, 0))
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
        if self.kind == 'pet_templates':
            self.tree.bind('<Button-3>', self.template_menu)
        elif self.kind == 'enemy_templates':
            self.tree.bind('<Button-3>', self.enemy_menu)
        elif self.kind == 'enemy_groups':
            self.tree.bind('<Button-3>', self.group_menu)
        ttk.Label(self.content, textvariable=self.info).pack(anchor='w', pady=(6, 0))
        self.configure_columns()
        self.set_enabled(False)

    def new_template(self):
        if not self.app.connected or self.app.busy:
            return
        if self.kind == 'enemy_templates':
            self.app.request(self, self.app.repository.new_enemy_template_form,
                             lambda result: self.show_instance_form(result, None, creating=True))
        else:
            self.app.request(self, self.app.repository.new_pet_template_form,
                             lambda result: TemplateEditor(self, result, creating=True))

    def new_black_shop(self):
        if self.app.connected and not self.app.busy:
            self.app.request(self, self.app.repository.black_shop_form,
                             lambda result: BlackShopEditor(self, result))

    def delete_black_shop(self):
        if not self.app.connected or self.app.busy or not self.tree.selection():
            return
        identity = self.rows[self.tree.selection()[0]]['id']

        def confirm(data):
            if messagebox.askyesno('删除商品', f'确定从黑市删除商品 #{identity}？', parent=self):
                self.app.after_idle(lambda: self.app.request(
                    self, lambda: self.app.repository.delete_black_shop(data['original']),
                    lambda result: self.app.after_idle(lambda: self.load(refresh=True))))

        self.app.request(self, lambda: self.app.repository.black_shop_form(identity), confirm)

    def delete_template(self):
        if not self.app.connected or self.app.busy:
            return
        enemy = self.kind == 'enemy_templates'
        title = '实例' if enemy else '基板'
        selection = self.tree.selection()
        if not selection or selection[0] not in self.rows:
            self.info.set(f'请先选择要删除的{title}。')
            return
        identity = self.rows[selection[0]]['id']

        def failed(error):
            if isinstance(error, ValueError):
                messagebox.showwarning(f'不能删除{title}', str(error), parent=self)

        def confirm(result):
            original = result['sections'][0][1][0] if enemy else result['template']
            delete_record = (self.app.repository.delete_enemy_template if enemy else
                             self.app.repository.delete_pet_template)
            if messagebox.askyesno(f'删除{title}',
                                   f'确定删除{title} #{identity}（{display(original["name"])}）？',
                                   parent=self):
                self.app.request(self, lambda: delete_record(original),
                                 lambda result: self.app.after_idle(lambda: self.load(refresh=True)),
                                 on_error=failed)

        read_record = (self.app.repository.enemy_template_form if enemy else
                       self.app.repository.pet_template_delete_form)
        self.app.request(self, lambda: read_record(identity),
                         confirm, on_error=failed)

    def template_menu(self, event):
        if not self.app.connected or self.app.busy:
            return
        identity = self.tree.identify_row(event.y)
        if not identity or identity not in self.rows:
            return
        self.tree.selection_set(identity)
        self.tree.focus(identity)
        template = self.rows[identity]['id']
        self.app.request(self, lambda: self.app.repository.template_enemies(template),
                         lambda rows: self.show_template_menu(rows, event.x_root, event.y_root))

    def enemy_menu(self, event):
        if not self.app.connected or self.app.busy:
            return
        identity = self.tree.identify_row(event.y)
        if not identity or identity not in self.rows:
            return
        self.tree.selection_set(identity)
        self.tree.focus(identity)
        enemy = self.rows[identity]['id']
        self.app.request(self, lambda: self.app.repository.enemy_groups(enemy),
                         lambda rows: self.show_template_menu(rows, event.x_root, event.y_root,
                                                              kind='enemy_groups'))

    def group_menu(self, event):
        if not self.app.connected or self.app.busy:
            return
        identity = self.tree.identify_row(event.y)
        if not identity or identity not in self.rows:
            return
        self.tree.selection_set(identity)
        self.tree.focus(identity)
        group = self.rows[identity]['id']
        self.app.request(self, lambda: self.app.repository.group_areas(group),
                         lambda rows: self.show_template_menu(rows, event.x_root, event.y_root,
                                                              kind='encounter_areas'))

    def show_template_menu(self, rows, x, y, kind='enemy_templates'):
        if hasattr(self, 'pet_menu'):
            self.pet_menu.destroy()
        menu = self.pet_menu = tk.Menu(self, tearoff=False)
        for row in rows:
            if kind == 'encounter_areas':
                label = f'{row["area_id"]} · {display(row["map_name"])}[{row["floor"]}]'
            else:
                label = f'{row["id"]} · {display(row["name"])}'
                if kind == 'enemy_templates':
                    label += f' · lv{row["lv_min"]}~{row["lv_max"]}'
            menu.add_command(label=label,
                             command=lambda identity=row['id']: self.locate_enemy(identity, kind))
        if not rows:
            title = {'enemy_templates': '实例', 'enemy_groups': '组合', 'encounter_areas': '区域'}[kind]
            menu.add_command(label=f'无对应{title}', state='disabled')
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def locate_enemy(self, identity, kind='enemy_templates'):
        if not self.app.connected or self.app.busy:
            return
        page = next(page for page in self.app.pet_pages if page.kind == kind)
        page.keyword.set('')
        page.scope = None

        def show(result):
            page.show_page(result)
            self.app.pet_tabs.select(page)
            key = str(identity)
            if key in page.rows:
                page.tree.selection_set(key)
                page.tree.focus(key)
                page.tree.see(key)
                page.tree.focus_set()
            else:
                title = {'enemy_templates': '实例', 'enemy_groups': '组合', 'encounter_areas': '区域'}[kind]
                page.info.set(f'{title} #{identity} 已不存在，请重新打开右键菜单。')

        self.app.request(page, lambda: self.app.repository.page(kind, '', page=None,
                                                               refresh=True), show)

    def configure_columns(self):
        columns = CATALOGS[self.kind].columns
        self.tree.configure(columns=[key for key, label in columns])
        for key, label in columns:
            self.tree.heading(key, text=label, anchor='w')
            if self.kind in ('pet_templates', 'enemy_templates', 'item_templates'):
                self.tree.heading(key, command=lambda column=key: self.sort_rows(column))
            self.tree.column(key, width=130, minwidth=20, stretch=True, anchor='w')

    def sort_rows(self, key, toggle=True):
        if self.kind not in ('pet_templates', 'enemy_templates', 'item_templates'):
            return
        if toggle:
            self.sort_descending = not self.sort_descending if self.sort_key == key else False
            self.sort_key = key
        present, missing = [], []
        for identity in self.tree.get_children():
            value = self.rows[identity].get(key)
            if value is None:
                missing.append(identity)
            else:
                value = display(value) if key in ('name', 'base_name', 'usefunc') else int(value)
                present.append((value, identity))
        present.sort(key=lambda entry: entry[0], reverse=self.sort_descending)
        for index, identity in enumerate([identity for value, identity in present] + missing):
            self.tree.move(identity, '', index)
        for column, label in CATALOGS[self.kind].columns:
            suffix = (' ▼' if self.sort_descending else ' ▲') if column == key else ''
            self.tree.heading(column, text=label + suffix)

    def set_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for button in self.buttons:
            unavailable = (self.kind in ('enemy_groups', 'encounter_areas',
                                        'pet_capture_requirements', 'item_templates')
                           and button.cget('text') in ('新增', '删除'))
            button.configure(state='disabled' if unavailable else state)
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
        if self.sort_key is not None:
            self.sort_rows(self.sort_key, toggle=False)
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
        if kind == 'black_shop_items':
            self.app.request(self, lambda: self.app.repository.black_shop_form(identity),
                             lambda result: BlackShopEditor(self, result))
            return
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
        if kind == 'enemy_groups':
            self.app.request(self, lambda: self.app.repository.enemy_group_form(identity),
                             lambda result: self.show_instance_form(result, identity, group=True))
            return
        if kind == 'encounter_areas':
            self.app.request(self, lambda: self.app.repository.encounter_area_form(identity),
                             lambda result: self.show_instance_form(result, identity, area=True))
            return
        self.app.request(self, lambda: self.app.repository.detail(kind, identity),
                         lambda result: self.show_detail(result, identity))

    def show_instance_form(self, result, identity, group=False, area=False, creating=False):
        original = result['sections'][0][1][0]
        editable = (tuple(key for key in ENEMY_GROUP_FIELDS if key in original) if group else
                    ('tempno', 'name', 'lv_min', 'lv_max', 'createminnum', 'createmaxnum',
                     'item1', 'item2', 'itemprob1', 'itemprob2'))
        save_record = (self.app.repository.save_enemy_group if group else
                       self.app.repository.save_enemy_template)
        if area:
            editable = tuple(key for key in ENCOUNTER_AREA_FIELDS if key in original)
            save_record = self.app.repository.save_encounter_area
        fields = {}
        saving = False
        window = tk.Toplevel(self)
        window.generation = 0
        window.loaded = True
        window.info = tk.StringVar(value='')
        window.title(f'遇敌区域 #{identity}' if area else f'宠物实例{"组合" if group else ""} #{identity}')
        if creating:
            window.title('新增宠物实例')
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
        if group:
            meanings = {'source_order': '记录加载顺序。', 'id': '组合编号。',
                        **{key: definition[0] for key, definition in ENEMY_GROUP_FIELDS.items()}}
        if area:
            meanings = {'source_order': '记录加载顺序。',
                        'map_name': '地图名称。',
                        **{key: definition[0] for key, definition in ENCOUNTER_AREA_FIELDS.items()}}
        row_index = 0
        for title, rows, attributes in result['sections']:
            ttk.Label(form, text=title).grid(row=row_index, column=0, columnspan=3,
                                            sticky='w', pady=(0, 8))
            row_index += 1
            if not rows:
                ttk.Label(form, text='无记录').grid(row=row_index, column=0, sticky='w')
                row_index += 1
            for row in rows:
                if area:
                    area_row = {}
                    for key, value in row.items():
                        area_row[key] = value
                        if key == 'floor':
                            area_row['map_name'] = result.get('map_name')
                    row = area_row
                for key, value in row.items():
                    ttk.Label(form, text=key).grid(row=row_index, column=0, sticky='w',
                                                   padx=(0, 12), pady=5)
                    entry = ttk.Entry(form, width=14)
                    entry.insert(0, '自动分配' if creating and key == 'id' else display(value))
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
            nonlocal original, creating
            if creating:
                creating = False
                window.title(f'宠物实例 #{data["id"]}')
                for key in ('id', 'source_order'):
                    entry = fields[key]
                    entry.configure(state='normal')
                    entry.delete(0, 'end')
                    entry.insert(0, display(data[key]))
            if area:
                entry = fields['map_name']
                entry.configure(state='normal')
                entry.delete(0, 'end')
                entry.insert(0, display(data['map_name']))
                data = data['area']
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
            self.app.request(window, lambda: save_record(original, values, creating=True)
                             if creating else save_record(original, values),
                             saved, on_error=failed)

        ttk.Label(actions, textvariable=window.info, wraplength=450).pack(side='left')
        close_button = ttk.Button(actions, text='关闭', command=close)
        close_button.pack(side='right')
        save_button = ttk.Button(actions, text='保存', command=save)
        save_button.pack(side='right', padx=8)
        window.protocol('WM_DELETE_WINDOW', close)

    def show_item_form(self, result, identity):
        original = result['sections'][0][1][0] if result['sections'][0][1] else None
        fields = {}
        saving = False
        adapters = {'pet': '宠技', 'magic': '魔法', 'profession': '职业技能'}
        skill_labels = {str(row['skill_id']):
                        f'{row["skill_id"]} · {display(row["name"])} · '
                        f'{adapters.get(row["adapter"], row["adapter"])}'
                        + (' · 执行函数未注册' if not row['function_registered'] else '')
                        for row in result['skills']}
        skill_labels.update({'0': '0 · 无技能', 'NULL': 'NULL · 使用旧配置'})
        skill_values = {label: identity for identity, label in skill_labels.items()}
        skill_popup = None
        skill_box = None
        meanings = {
            'id': '道具模板编号。',
            'name': '道具名称。', 'secretname': '道具隐藏名称。',
            'effectstring': '道具效果说明。', 'argument': '道具功能参数；技能罐头仅在 skill_id 为 NULL 时按旧宠技编号读取。',
            'acode': '道具类型编码。', 'inlaycode': '镶嵌编码。',
            'initfunc': '初始化处理函数。', 'preoverfunc': '重叠前处理函数。',
            'postoverfunc': '重叠后处理函数。', 'watchfunc': '查看处理函数。',
            'usefunc': '使用处理函数。', 'attachfunc': '装备处理函数。',
            'detachfunc': '卸下处理函数。', 'dropfunc': '丢弃处理函数。',
            'pickupfunc': '拾取处理函数。', 'relifefunc': '死亡重生处理函数。',
            'imagenumber': '道具图像编号。', 'cost': '道具价格配置值。',
            'type': '道具类型配置值。', 'fieldtype': '允许使用的场景配置值。',
            'target': '使用目标配置值。', 'level': '道具等级配置值。',
            'dambreak': '使用损耗配置值。', 'upinums': '叠加使用数量配置值。',
            'campile': '允许叠加配置值。', 'nestr': '所需腕力。',
            'nedex': '所需速度。', 'netra': '所需转生次数。',
            'neprof': '所需职业配置值。', 'damcrushe': '耐久损耗配置值。',
            'maxdmce': '最大耐久配置值。', 'otdmags': '附加伤害配置值。',
            'otdefcs': '附加防御配置值。', 'nsuit': '套装编码。',
            'attrib': '属性类型配置值。', 'attribvalue': '属性数值。',
            'magicid': '旧魔法编号；非技能罐头且 skill_id 为 NULL 时使用。', 'magicprob': '魔法触发概率原始值。',
            'magicusemp': '魔法消耗魔力。', 'arr': '排列修正配置值。',
            'seqce': '行动顺序修正配置值。', 'iapi': '装备叠加配置值。',
            'hirt': '命中修正配置值。', 'neguard': '忽略防御配置值。',
            'useaction': '使用动作编号。', 'dropatlogout': '登出时丢弃标记。',
            'vanishatdrop': '丢弃时消失标记。', 'isovered': '允许重叠标记。',
            'canpetmail': '允许宠物邮件传送标记。',
            'canmergefrom': '允许作为合成材料标记。', 'canmergeto': '允许作为合成结果标记。',
            'perception': '装备隐藏感知加成。',
            'skill_id': '统一技能编号，可引用宠技、魔法或职业技能；0 为无技能，NULL 使用旧配置。技能罐头用于学习，其余道具用于施放。',
        }
        for key, label in (('attacknum', '攻击次数'), ('attack', '攻击修正'),
                           ('defence', '防御修正'), ('quick', '速度修正'),
                           ('hp', '生命修正'), ('mp', '魔力修正'),
                           ('luck', '运气修正'), ('charm', '魅力修正'),
                           ('avoid', '闪避修正'), ('poison', '毒相关参数'),
                           ('paralysis', '麻痹相关参数'), ('sleep', '睡眠相关参数'),
                           ('stone', '石化相关参数'), ('drunk', '酒醉相关参数'),
                           ('confusion', '混乱相关参数'), ('critical', '会心相关参数')):
            meanings[f'{key}_min'] = f'{label}下限。'
            meanings[f'{key}_max'] = f'{label}上限。'
        for index in range(5):
            meanings[f'ingname{index}'] = f'第 {index + 1} 个成分名称。'
            meanings[f'ingvalue{index}'] = f'第 {index + 1} 个成分数值。'
        window = tk.Toplevel(self)
        window.title(f'道具详情 #{identity}')
        window.generation = 0
        window.info = tk.StringVar(value='可编辑属性上下限、耐久及统一技能。技能支持编号或名称搜索。')
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

        def hide_skills(event=None):
            if skill_popup is not None:
                skill_popup.withdraw()

        def leave_skills():
            if skill_popup is not None and window.focus_get() not in (skill_box, skill_list):
                hide_skills()

        def choose_skill(event=None):
            selection = skill_list.curselection()
            if selection:
                value = skill_list.get(selection[0])
                hide_skills()
                skill_box.set(value)
                skill_box.icursor('end')
                skill_box.focus_set()
            return 'break'

        def cancel_skills(event=None):
            hide_skills()
            skill_box.focus_set()
            return 'break'

        def focus_skills(event=None):
            if skill_popup is not None and skill_popup.winfo_viewable():
                skill_list.selection_set(0)
                skill_list.activate(0)
                skill_list.focus_set()
                return 'break'

        def filter_skills(*args):
            nonlocal skill_popup, skill_list
            text = skill_variable.get()
            keyword = text.strip().casefold()
            values = tuple(skill_labels.values())
            if text not in skill_values and keyword:
                values = tuple(label for label in values if keyword in label.casefold())
            skill_box.configure(values=values)
            hide_skills()
            if (not keyword or text in skill_values or not values
                    or saving or window.focus_get() is not skill_box):
                return
            if skill_popup is None:
                skill_popup = tk.Toplevel(window)
                skill_popup.withdraw()
                skill_popup.overrideredirect(True)
                skill_popup.transient(window)
                skill_list = tk.Listbox(skill_popup, exportselection=False)
                skill_list.pack(side='left', fill='both', expand=True)
                scrollbar = ttk.Scrollbar(skill_popup, command=skill_list.yview)
                scrollbar.pack(side='right', fill='y')
                skill_list.configure(yscrollcommand=scrollbar.set)
                skill_list.bind('<ButtonRelease-1>', choose_skill)
                skill_list.bind('<Return>', choose_skill)
                skill_list.bind('<Escape>', cancel_skills)
                skill_list.bind('<FocusOut>', lambda event: window.after_idle(leave_skills))
            skill_list.delete(0, 'end')
            skill_list.insert('end', *values)
            skill_list.configure(height=min(8, len(values)))
            skill_popup.update_idletasks()
            height = skill_popup.winfo_reqheight()
            width = max(skill_box.winfo_width(), skill_popup.winfo_reqwidth())
            x = min(skill_box.winfo_rootx(), max(0, window.winfo_screenwidth() - width))
            y = skill_box.winfo_rooty() + skill_box.winfo_height()
            if y + height > window.winfo_screenheight():
                y = max(0, skill_box.winfo_rooty() - height)
            skill_popup.geometry(f'{width}x{height}+{x}+{y}')
            skill_popup.deiconify()
            skill_popup.lift()

        skill_list = None
        row_index = 0
        for title, rows, attributes in result['sections']:
            ttk.Label(form, text=title).grid(row=row_index, column=0, columnspan=3,
                                            sticky='w', pady=(0, 8))
            row_index += 1
            if not rows:
                ttk.Label(form, text='无记录').grid(row=row_index, column=0, sticky='w')
                row_index += 1
            for row in rows:
                for key in (('id',) if 'id' in row else ()) + tuple(key for key in row if key != 'id'):
                    if key in ('source_order', 'pet_skill_id'):
                        continue
                    value = row[key]
                    ttk.Label(form, text='模板编号' if key == 'id' else key).grid(row=row_index, column=0, sticky='w',
                                                   padx=(0, 12), pady=5)
                    if key == 'skill_id':
                        skill_variable = tk.StringVar(value=skill_labels.get(display(value), display(value)))
                        entry = skill_box = ttk.Combobox(form, textvariable=skill_variable, width=36,
                                                        values=tuple(skill_labels.values()))
                        skill_variable.trace_add('write', filter_skills)
                        entry.bind('<Down>', focus_skills)
                        entry.bind('<Escape>', hide_skills)
                        entry.bind('<Button-1>', hide_skills)
                        entry.bind('<FocusOut>', lambda event: window.after_idle(leave_skills))
                    else:
                        entry = ttk.Entry(form, width=24)
                        entry.insert(0, display(value))
                    entry.configure(state='normal' if key in ITEM_TEMPLATE_FIELDS else 'readonly')
                    if key in ITEM_TEMPLATE_FIELDS:
                        fields[key] = entry
                    entry.grid(row=row_index, column=1, columnspan=2 if key == 'skill_id' else 1,
                               sticky='w', padx=(0, 12), pady=5)
                    if key == 'skill_id':
                        row_index += 1
                    ttk.Label(form, text=meanings.get(key, '原始字段，当前仅支持查看。'),
                              wraplength=260).grid(row=row_index, column=2, sticky='w', pady=5)
                    row_index += 1
        actions = ttk.Frame(window, padding=12)
        actions.pack(fill='x')

        def close():
            if not saving:
                hide_skills()
                window.generation += 1
                window.destroy()

        def finish():
            nonlocal saving
            saving = False
            for entry in fields.values():
                entry.configure(state='normal')
            save_button.configure(state='normal' if original is not None else 'disabled')
            close_button.configure(state='normal')

        def saved(data):
            nonlocal original
            original = data
            finish()
            for key, entry in fields.items():
                if key == 'skill_id':
                    entry.set(skill_labels.get(display(data[key]), display(data[key])))
                else:
                    entry.delete(0, 'end')
                    entry.insert(0, display(data[key]))
            window.info.set('已保存。生效时间取决于服务端模板重载机制。')
            self.app.after_idle(lambda: self.load(refresh=True))

        def failed(error):
            if not window.winfo_exists():
                return
            finish()
            window.info.set(str(error) if isinstance(error, (ConnectionFailure, QueryFailure, ValueError))
                            else '保存失败，请关闭表单并重新读取确认状态。')

        def save():
            nonlocal saving
            if saving or self.app.busy or not self.app.connected or original is None:
                return
            values = {key: entry.get() for key, entry in fields.items()}
            if 'skill_id' in values:
                text = values['skill_id'].strip()
                values['skill_id'] = skill_values.get(text, text)
            hide_skills()
            saving = True
            for entry in fields.values():
                entry.configure(state='disabled')
            save_button.configure(state='disabled')
            close_button.configure(state='disabled')
            window.info.set('正在保存…')
            self.app.request(window, lambda: self.app.repository.save_item_template(original, values),
                             saved, on_error=failed)

        ttk.Label(actions, textvariable=window.info, wraplength=430).pack(side='left')
        close_button = ttk.Button(actions, text='关闭', command=close)
        close_button.pack(side='right')
        save_button = ttk.Button(actions, text='保存', command=save,
                                 state='normal' if original is not None else 'disabled')
        save_button.pack(side='right', padx=8)
        window.protocol('WM_DELETE_WINDOW', close)

    def show_detail(self, result, identity):
        if self.kind == 'item_templates':
            self.show_item_form(result, identity)
            return
        if self.kind in ('enemy_templates', 'enemy_groups', 'encounter_areas'):
            self.show_instance_form(result, identity, group=self.kind == 'enemy_groups',
                                    area=self.kind == 'encounter_areas')
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
