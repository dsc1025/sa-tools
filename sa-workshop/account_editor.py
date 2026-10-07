from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from catalog import display
from connection import ConnectionFailure, QueryFailure


class AccountEditor(tk.Toplevel):
    def __init__(self, page, data):
        super().__init__(page)
        self.page = page
        self.app = page.app
        self.original = data
        self.generation = 0
        self.loaded = True
        self.saving = False
        self.info = tk.StringVar()
        self.title(f'账户 #{data["id"]}')
        self.transient(self.app)
        self.resizable(False, False)
        form = ttk.Frame(self, padding=16)
        form.pack(fill='both', expand=True)
        for row, (key, label) in enumerate((('id', '账户编号'), ('username', '账号'), ('created_at', '创建时间'))):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', padx=(0, 16), pady=6)
            ttk.Label(form, text=display(data[key])).grid(row=row, column=1, sticky='w')
        self.enabled = tk.StringVar(value=display(data['enabled']))
        self.gm_level = tk.StringVar(value=display(data['gm_level']))
        ttk.Label(form, text='启用状态').grid(row=3, column=0, sticky='w', pady=6)
        self.enabled_box = ttk.Combobox(form, textvariable=self.enabled, values=('0', '1'), state='readonly', width=28)
        self.enabled_box.grid(row=3, column=1, sticky='ew')
        ttk.Label(form, text='GM等级').grid(row=4, column=0, sticky='w', pady=6)
        self.gm_box = ttk.Combobox(form, textvariable=self.gm_level, state='readonly', width=28)
        self.gm_box.grid(row=4, column=1, sticky='ew')
        self.configure_levels()
        ttk.Label(form, textvariable=self.info, wraplength=400).grid(row=5, column=0, columnspan=2, sticky='w', pady=10)
        actions = ttk.Frame(form)
        actions.grid(row=6, column=0, columnspan=2, sticky='e')
        self.save_button = ttk.Button(actions, text='保存', command=self.save)
        self.save_button.pack(side='left')
        self.close_button = ttk.Button(actions, text='关闭', command=self.close)
        self.close_button.pack(side='left', padx=(8, 0))
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.grab_set()

    def configure_levels(self):
        levels = tuple(str(level) for level in range(5))
        self.gm_box.configure(values=('NULL', *levels) if self.original['gm_level'] is None else levels)

    def save(self):
        if self.app.busy or not self.app.connected or self.saving:
            return
        try:
            enabled = int(self.enabled.get())
            gm_level = None if self.gm_level.get() == 'NULL' else int(self.gm_level.get())
        except ValueError:
            self.info.set('字段值无效。')
            return
        if enabled == self.original['enabled'] and gm_level == self.original['gm_level']:
            self.info.set('没有修改。')
            return
        self.saving = True
        for widget in (self.enabled_box, self.gm_box, self.save_button, self.close_button):
            widget.configure(state='disabled')
        self.info.set('正在保存…')
        self.app.request(self, lambda: self.app.repository.save_account(self.original, enabled, gm_level),
                         self.saved, on_error=self.failed)

    def saved(self, data):
        self.original = data
        self.enabled.set(display(data['enabled']))
        self.gm_level.set(display(data['gm_level']))
        self.configure_levels()
        self.finish()
        self.info.set('已保存。')
        self.app.after_idle(self.page.search)

    def failed(self, error):
        if not self.winfo_exists():
            return
        self.finish()
        self.info.set(str(error) if isinstance(error, (ConnectionFailure, QueryFailure, ValueError)) else '保存失败，请重新读取账户确认状态。')

    def finish(self):
        self.saving = False
        self.enabled_box.configure(state='readonly')
        self.gm_box.configure(state='readonly')
        self.save_button.configure(state='normal')
        self.close_button.configure(state='normal')

    def close(self):
        if self.saving:
            return
        self.generation += 1
        self.destroy()
