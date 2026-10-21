from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from uuid import uuid4

from auction_repository import AuctionRepository
from catalog import display


LABELS = {'pet': '宠物', 'item': '道具', 'active': '在售', 'sold': '成交',
          'expired': '流拍', 'cancelled': '撤拍', 'pending': '待处理', 'claimed': '已领取',
          'asset': '资产退回 / 交付', 'refund': '退款待领', 'proceeds': '成交款待领',
          'deposit': '竞价扣款', 'claim': '资金领取', 'cancel': '撤回',
          'done': '已执行', 'rejected': '已拒绝', 'OK': '成功', 'STALE': '拍卖已变化或到期'}


class AuctionPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app
        self.repository = AuctionRepository(app.connection)
        self.kind, self.scope = 'auctions', None
        self.loaded, self.generation = False, 0
        self.available = False
        self.original_settings = None
        self.info = tk.StringVar(value='请先连接数据库。')
        self.keyword = tk.StringVar()
        self.asset_type = tk.StringVar(value='全部类型')
        self.status = tk.StringVar(value='全部状态')
        self.views, self.controls, self.tables = {}, [], []
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True)
        self.build_list('listings', '拍卖列表', (
            ('id', '拍卖编号', 90), ('type', '类型', 60), ('name', '名称', 140),
            ('seller', '卖家', 100), ('buyer', '买家', 100), ('price', '当前价 P', 90),
            ('buyout', '一口价 P', 90), ('status', '状态', 80), ('expires', '截止时间 UTC', 175)))
        self.build_list('bids', '出价记录', (
            ('id', '出价编号', 90), ('listing', '拍卖编号', 90), ('bidder', '出价角色', 100),
            ('amount', '报价 P', 90), ('charged', '本次扣款 P', 100),
            ('status', '状态', 100), ('created', '出价时间', 175)))
        self.build_list('claims', '待领取', (
            ('id', '领取编号', 90), ('listing', '拍卖编号', 90), ('recipient', '领取角色', 100),
            ('kind', '用途', 130), ('asset', '资产 / 金额 P', 140), ('status', '状态', 90),
            ('created', '生成时间', 175)))
        self.build_list('ledger', '资金流水', (
            ('id', '流水编号', 90), ('listing', '拍卖编号', 90), ('character', '角色', 100),
            ('kind', '业务类型', 130), ('amount', '金额 P', 90), ('operation', '关联编号', 150),
            ('created', '发生时间', 175)))
        self.build_settings()
        self.build_list('admin', '管理操作', (
            ('id', '操作编号', 90), ('action', '动作', 70), ('target', '拍卖编号', 90),
            ('operator', '操作人', 100), ('reason', '原因', 160), ('status', '执行状态', 100),
            ('result', '执行结果', 160), ('created', '提交时间', 175),
            ('request_id', '请求编号', 260)))
        ttk.Label(self, textvariable=self.info, wraplength=900).pack(anchor='w', pady=(8, 0))
        self.tabs.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.set_enabled(False)

    def build_list(self, kind, title, columns):
        page = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(page, text=title)
        toolbar = ttk.Frame(page)
        toolbar.pack(fill='x', pady=(0, 8))
        if kind == 'listings':
            for variable, values in ((self.asset_type, ('全部类型', '宠物', '道具')),
                                      (self.status, ('全部状态', '在售', '成交', '流拍', '撤拍'))):
                box = ttk.Combobox(toolbar, textvariable=variable, values=values, width=10, state='readonly')
                box.pack(side='left', padx=(0, 8))
                self.controls.append((box, 'readonly'))
            keyword = self.keyword
        else:
            ttk.Label(toolbar, text='拍卖编号 / 操作人' if kind == 'admin' else '拍卖编号 / 角色').pack(side='left', padx=(0, 8))
            keyword = tk.StringVar()
        search = ttk.Entry(toolbar, textvariable=keyword, width=24)
        search.pack(side='left', fill='x', expand=True)
        search.bind('<Return>', lambda event: self.search())
        refresh = ttk.Button(toolbar, text='刷新', command=self.search)
        refresh.pack(side='right', padx=(8, 0))
        self.controls.extend(((search, 'normal'), (refresh, 'normal')))
        listing = ttk.Frame(page)
        listing.pack(fill='both', expand=True)
        tree = ttk.Treeview(listing, columns=[key for key, _, _ in columns], show='headings', selectmode='browse')
        for key, caption, width in columns:
            tree.heading(key, text=caption)
            tree.column(key, width=width, minwidth=60, stretch=False)
        vertical = ttk.Scrollbar(listing, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(listing, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.tables.append(tree)
        footer = ttk.Frame(page)
        footer.pack(fill='x', pady=(8, 0))
        previous = ttk.Button(footer, text='上一页', command=lambda: self.turn(kind, -1))
        following = ttk.Button(footer, text='下一页', command=lambda: self.turn(kind, 1))
        previous.pack(side='left')
        following.pack(side='left', padx=8)
        position = tk.StringVar(value='第 1 页')
        ttk.Label(footer, textvariable=position).pack(side='left')
        actions = []
        if kind in ('listings', 'claims'):
            if kind == 'listings':
                cancel = ttk.Button(footer, text='申请撤回', command=self.cancel_dialog)
                cancel.pack(side='right')
                actions.append(cancel)
            detail = ttk.Button(footer, text='查看详情', command=self.details)
            detail.pack(side='right', padx=8)
            actions.append(detail)
        self.views[kind] = dict(page=page, tree=tree, columns=columns, keyword=keyword,
                                previous=previous, following=following, position=position,
                                offset=0, next=False, rows={}, loaded=False, actions=actions)
        tree.bind('<<TreeviewSelect>>', lambda event: self.update_actions())

    def build_settings(self):
        self.settings_page = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(self.settings_page, text='竞拍设置')
        self.enabled = tk.BooleanVar(value=False)
        toggle = ttk.Checkbutton(self.settings_page, text='允许新上架和出价', variable=self.enabled)
        toggle.grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 12))
        self.controls.append((toggle, 'normal'))
        self.settings = {}
        for row, (key, caption, value) in enumerate((
                ('listing_limit', '每角色同时在售上限', '5'), ('minimum_increment', '最小加价 P', '1'),
                ('listing_fee', '上架费 P', '0'), ('sale_fee', '成交费 P', '0')), start=1):
            ttk.Label(self.settings_page, text=caption).grid(row=row, column=0, sticky='w', pady=6, padx=(0, 16))
            variable = tk.StringVar(value=value)
            entry = ttk.Entry(self.settings_page, textvariable=variable, width=16, state='readonly' if row > 2 else 'normal')
            entry.grid(row=row, column=1, sticky='w', pady=6)
            self.settings[key] = variable
            self.controls.append((entry, 'readonly' if row > 2 else 'normal'))
        ttk.Label(self.settings_page, text='上架时长：4 / 8 / 12 小时；当前版本手续费固定为 0P。').grid(
            row=5, column=0, columnspan=3, sticky='w', pady=(16, 8))
        ttk.Label(self.settings_page, text='开关即时限制新上架和出价；上限用于后续上架校验，加价配置仅用于新订单。\n'
                  '暂停后已有订单继续到期结算，玩家仍可撤拍和领取。', wraplength=680).grid(
            row=6, column=0, columnspan=3, sticky='w', pady=8)
        actions = ttk.Frame(self.settings_page)
        actions.grid(row=7, column=0, columnspan=3, sticky='w', pady=12)
        refresh = ttk.Button(actions, text='重新读取', command=self.search)
        refresh.pack(side='left')
        self.save_button = ttk.Button(actions, text='提交设置', command=self.save_settings)
        self.save_button.pack(side='left', padx=8)
        self.controls.append((refresh, 'normal'))

    def current_kind(self):
        return next((kind for kind, view in self.views.items() if str(view['page']) == self.tabs.select()), 'settings')

    def set_enabled(self, enabled):
        self.available = enabled
        for control, state in self.controls:
            control.configure(state=state if enabled else 'disabled')
        self.save_button.configure(state='normal' if enabled and self.original_settings else 'disabled')
        self.update_actions()

    def update_actions(self):
        for kind, view in self.views.items():
            view['previous'].configure(state='normal' if self.available and view['offset'] else 'disabled')
            view['following'].configure(state='normal' if self.available and view['next'] else 'disabled')
            row = self.selected(kind)
            for button in view['actions']:
                allowed = self.available and row is not None
                if button['text'] == '申请撤回':
                    allowed = allowed and row['status'] == 'active'
                button.configure(state='normal' if allowed else 'disabled')

    def selected(self, kind):
        view = self.views[kind]
        selection = view['tree'].selection()
        return view['rows'].get(selection[0]) if selection else None

    def search(self, reset=True):
        if not self.app.connected or self.app.busy:
            return
        kind = self.current_kind()
        self.loaded = True
        self.info.set('正在读取竞拍数据…')
        if kind == 'settings':
            self.app.request(self, self.repository.settings, self.show_settings)
            return
        view = self.views[kind]
        if reset:
            view['offset'] = 0
        offset, keyword = view['offset'], view['keyword'].get()
        asset_type = {'宠物': 'pet', '道具': 'item'}.get(self.asset_type.get(), 'all')
        status = {'在售': 'active', '成交': 'sold', '流拍': 'expired', '撤拍': 'cancelled'}.get(self.status.get(), 'all')
        self.app.request(self, lambda: self.repository.page(kind, offset, keyword, asset_type, status),
                         lambda data: self.show_rows(kind, offset, data))

    def show_rows(self, kind, offset, data):
        view = self.views[kind]
        rows, view['next'] = data
        tree = view['tree']
        tree.delete(*tree.get_children())
        view['rows'] = {str(row['id']): row for row in rows}
        for row in rows:
            values = []
            for key, _, _ in view['columns']:
                value = row[key]
                text = '' if value is None else display(value)
                values.append(LABELS.get(text, text) if key in ('type', 'status', 'kind', 'action', 'result') else text)
            tree.insert('', 'end', iid=str(row['id']), values=values)
        view['loaded'] = True
        view['position'].set(f'第 {offset // self.repository.page_size + 1} 页 · 本页 {len(rows)} 条')
        self.info.set('已读取。截止时间使用 UTC；其余记录时间按数据库原值显示。点击刷新更新结果。')

    def show_settings(self, data):
        self.original_settings = data
        self.enabled.set(bool(data['enabled']))
        for key, variable in self.settings.items():
            variable.set(str(data[key]))
        self.info.set(f'已读取竞拍设置，版本 {data["version"]}。')

    def save_settings(self):
        if not self.available or self.original_settings is None:
            return
        values = {key: variable.get() for key, variable in self.settings.items()}
        values['enabled'] = int(self.enabled.get())
        original = dict(self.original_settings)
        self.info.set('正在保存竞拍设置…')
        self.app.request(self, lambda: self.repository.save_settings(original, values), self.settings_saved)

    def settings_saved(self, data):
        self.show_settings(data)
        self.info.set('竞拍设置已保存。')

    def turn(self, kind, direction):
        view = self.views[kind]
        if not self.available or (direction > 0 and not view['next']):
            return
        view['offset'] = max(0, view['offset'] + direction * self.repository.page_size)
        self.search(reset=False)

    def tab_changed(self, event=None):
        kind = self.current_kind()
        if kind == 'settings' or not self.views[kind]['loaded']:
            self.search()

    def details(self):
        kind = self.current_kind()
        row = self.selected(kind)
        if not self.available or not row:
            return
        identity = row['id'] if kind == 'listings' else row['listing']
        self.app.request(self, lambda: self.repository.listing_detail(identity), self.show_detail)

    def show_detail(self, row):
        window = tk.Toplevel(self)
        window.title(f'拍卖 #{row["id"]}')
        window.transient(self.app)
        text = tk.Text(window, width=90, height=28, wrap='word')
        scrollbar = ttk.Scrollbar(window, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        captions = {'id': '拍卖编号', 'type': '类型', 'name': '名称', 'seller_name': '上架时卖家',
                    'seller_id': '卖家角色编号', 'buyer_id': '买家角色编号', 'buyer_name': '买家',
                    'highest_bidder_id': '最高出价角色编号', 'highest_bidder_name': '最高出价角色',
                    'level': '等级', 'quantity': '数量', 'start_price': '起拍价 P',
                    'buyout_price': '一口价 P', 'current_price': '当前价 P', 'status': '状态',
                    'duration_hours': '时长（小时）', 'minimum_increment': '最小加价 P',
                    'expires_at': '截止时间 UTC', 'closed_at': '结束时间 UTC', 'server_utc': '服务器当前 UTC',
                    'version': '版本', 'unique_code': '资产唯一编号'}
        for key, caption in captions.items():
            value = '' if row[key] is None else display(row[key])
            if key in ('status', 'type'):
                value = LABELS.get(value, value)
            text.insert('end', f'{caption}：{value}\n')
        text.configure(state='disabled')
        self.app.center_window(window)
        self.info.set('已读取拍卖详情。')

    def cancel_dialog(self):
        row = self.selected('listings')
        if not self.available or not row or row['status'] != 'active':
            return
        window = tk.Toplevel(self)
        window.title(f'申请撤回拍卖 #{row["id"]}')
        window.transient(self.app)
        form = ttk.Frame(window, padding=16)
        form.pack(fill='both', expand=True)
        ttk.Label(form, text='由 SAAC 校验并执行撤回；已有出价将生成退款待领取。\n'
                  '提交后请在“管理操作”刷新查看执行结果。', wraplength=480).pack(anchor='w')
        operator, reason = tk.StringVar(), tk.StringVar()
        for caption, variable in (('操作人', operator), ('撤回原因', reason)):
            ttk.Label(form, text=caption).pack(anchor='w', pady=(10, 2))
            ttk.Entry(form, textvariable=variable, width=60).pack(fill='x')
        status = tk.StringVar()
        ttk.Label(form, textvariable=status, wraplength=480).pack(anchor='w', pady=8)
        request_id = uuid4().hex
        buttons = ttk.Frame(form)
        buttons.pack(anchor='e')
        saving = [False]

        def finish():
            saving[0] = False
            if window.winfo_exists():
                submit.configure(state='normal')
                close.configure(state='normal')

        def failed(error):
            finish()
            if window.winfo_exists():
                status.set(f'{error}\n请求编号：{request_id}；提交异常时先在管理操作查询此编号。')

        def saved(result):
            finish()
            if window.winfo_exists():
                window.destroy()
            self.info.set(f'已提交撤回请求 {result}，等待 SAAC 执行。请在管理操作刷新查看。')

        def submit_request():
            if saving[0] or not self.available:
                return
            operator_text, reason_text = operator.get(), reason.get()
            saving[0] = True
            submit.configure(state='disabled')
            close.configure(state='disabled')
            status.set('正在提交撤回请求…')
            self.app.request(self, lambda: self.repository.cancel(row, operator_text, reason_text, request_id),
                             saved, on_error=failed)

        submit = ttk.Button(buttons, text='提交撤回请求', command=submit_request)
        submit.pack(side='left')
        close = ttk.Button(buttons, text='关闭', command=lambda: window.destroy() if not saving[0] else None)
        close.pack(side='left', padx=(8, 0))
        window.protocol('WM_DELETE_WINDOW', lambda: window.destroy() if not saving[0] else None)
        self.app.center_window(window)

    def clear_results(self):
        self.generation += 1
        self.loaded = False
        self.original_settings = None
        for view in self.views.values():
            view['tree'].delete(*view['tree'].get_children())
            view.update(rows={}, loaded=False, offset=0, next=False)
            view['position'].set('第 1 页')
