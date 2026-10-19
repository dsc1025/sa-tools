from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from catalog import PET_TEMPLATE_FIELDS, display
from connection import ConnectionFailure, QueryFailure


class TemplateEditor(tk.Toplevel):
    def __init__(self, page, data, creating=False):
        super().__init__(page)
        self.creating = creating
        skills = data['skills']
        data = data['template']
        adapters = {'pet': '宠技', 'magic': '魔法', 'profession': '职业技能'}
        self.skill_labels = {str(row['skill_id']):
                             f'{row["skill_id"]} · {display(row["name"])} · '
                             f'{adapters.get(row["adapter"], row["adapter"])}'
                             + (' · 执行函数未注册' if not row['function_registered'] else '')
                             for row in skills}
        self.skill_labels.update({'0': '0 · 空技能', 'NULL': 'NULL · 使用旧编号'})
        self.skill_values = {label: identity for identity, label in self.skill_labels.items()}
        self.page, self.app, self.original = page, page.app, data
        self.generation = 0
        self.loaded = True
        self.saving = False
        self.info = tk.StringVar(value='')
        self.fields = {}
        self.skill_popup = None
        self.skill_box = None
        self.title('新增宠物基板' if creating else f'宠物基板 #{data["tempno"]}')
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
            if creating and key == 'tempno':
                definition = ('宠物基板编号。', 0, 2147483647)
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
                entry.bind('<Down>', lambda event, box=entry: self.focus_skills(box))
                entry.bind('<Escape>', self.hide_skills)
                entry.bind('<Button-1>', self.hide_skills)
                entry.bind('<FocusOut>', lambda event: self.after_idle(self.leave_skills))
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
        self.hide_skills()
        if (not keyword or text in self.skill_values or not values
                or self.saving or self.focus_get() is not box):
            return
        if self.skill_popup is None:
            self.skill_popup = tk.Toplevel(self)
            self.skill_popup.withdraw()
            self.skill_popup.overrideredirect(True)
            self.skill_popup.transient(self)
            self.skill_list = tk.Listbox(self.skill_popup, exportselection=False)
            self.skill_list.pack(side='left', fill='both', expand=True)
            scroll = ttk.Scrollbar(self.skill_popup, command=self.skill_list.yview)
            scroll.pack(side='right', fill='y')
            self.skill_list.configure(yscrollcommand=scroll.set)
            self.skill_list.bind('<ButtonRelease-1>', self.choose_skill)
            self.skill_list.bind('<Return>', self.choose_skill)
            self.skill_list.bind('<Escape>', self.cancel_skills)
            self.skill_list.bind('<FocusOut>', lambda event: self.after_idle(self.leave_skills))
        self.skill_box = box
        self.skill_list.delete(0, 'end')
        self.skill_list.insert('end', *values)
        self.skill_list.configure(height=min(8, len(values)))
        self.skill_popup.update_idletasks()
        height = self.skill_popup.winfo_reqheight()
        width = max(box.winfo_width(), self.skill_popup.winfo_reqwidth())
        x = min(box.winfo_rootx(), max(0, self.winfo_screenwidth() - width))
        y = box.winfo_rooty() + box.winfo_height()
        if y + height > self.winfo_screenheight():
            y = max(0, box.winfo_rooty() - height)
        self.skill_popup.geometry(f'{width}x{height}+{x}+{y}')
        self.skill_popup.deiconify()
        self.skill_popup.lift()

    def hide_skills(self, event=None):
        if self.skill_popup is not None:
            self.skill_popup.withdraw()
        self.skill_box = None

    def leave_skills(self):
        if self.skill_box is not None and self.focus_get() not in (self.skill_box, self.skill_list):
            self.hide_skills()

    def focus_skills(self, box):
        if self.skill_box is box:
            self.skill_list.selection_set(0)
            self.skill_list.activate(0)
            self.skill_list.focus_set()
            return 'break'

    def choose_skill(self, event=None):
        selection = self.skill_list.curselection()
        box = self.skill_box
        if selection and box is not None:
            value = self.skill_list.get(selection[0])
            self.hide_skills()
            box.set(value)
            box.icursor('end')
            box.focus_set()
        return 'break'

    def cancel_skills(self, event=None):
        box = self.skill_box
        self.hide_skills()
        if box is not None:
            box.focus_set()
        return 'break'

    def save(self):
        if self.saving or self.app.busy or not self.app.connected:
            return
        values = {key: variable.get() for key, (variable, entry) in self.fields.items()
                  if key in PET_TEMPLATE_FIELDS}
        for key in values:
            if key.startswith('pet_skill_id'):
                text = values[key].strip()
                values[key] = self.skill_values.get(text, text)
        self.hide_skills()
        self.saving = True
        for variable, entry in self.fields.values():
            entry.configure(state='disabled')
        self.save_button.configure(state='disabled')
        self.close_button.configure(state='disabled')
        self.info.set('正在保存…')
        original = dict(self.original)
        if self.creating:
            original['tempno'] = self.fields['tempno'][0].get()
        self.app.request(self, lambda: self.app.repository.save_pet_template(original, values,
                                                                            creating=self.creating),
                         self.saved, on_error=self.failed)

    def saved(self, data):
        self.creating = False
        self.original = data
        self.title(f'宠物基板 #{data["tempno"]}')
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
            entry.configure(state='normal' if key in PET_TEMPLATE_FIELDS
                            or (self.creating and key == 'tempno') else 'disabled')
        self.save_button.configure(state='normal')
        self.close_button.configure(state='normal')

    def close(self):
        if not self.saving:
            self.generation += 1
            self.destroy()
