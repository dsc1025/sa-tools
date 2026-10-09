from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from catalog import PET_TEMPLATE_FIELDS, display
from connection import ConnectionFailure, QueryFailure


class TemplateEditor(tk.Toplevel):
    def __init__(self, page, data):
        super().__init__(page)
        skills = data['skills']
        data = data['template']
        self.skill_labels = {str(row['skill_id']):
                             f'{row["skill_id"]} · {display(row["name"])} · '
                             f'{"魔法" if row["adapter"] == "magic" else "宠技"}'
                             for row in skills}
        self.skill_labels.update({'0': '0 · 空技能', 'NULL': 'NULL · 使用旧编号'})
        self.skill_values = {label: identity for identity, label in self.skill_labels.items()}
        self.page, self.app, self.original = page, page.app, data
        self.generation = 0
        self.loaded = True
        self.saving = False
        self.info = tk.StringVar(value='')
        self.fields = {}
        self.title(f'宠物基板 #{data["tempno"]}')
        self.transient(self.app)
        self.app.center_window(self, 660, 560)
        content = ttk.Frame(self)
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
        self.wheel_delta = 0

        def wheel(event):
            if canvas.bbox('all') and canvas.bbox('all')[3] > canvas.winfo_height():
                self.wheel_delta += event.delta
                steps = int(self.wheel_delta / 120)
                self.wheel_delta -= steps * 120
                canvas.yview_scroll(-steps * 3, 'units')
            return 'break'

        self.bind('<MouseWheel>', wheel)
        form.columnconfigure(2, weight=1)
        row_index = 0
        leading = ('source_order', 'imgnumber', 'petflg', 'size', 'limitlevel')
        skills = ('slot',
                  # *(f'petskill{index}' for index in range(1, 8)),
                  *(f'pet_skill_id{index}' for index in range(1, 8)))
        fields = (*leading, *(key for key in data if key not in leading and key not in skills
                             and not key.startswith('petskill')),
                  *skills)
        for key in fields:
            if key not in data:
                continue
            value = data[key]
            if key.startswith(('atomfixname', 'atombaseadd', 'atomfixmin', 'atomfixmax')):
                continue
            definition = PET_TEMPLATE_FIELDS.get(key)
            if key.startswith(('petskill', 'pet_skill_id')):
                meaning = ''
            elif definition:
                meaning = definition[0]
            else:
                meaning = {
                    'source_order': '记录加载顺序。',
                    'tempno': '宠物基板编号。',
                    'petflg': '基板宠物标记。',
                    'critical': '会心相关参数。',
                    'counter': '反击相关参数。',
                }.get(key, '')
            ttk.Label(form, text=key).grid(row=row_index, column=0, sticky='w',
                                           padx=(0, 12), pady=5)
            variable = tk.StringVar(value=display(value))
            if key.startswith('pet_skill_id'):
                variable.set(self.skill_labels.get(display(value), display(value)))
                entry = ttk.Combobox(form, textvariable=variable, width=36,
                                     values=tuple(self.skill_labels.values()), state='normal')
                variable.trace_add('write', lambda *args, box=entry, text=variable:
                                   self.filter_skills(box, text.get()))
            else:
                entry = ttk.Entry(form, textvariable=variable, width=14)
            entry.grid(row=row_index, column=1,
                       columnspan=2 if key.startswith('pet_skill_id') else 1,
                       sticky='w', padx=(0, 12), pady=5)
            if meaning:
                ttk.Label(form, text=meaning, wraplength=340).grid(
                    row=row_index, column=2, sticky='w', pady=5)
            entry.configure(state='normal' if definition else 'disabled')
            self.fields[key] = (variable, entry)
            row_index += 1
        ttk.Label(self, textvariable=self.info, wraplength=620, padding=12).pack(fill='x')
        actions = ttk.Frame(self, padding=(12, 0, 12, 12))
        actions.pack(fill='x')
        self.close_button = ttk.Button(actions, text='关闭', command=self.close)
        self.close_button.pack(side='right')
        self.save_button = ttk.Button(actions, text='保存', command=self.save)
        self.save_button.pack(side='right', padx=8)
        self.protocol('WM_DELETE_WINDOW', self.close)

    def filter_skills(self, box, text):
        keyword = text.strip().casefold()
        values = tuple(self.skill_labels.values())
        if text not in self.skill_values and keyword:
            values = tuple(label for label in values if keyword in label.casefold())
        box.configure(values=values)

    def save(self):
        if self.saving or self.app.busy or not self.app.connected:
            return
        values = {key: variable.get() for key, (variable, entry) in self.fields.items()
                  if key in PET_TEMPLATE_FIELDS}
        for key in values:
            if key.startswith('pet_skill_id'):
                text = values[key].strip()
                values[key] = self.skill_values.get(text, text)
        self.saving = True
        for variable, entry in self.fields.values():
            entry.configure(state='disabled')
        self.save_button.configure(state='disabled')
        self.close_button.configure(state='disabled')
        self.info.set('正在保存…')
        self.app.request(self, lambda: self.app.repository.save_pet_template(self.original, values),
                         self.saved, on_error=self.failed)

    def saved(self, data):
        self.original = data
        for key, (variable, entry) in self.fields.items():
            value = display(data[key])
            variable.set(self.skill_labels.get(value, value) if key.startswith('pet_skill_id') else value)
        self.finish()
        self.info.set('已保存。服务端加载后的生效时间取决于其模板重载机制。')
        self.app.after_idle(lambda: self.page.load(refresh=True))

    def failed(self, error):
        if not self.winfo_exists():
            return
        self.finish()
        self.info.set(str(error) if isinstance(error, (ConnectionFailure, QueryFailure, ValueError))
                      else '保存失败，请关闭表单并重新读取确认状态。')

    def finish(self):
        self.saving = False
        for key, (variable, entry) in self.fields.items():
            entry.configure(state='normal' if key in PET_TEMPLATE_FIELDS else 'disabled')
        self.save_button.configure(state='normal')
        self.close_button.configure(state='normal')

    def close(self):
        if not self.saving:
            self.generation += 1
            self.destroy()
