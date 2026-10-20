from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from catalog import display
from connection import ConnectionFailure, QueryFailure
from template_editor import TemplateEditor


class BlackShopEditor(TemplateEditor):
    def __init__(self, page, data):
        tk.Toplevel.__init__(self, page)
        self.page, self.app = page, page.app
        self.original = data['original']
        self.generation = 0
        self.loaded = True
        self.saving = False
        self.info = tk.StringVar()
        self.skill_popup = None
        self.skill_box = None
        self.skill_labels = {str(row['id']): f'{row["id"]} · {display(row["name"])} · 金币 {row["cost"]}'
                             for row in data['items']}
        self.skill_values = {label: identity for identity, label in self.skill_labels.items()}
        values = self.original or dict(item_id='', enabled=1,
                                       cost=None, pearl=1, stock=-1)
        self.title('黑市商品' if self.original else '新增黑市商品')
        self.transient(self.app)
        form = ttk.Frame(self, padding=16)
        form.pack(fill='both', expand=True)
        self.fields = {}
        labels = (('item_id', '道具基板'), ('cost', '金币价格'),
                  ('pearl', 'P 价格'), ('stock', '库存'), ('enabled', '启用'))
        for index, (key, label) in enumerate(labels):
            ttk.Label(form, text=label).grid(row=index, column=0, sticky='w', padx=(0, 12), pady=6)
            variable = tk.StringVar(value=display(values[key]))
            if key == 'item_id':
                variable.set(self.skill_labels.get(str(values[key]), str(values[key])))
                widget = ttk.Combobox(form, textvariable=variable, width=48,
                                      values=tuple(self.skill_labels.values()))
                variable.trace_add('write', lambda *args, box=widget, text=variable:
                                   self.filter_skills(box, text.get()))
                widget.bind('<Down>', lambda event, box=widget: self.focus_skills(box))
                widget.bind('<Escape>', self.hide_skills)
                widget.bind('<Button-1>', self.hide_skills)
                widget.bind('<FocusOut>', lambda event: self.after_idle(self.leave_skills))
                if self.original:
                    widget.configure(state='disabled')
            elif key == 'enabled':
                widget = ttk.Combobox(form, textvariable=variable, values=('0', '1'), state='readonly')
            else:
                widget = ttk.Entry(form, textvariable=variable)
            widget.grid(row=index, column=1, sticky='ew', pady=6)
            self.fields[key] = (variable, widget)
        ttk.Label(form, text='金币：NULL / -1 使用基板价格；P：NULL / -1 禁用，0 为免费。\n'
                            '可任选金币或 P 支付；P 使用会员积分。库存 -1 为不限量。',
                  wraplength=460).grid(row=5, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Label(form, textvariable=self.info, wraplength=460).grid(row=6, column=0, columnspan=2, sticky='w')
        actions = ttk.Frame(form)
        actions.grid(row=7, column=0, columnspan=2, sticky='e', pady=(12, 0))
        self.save_button = ttk.Button(actions, text='保存', command=self.save)
        self.save_button.pack(side='left')
        self.close_button = ttk.Button(actions, text='关闭', command=self.close)
        self.close_button.pack(side='left', padx=(8, 0))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.app.center_window(self)

    def save(self):
        if self.saving or self.app.busy or not self.app.connected:
            return
        values = {key: variable.get() for key, (variable, widget) in self.fields.items()}
        values['item_id'] = self.skill_values.get(values['item_id'], values['item_id'])
        self.hide_skills()
        self.saving = True
        for variable, widget in self.fields.values():
            widget.configure(state='disabled')
        self.save_button.configure(state='disabled')
        self.close_button.configure(state='disabled')
        self.info.set('正在保存…')
        self.app.request(self, lambda: self.app.repository.save_black_shop(self.original, values),
                         self.saved, on_error=self.failed)

    def saved(self, data):
        self.original = data
        self.finish()
        self.info.set('已保存。')
        self.app.after_idle(lambda: self.page.load(refresh=True))
        self.close()

    def failed(self, error):
        if self.winfo_exists():
            self.finish()
            self.info.set(str(error) if isinstance(error, (ConnectionFailure, QueryFailure, ValueError))
                          else '保存失败，请重新读取商品确认状态。')

    def finish(self):
        self.saving = False
        for key, (variable, widget) in self.fields.items():
            widget.configure(state='disabled' if key == 'item_id' and self.original else
                             'readonly' if key == 'enabled' else 'normal')
        self.save_button.configure(state='normal')
        self.close_button.configure(state='normal')
