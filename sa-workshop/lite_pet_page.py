from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from catalog import display
from template_editor import TemplateEditor


class LitePetPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app
        self.kind = 'lite_pet_selection'
        self.scope = None
        self.loaded = False
        self.generation = 0
        self.rows = {}
        self.info = tk.StringVar(value='请先在设置中连接数据库。')

        actions = ttk.Frame(self)
        actions.pack(fill='x', pady=(0, 8))
        self.add_button = ttk.Button(actions, text='加入名单', command=self.add_dialog)
        self.add_button.pack(side='left')
        self.remove_button = ttk.Button(actions, text='移出名单', command=lambda: self.change_selection(False))
        self.remove_button.pack(side='left', padx=(8, 0))

        listing = ttk.Frame(self)
        listing.pack(fill='both', expand=True)
        self.tree = ttk.Treeview(listing, columns=('tempno', 'name'),
                                 show='headings', selectmode='extended')
        for key, label in (('tempno', '基板编号'), ('name', '名称')):
            self.tree.heading(key, text=label, anchor='w')
            self.tree.column(key, width=130, minwidth=20, stretch=True, anchor='w')
        self.tree.grid(row=0, column=0, sticky='nsew')
        self.tree.bind('<Double-1>', self.edit_template)
        scrollbar = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scrollbar.set)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        ttk.Label(self, textvariable=self.info).pack(anchor='w', pady=(6, 0))
        self.set_enabled(False)

    def set_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for widget in (self.add_button, self.remove_button):
            widget.configure(state=state)

    def clear_results(self):
        self.generation += 1
        self.loaded = False
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()

    def search(self):
        if not self.app.connected or self.app.busy:
            return
        self.info.set('正在查询…')
        self.app.request(self, lambda: self.app.repository.lite_pet_list('', '', True),
                         self.show_rows)

    def load(self, refresh=False):
        self.search()

    def edit_template(self, event):
        if not self.app.connected or self.app.busy:
            return
        identity = self.tree.identify_row(event.y)
        if not identity or identity not in self.rows:
            return
        self.app.request(self, lambda: self.app.repository.pet_template_form(int(identity)),
                         lambda result: TemplateEditor(self, result))

    def show_rows(self, rows):
        self.clear_results()
        self.loaded = True
        for row in rows:
            identity = str(row['tempno'])
            self.rows[identity] = row
            self.tree.insert('', 'end', iid=identity,
                             values=(identity, display(row['name'])))
        self.info.set(f'共 {len(rows)} 条。')

    def add_dialog(self):
        if not self.app.connected or self.app.busy:
            return
        dialog = tk.Toplevel(self)
        dialog.title('加入名单')
        dialog.transient(self.app)
        mode = tk.StringVar(value='single')
        identity = tk.StringVar()
        info = tk.StringVar()
        form = ttk.Frame(dialog, padding=16)
        form.pack(fill='both', expand=True)
        ttk.Radiobutton(form, text='单个', variable=mode, value='single').grid(row=0, column=0, sticky='w')
        entry = ttk.Entry(form, textvariable=identity, width=20)
        entry.grid(row=0, column=1, padx=(8, 0), pady=4)
        ttk.Radiobutton(form, text='全部同步', variable=mode, value='all').grid(row=1, column=0, sticky='w')
        ttk.Label(form, textvariable=info).grid(row=2, column=0, columnspan=2, sticky='w', pady=4)
        buttons = ttk.Frame(form)
        buttons.grid(row=3, column=0, columnspan=2, sticky='e', pady=(8, 0))

        def confirm():
            if mode.get() == 'single':
                text = identity.get().strip()
                if not text.isascii() or not text.isdigit() or int(text) > 2147483647:
                    info.set('请输入有效的基板编号。')
                    return
                action = lambda: self.app.repository.change_lite_pet_selection([int(text)], True)
            else:
                action = self.app.repository.sync_lite_pet_selection
            dialog.destroy()
            self.info.set('正在保存…')
            self.app.request(self, action, lambda result: self.app.after_idle(self.search))

        ttk.Button(buttons, text='确认', command=confirm).pack(side='left')
        ttk.Button(buttons, text='取消', command=dialog.destroy).pack(side='left', padx=(8, 0))
        self.app.center_window(dialog)
        dialog.grab_set()
        entry.focus_set()

    def change_selection(self, add):
        if not self.app.connected or self.app.busy:
            return
        identities = [int(identity) for identity in self.tree.selection() if identity in self.rows]
        if not identities:
            self.info.set('请先选择宠物基板。')
            return
        self.info.set('正在保存…')
        self.app.request(self, lambda: self.app.repository.change_lite_pet_selection(identities, add),
                         lambda result: self.app.after_idle(self.search))
