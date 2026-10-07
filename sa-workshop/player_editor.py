from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from account_editor import AccountEditor
from catalog import CHARACTER_FIELDS, CHARACTER_STATS, character_points, character_value, display
from connection import ConnectionFailure, QueryFailure


class PlayerEditor(AccountEditor):
    def __init__(self, page, data):
        tk.Toplevel.__init__(self, page)
        self.page = page
        self.app = page.app
        self.generation = 0
        self.loaded = True
        self.saving = False
        self.original = data
        self.info = tk.StringVar()
        title = {'characters': '角色', 'pets': '宠物', 'items': '物品'}[data['kind']]
        self.title(f'{title} #{data["id"]}')
        self.transient(self.app)
        self.resizable(False, False)
        body = ttk.Frame(self, padding=16)
        body.pack(fill='both', expand=True)
        self.heading = tk.StringVar()
        ttk.Label(body, textvariable=self.heading).pack(anchor='w', pady=(0, 10))
        ttk.Label(body, text='四项属性按游戏点数填写；增加消耗剩余点数，减少返还点数。').pack(anchor='w', pady=(0, 8))
        listing = ttk.Frame(body)
        listing.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(listing, width=520, height=420, highlightthickness=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        scrollbar = ttk.Scrollbar(listing, orient='vertical', command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.form = ttk.Frame(self.canvas)
        window = self.canvas.create_window((0, 0), window=self.form, anchor='nw')
        self.form.bind('<Configure>', lambda event: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda event: self.canvas.itemconfigure(window, width=event.width))
        self.bind('<MouseWheel>', self.scroll)
        self.form.columnconfigure(1, weight=1)
        ttk.Label(body, textvariable=self.info, wraplength=520).pack(anchor='w', pady=10)
        actions = ttk.Frame(body)
        actions.pack(fill='x')
        self.save_button = ttk.Button(actions, text='保存', command=self.save)
        self.save_button.pack(side='left', padx=(12, 0))
        self.close_button = ttk.Button(actions, text='关闭', command=self.close)
        self.close_button.pack(side='left', padx=6)
        self.render(data)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.grab_set()

    def render(self, data):
        self.original = data
        for widget in self.form.winfo_children():
            widget.destroy()
        self.values = {}
        self.field_values = {}
        self.entries = []
        self.points_value = None
        character = data['character']
        self.heading.set(f'角色：{display(character["name"])}')
        labels = dict(field for group in CHARACTER_FIELDS for field in group)
        for index, row in enumerate(data['attributes']):
            ttk.Label(self.form, text=labels[row['field_key']]).grid(row=index, column=0, sticky='w', padx=(0, 16), pady=5)
            value = tk.StringVar(value=character_value(row))
            self.field_values[row['ordinal']] = value
            if row['field_key'] == b'skup':
                self.points_value = value
            editable = self.app.repository.attribute_editable(data['kind'], row)
            if isinstance(row['field_value'], bytes):
                try:
                    row['field_value'].decode('gbk')
                except UnicodeDecodeError:
                    editable = False
            entry = ttk.Entry(self.form, textvariable=value, width=48, state='normal' if editable else 'readonly')
            entry.grid(row=index, column=1, sticky='ew')
            self.entries.append((entry, editable))
            if editable:
                self.values[row['ordinal']] = value
        for row in data['attributes']:
            if row['field_key'] in CHARACTER_STATS:
                variable = self.values.get(row['ordinal'])
                if variable is not None:
                    variable.trace_add('write', self.update_points)
        self.finish()

    def update_points(self, *args):
        try:
            values = {ordinal: variable.get() for ordinal, variable in self.values.items()}
            row, remaining = character_points(self.original, values)
            if self.points_value is not None:
                self.points_value.set(str(remaining))
            self.info.set('')
        except (ValueError, TypeError) as exc:
            if self.points_value is not None:
                self.points_value.set('无法分配')
            self.info.set(str(exc))

    def changed(self):
        rows = {row['ordinal']: row for row in self.original['attributes']}
        return any(variable.get() != character_value(rows[ordinal]) for ordinal, variable in self.values.items())

    def begin(self):
        self.saving = True
        for entry, editable in self.entries:
            entry.configure(state='disabled')
        for button in (self.save_button, self.close_button):
            button.configure(state='disabled')

    def finish(self):
        self.saving = False
        for entry, editable in self.entries:
            entry.configure(state='normal' if editable else 'readonly')
        self.save_button.configure(state='normal' if self.values else 'disabled')
        self.close_button.configure(state='normal')

    def scroll(self, event):
        if self.form.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(event.delta / 120), 'units')
        return 'break'

    def save(self):
        if self.saving or self.app.busy or not self.app.connected:
            return
        if not self.changed():
            self.info.set('没有修改。')
            return
        values = {ordinal: variable.get() for ordinal, variable in self.values.items()}
        original = self.original
        self.begin()
        self.info.set('正在保存…')
        self.app.request(self, lambda: self.app.repository.save_player(original, values),
                         self.saved, on_error=self.failed)

    def saved(self, data):
        self.render(data)
        self.info.set('已保存。')
        self.app.after_idle(self.app.pages[1].search)

    def failed(self, error):
        if not self.winfo_exists():
            return
        self.finish()
        self.info.set(str(error) if isinstance(error, (ConnectionFailure, QueryFailure, ValueError)) else '操作失败，请重新打开表单确认状态。')
