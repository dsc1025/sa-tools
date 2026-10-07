from __future__ import annotations

import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

from browser import Browser
from catalog import Repository, display
from connection import Connection, ConnectionFailure, QueryFailure
from settings import Profile, Settings


class Workshop(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('石器工坊 · SA 综合数据管理工具')
        self.geometry('1020x800')
        self.minsize(900, 780)
        self.settings = Settings()
        self.connection = Connection()
        self.repository = Repository(self.connection)
        self.pages = []
        self.pet_pages = []
        self.ride_pages = []
        self.selected_account = None
        self.selected_character = None
        self.account_name = ''
        self.context = tk.StringVar(value='请先选择账户，再选择角色。')
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.results = queue.Queue()
        self.busy = False
        self.connected = False
        self.closing = False
        self.active_profile = None
        self.profiles = []
        self.edit_index = -1
        self.config_job = None
        self.loading_config = True
        self.config_ready = False
        self.values = {name: tk.StringVar(value=str(value))
                       for name, value in vars(Profile()).items()}
        self.ssh_secret = self.values['ssh_secret']
        self.mysql_password = self.values['mysql_password']
        self.status = tk.StringVar(value='未连接')
        self.details = tk.StringVar(value='连接后显示数据库信息。')
        self.controls = []
        self.build_ui()
        try:
            self.profiles = self.settings.load()
            self.refresh_profiles()
            if isinstance(self.settings.selected, int) and 0 <= self.settings.selected < len(self.profiles):
                self.profile_box.current(self.settings.selected)
                self.select_profile()
            if isinstance(self.settings.draft, dict):
                for name, variable in self.values.items():
                    if name in self.settings.draft:
                        variable.set(str(self.settings.draft[name]))
            self.config_ready = True
        except Exception:
            messagebox.showerror('配置读取失败', '连接配置无法读取，原文件未改动。')
        self.loading_config = False
        for variable in self.values.values():
            variable.trace_add('write', self.schedule_config_save)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def build_ui(self):
        outer = ttk.Frame(self, padding=16)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, textvariable=self.status).pack(anchor='w', pady=(6, 14))
        style = ttk.Style(self)
        style.configure('Main.TNotebook', tabposition='wn')
        style.configure('Main.TNotebook.Tab', padding=(12, 10))
        body = ttk.Notebook(outer, style='Main.TNotebook')
        self.notebook = body
        body.pack(fill='both', expand=True)
        panel = ttk.Frame(body, padding=16)
        players = ttk.Frame(body)
        self.players = players
        body.add(players, text='账户')
        players.columnconfigure(0, weight=1)
        players.rowconfigure(0, weight=1)
        players.rowconfigure(2, weight=1)
        players.rowconfigure(3, weight=2)
        account_page = Browser(players, self, (('账户', 'accounts'),))
        account_page.grid(row=0, column=0, sticky='nsew')
        self.pages.append(account_page)
        selection = ttk.Frame(players, padding=(12, 6))
        selection.grid(row=1, column=0, sticky='ew')
        ttk.Label(selection, textvariable=self.context).pack(side='left', padx=(0, 16))
        role_page = Browser(players, self, (('角色', 'characters'),), scoped=True, refresh_parent=selection)
        role_page.grid(row=2, column=0, sticky='nsew')
        self.pages.append(role_page)
        self.assets_tabs = ttk.Notebook(players)
        self.assets_tabs.grid(row=3, column=0, sticky='nsew', padx=12, pady=(0, 12))
        modules = (('宠物', (('玩家宠物', 'pets'),)),
                   ('物品', (('玩家物品', 'items'),)))
        for title, modes in modules:
            page = Browser(self.assets_tabs, self, modes, scoped=True)
            self.pages.append(page)
            self.assets_tabs.add(page, text=title)
        self.pet_tabs = ttk.Notebook(body)
        body.add(self.pet_tabs, text='宠物')
        for title, kind in (('基板', 'pet_templates'),
                            ('实例', 'enemy_templates'), ('实例组合', 'enemy_groups'),
                            ('遇敌区域', 'encounter_areas'), ('捕捉条件', 'pet_capture_requirements')):
            page = Browser(self.pet_tabs, self, ((title, kind),))
            self.pet_pages.append(page)
            self.pages.append(page)
            self.pet_tabs.add(page, text=title)
        self.item_page = Browser(body, self, (('道具总表', 'item_templates'),))
        self.pages.append(self.item_page)
        body.add(self.item_page, text='道具')
        self.skill_page = Browser(body, self, (('技能魔法总表', 'skills'),))
        self.pages.append(self.skill_page)
        body.add(self.skill_page, text='技能')
        self.ride_tabs = ttk.Notebook(body)
        body.add(self.ride_tabs, text='骑乘')
        for title, kind in (('默认映射', 'ride_base'), ('骑宠许可', 'ride_pet'),
                            ('骑乘形象', 'ride_image'), ('人物类型', 'ride_player'), ('族长许可', 'ride_leader')):
            page = Browser(self.ride_tabs, self, ((title, kind),))
            self.ride_pages.append(page)
            self.pages.append(page)
            self.ride_tabs.add(page, text=title)
        body.add(panel, text='设置')
        body.select(panel)
        body.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.assets_tabs.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.pet_tabs.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.ride_tabs.bind('<<NotebookTabChanged>>', self.tab_changed)
        profiles = ttk.Frame(panel)
        profiles.pack(fill='x', pady=(0, 10))
        ttk.Label(profiles, text='服务器配置').pack(side='left')
        self.profile_box = ttk.Combobox(profiles, state='readonly', width=24)
        self.profile_box.pack(side='left', padx=8)
        self.profile_box.bind('<<ComboboxSelected>>', self.select_profile)
        self.controls.append((self.profile_box, 'readonly'))
        for title, action in (('新建', self.new_profile), ('保存配置', self.save_profile),
                              ('删除配置', self.delete_profile)):
            button = ttk.Button(profiles, text=title, command=action)
            button.pack(side='left', padx=3)
            self.controls.append((button, 'normal'))
        form = ttk.Frame(panel)
        form.pack(fill='x')
        form.columnconfigure(1, weight=1)
        fields = [('配置名称', 'name'), ('SSH 地址', 'ssh_host'), ('SSH 端口', 'ssh_port'),
                  ('SSH 用户', 'ssh_user'), ('SSH 认证', 'auth'), ('SSH 私钥文件', 'key_path'),
                  ('known_hosts 文件', 'known_hosts'), ('SSH 密码 / 私钥口令', None),
                  ('MySQL 地址（SSH 服务器侧）', 'mysql_host'), ('MySQL 端口', 'mysql_port'),
                  ('MySQL 用户', 'mysql_user'), ('MySQL 密码', None), ('数据库', 'database')]
        for row, (label, name) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky='w', padx=(0, 12), pady=4)
            variable = (self.ssh_secret if row == 7 else self.mysql_password) if name is None else self.values[name]
            if name == 'auth':
                widget = ttk.Combobox(form, textvariable=variable, values=('password', 'key'), state='readonly')
                state = 'readonly'
            else:
                widget = ttk.Entry(form, textvariable=variable, show='*' if name is None else '')
                state = 'normal'
                widget.configure(state=state)
            widget.grid(row=row, column=1, sticky='ew', pady=4)
            self.controls.append((widget, state))
            if name in ('key_path', 'known_hosts'):
                button = ttk.Button(form, text='选择', command=lambda field=name: self.choose_file(field))
                button.grid(row=row, column=2, padx=(6, 0))
                self.controls.append((button, 'normal'))
        ttk.Label(panel, text='SSH 主机密钥须事先核实并记录在 known_hosts 中。\n'
                  '不指定文件时使用当前用户 ~/.ssh/known_hosts；工具不自动接受未知主机。').pack(anchor='w')
        actions = ttk.Frame(panel)
        actions.pack(fill='x', pady=12)
        self.connect_button = ttk.Button(actions, text='连接数据库', command=self.connect)
        self.connect_button.pack(side='left')
        self.disconnect_button = ttk.Button(actions, text='断开连接', command=self.disconnect, state='disabled')
        self.disconnect_button.pack(side='left', padx=10)
        ttk.Label(panel, textvariable=self.details, wraplength=680).pack(anchor='w', pady=4)

    def read_profile(self, validate=True):
        values = {name: variable.get() if name in ('ssh_secret', 'mysql_password') else variable.get().strip()
                  for name, variable in self.values.items()}
        try:
            values['ssh_port'] = int(values['ssh_port'])
            values['mysql_port'] = int(values['mysql_port'])
        except ValueError:
            raise ValueError('端口必须是整数。') from None
        profile = Profile(**values)
        if validate:
            profile.validate()
        return profile

    def refresh_profiles(self):
        self.profile_box.configure(values=[profile.name for profile in self.profiles])

    def choose_file(self, field):
        title = '选择 SSH 私钥' if field == 'key_path' else '选择 known_hosts 文件'
        path = filedialog.askopenfilename(title=title)
        if path:
            self.values[field].set(path)

    def new_profile(self):
        self.persist_config()
        self.loading_config = True
        self.edit_index = -1
        self.profile_box.set('')
        for name, value in vars(Profile()).items():
            self.values[name].set(str(value))
        self.ssh_secret.set('')
        self.mysql_password.set('')
        self.loading_config = False
        self.schedule_config_save()

    def select_profile(self, event=None):
        index = self.profile_box.current()
        if index < 0:
            return
        self.persist_config()
        self.loading_config = True
        self.edit_index = index
        self.profile_box.current(index)
        profile = self.profiles[index]
        for name, value in vars(profile).items():
            self.values[name].set(str(value))
        self.loading_config = False
        self.schedule_config_save()

    def schedule_config_save(self, *args):
        if self.loading_config or not self.config_ready or self.closing:
            return
        if self.config_job is not None:
            self.after_cancel(self.config_job)
        self.config_job = self.after(500, self.persist_config)

    def persist_config(self):
        if self.config_job is not None:
            self.after_cancel(self.config_job)
            self.config_job = None
        if not self.config_ready:
            return
        draft = {name: variable.get() for name, variable in self.values.items()}
        profiles = self.profiles.copy()
        try:
            profile = self.read_profile(validate=False)
        except ValueError:
            profile = None
        if profile is not None and 0 <= self.edit_index < len(profiles):
            if not any(entry.name == profile.name and position != self.edit_index
                       for position, entry in enumerate(profiles)):
                profiles[self.edit_index] = profile
        try:
            self.settings.save(profiles, draft, self.edit_index)
            self.profiles = profiles
            self.refresh_profiles()
            if self.edit_index >= 0:
                self.profile_box.current(self.edit_index)
        except Exception:
            self.details.set('配置自动保存失败，请检查项目目录的写入权限。')
            return

    def save_profile(self):
        try:
            profile = self.read_profile()
            index = self.profile_box.current()
            if any(entry.name == profile.name and position != index
                   for position, entry in enumerate(self.profiles)):
                raise ValueError('配置名称已存在，请使用其他名称。')
            profiles = self.profiles.copy()
            if index < 0:
                profiles.append(profile)
                index = len(profiles) - 1
            else:
                profiles[index] = profile
            self.settings.save(profiles, {name: value.get() for name, value in self.values.items()},
                               index)
            self.profiles = profiles
            self.edit_index = index
            self.config_ready = True
            self.refresh_profiles()
            self.profile_box.current(index)
        except ValueError as exc:
            messagebox.showerror('配置未保存', str(exc))
            return
        except Exception:
            messagebox.showerror('配置未保存', '无法写入本地连接配置。')
            return
        self.details.set('配置及密码已保存到项目目录的 config.json。')

    def delete_profile(self):
        index = self.profile_box.current()
        if index < 0:
            return
        if not messagebox.askyesno('删除配置', '删除此本地连接配置？'):
            return
        profiles = self.profiles.copy()
        del profiles[index]
        try:
            self.settings.save(profiles)
        except Exception:
            messagebox.showerror('删除失败', '无法写入本地连接配置。')
            return
        self.profiles = profiles
        self.edit_index = -1
        self.refresh_profiles()
        self.new_profile()

    def update_controls(self):
        editable = not self.busy and not self.connected
        for widget, state in self.controls:
            widget.configure(state=state if editable else 'disabled')
        self.connect_button.configure(state='normal' if editable else 'disabled')
        self.disconnect_button.configure(state='normal' if self.connected and not self.busy else 'disabled')
        for page in self.pages:
            allowed = (page in self.pet_pages or page in self.ride_pages or page in (self.item_page, self.skill_page)
                       or page.kind == 'accounts' or page.scope is not None)
            page.set_enabled(self.connected and not self.busy and allowed)

    def tab_changed(self, event=None):
        if not self.connected or self.busy:
            return
        for page in (self.item_page, self.skill_page):
            if self.notebook.select() == str(page):
                if not page.loaded:
                    page.search()
                return
        if self.notebook.select() == str(self.ride_tabs):
            selected = self.ride_tabs.select()
            for page in self.ride_pages:
                if str(page) == selected and not page.loaded:
                    page.search()
                    break
            return
        if self.notebook.select() == str(self.pet_tabs):
            selected = self.pet_tabs.select()
            for page in self.pet_pages:
                if str(page) == selected and not page.loaded:
                    page.search()
                    break
            return
        if self.notebook.select() != str(self.players):
            return
        if not self.pages[0].loaded:
            self.pages[0].search()
            return
        if self.selected_account is not None and not self.pages[1].loaded:
            self.pages[1].search()
            return
        selected = self.assets_tabs.select()
        for page in self.pages:
            if str(page) == selected and not page.loaded and (page.kind == 'accounts' or page.scope is not None):
                page.search()
                break

    def selected_row(self, page, row, refresh=False):
        if page in self.pet_pages or page in self.ride_pages or page in (self.item_page, self.skill_page):
            return
        if page.kind == 'accounts':
            identity = row['id']
            if identity == self.selected_account:
                return
            self.selected_account = identity
            self.account_name = display(row['username'])
            self.context.set(f'账户：{self.account_name}')
            self.selected_character = None
            for child in self.pages[1:4]:
                child.clear_results()
                child.keyword.set('')
                child.scope = ('account_id', identity) if child.kind == 'characters' else None
                child.info.set('请先选择角色。' if child.kind != 'characters' else '已按选中账户筛选。')
        elif page.kind == 'characters':
            identity = row['id']
            changed = identity != self.selected_character
            if not changed and not refresh:
                return
            self.selected_character = identity
            self.context.set(f'账户：{self.account_name} · 角色：{display(row["name"])}')
            for child in self.pages[2:4]:
                child.clear_results()
                if changed:
                    child.keyword.set('')
                child.scope = ('character_id', identity)
                child.info.set('已按选中角色筛选，切换到此页查看。')
        self.update_controls()
        self.after_idle(self.tab_changed)

    def request(self, page, function, callback, on_error=None):
        self.submit(('query', page, callback, page.generation, on_error), function)

    def show_roles(self, rows):
        previous = self.selected_character
        if rows:
            index = next((index for index, row in enumerate(rows) if row['id'] == previous), 0)
            tree = self.pages[1].tree
            tree.selection_set(str(rows[index]['id']))
            tree.see(str(rows[index]['id']))
            self.selected_row(self.pages[1], rows[index], refresh=True)
        else:
            self.selected_character = None
            self.context.set(f'账户：{self.account_name}')
            for page in self.pages[2:4]:
                page.clear_results()
                page.scope = None

    def submit(self, operation, function):
        self.busy = True
        self.update_controls()
        future = self.executor.submit(function)
        future.add_done_callback(lambda completed: self.results.put((operation, completed)))

    def connect(self):
        try:
            profile = self.read_profile()
        except ValueError as exc:
            messagebox.showerror('无法连接', str(exc))
            return
        self.active_profile = profile
        ssh_secret, mysql_password = self.ssh_secret.get(), self.mysql_password.get()
        self.status.set('正在建立 SSH 隧道并连接 MySQL…')
        self.details.set('连接过程仅执行只读结构查询。')
        self.submit('connect', lambda: self.connection.connect(profile, ssh_secret, mysql_password))

    def disconnect(self):
        self.status.set('正在断开连接…')
        self.submit('disconnect', self.connection.disconnect)

    def poll(self):
        try:
            operation, future = self.results.get_nowait()
        except queue.Empty:
            if not self.closing:
                self.after(100, self.poll)
            return
        self.busy = False
        try:
            result = future.result()
            if operation == 'connect':
                self.repository.configure_cache(self.active_profile)
                self.connected = True
                self.status.set(f'已连接：{self.active_profile.name} · 支持账户及角色编辑')
                self.details.set(result)
                self.notebook.select(self.players)
            elif operation == 'disconnect':
                self.connected = False
                self.status.set('未连接')
                self.details.set('SSH 隧道和数据库连接已关闭。')
            elif isinstance(operation, tuple) and operation[0] == 'query':
                if operation[3] == operation[1].generation:
                    operation[2](result)
        except Exception as exc:
            if isinstance(operation, tuple) and operation[0] == 'query' and operation[4] is not None:
                operation[4](exc)
            if isinstance(operation, tuple) and operation[0] == 'query' and not isinstance(exc, ConnectionFailure):
                if operation[3] == operation[1].generation:
                    operation[1].loaded = True
                    operation[1].info.set(str(exc) if isinstance(exc, (QueryFailure, ValueError)) else '查询结果处理失败，请检查数据格式。')
            else:
                self.connected = False
                self.status.set('连接失败' if operation == 'connect' else '连接已断开')
                self.details.set(str(exc) if isinstance(exc, ConnectionFailure) else '操作失败，请检查连接配置。')
        if not self.connected:
            self.selected_account = None
            self.selected_character = None
            self.context.set('请先选择账户，再选择角色。')
            for page in self.pages:
                page.clear_results()
                page.scope = None
                page.info.set('请先在设置中连接数据库。')
        self.update_controls()
        if self.connected:
            self.after_idle(self.tab_changed)
        if not self.closing:
            self.after(100, self.poll)

    def close(self):
        self.persist_config()
        self.closing = True
        self.connection.stop.set()
        self.ssh_secret.set('')
        self.mysql_password.set('')
        self.executor.submit(self.connection.disconnect)
        self.executor.shutdown(wait=False)
        self.destroy()


if __name__ == '__main__':
    Workshop().mainloop()
