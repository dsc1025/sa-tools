from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class AuctionPage(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, padding=12)
        self.app = app
        self.kind = 'auctions'
        self.scope = None
        self.loaded = False
        self.generation = 0
        self.info = tk.StringVar(value='竞拍界面已就绪，等待服务端接入。')
        self.keyword = tk.StringVar()
        self.asset_type = tk.StringVar(value='全部类型')
        self.status = tk.StringVar(value='全部状态')
        self.tables = []
        self.controls = []
        self.actions = []
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill='both', expand=True)
        self.build_list('拍卖列表', (
            ('id', '拍卖编号', 90), ('type', '类型', 60), ('name', '名称', 140),
            ('seller', '卖家', 100), ('buyer', '买家', 100), ('price', '当前价 P', 90),
            ('buyout', '一口价 P', 90), ('status', '状态', 80), ('expires', '截止时间', 150)),
            filters=True, actions=('查看详情', '申请撤回'))
        self.build_list('出价记录', (
            ('id', '出价编号', 90), ('listing', '拍卖编号', 90), ('bidder', '出价角色', 100),
            ('amount', '报价 P', 90), ('charged', '本次扣款 P', 100),
            ('status', '状态', 100), ('created', '出价时间', 150)))
        self.build_list('待领取', (
            ('id', '领取编号', 90), ('listing', '拍卖编号', 90), ('recipient', '领取角色', 100),
            ('kind', '用途', 110), ('asset', '资产 / 金额', 140), ('status', '状态', 90),
            ('created', '生成时间', 150)), actions=('查看详情',))
        self.build_list('资金流水', (
            ('id', '流水编号', 90), ('listing', '拍卖编号', 90), ('character', '角色', 100),
            ('kind', '业务类型', 110), ('amount', '金额 P', 90), ('operation', '操作编号', 150),
            ('created', '发生时间', 150)))
        self.build_settings()
        self.build_list('管理操作', (
            ('id', '操作编号', 110), ('action', '动作', 100), ('target', '拍卖编号', 90),
            ('operator', '操作人', 100), ('reason', '原因', 160), ('status', '执行状态', 100),
            ('result', '执行结果', 160), ('created', '提交时间', 150)))
        ttk.Label(self, textvariable=self.info, wraplength=780).pack(anchor='w', pady=(8, 0))
        self.tabs.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.set_enabled(False)

    def build_list(self, title, columns, filters=False, actions=()):
        page = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(page, text=title)
        toolbar = ttk.Frame(page)
        toolbar.pack(fill='x', pady=(0, 8))
        if filters:
            for variable, values, width in (
                    (self.asset_type, ('全部类型', '宠物', '道具'), 10),
                    (self.status, ('全部状态', '在售', '成交', '流拍', '撤拍'), 10)):
                box = ttk.Combobox(toolbar, textvariable=variable, values=values, width=width, state='readonly')
                box.pack(side='left', padx=(0, 8))
                self.controls.append((box, 'readonly'))
            search = ttk.Entry(toolbar, textvariable=self.keyword, width=24)
            search.pack(side='left', fill='x', expand=True)
            search.bind('<Return>', lambda event: self.search())
            self.controls.append((search, 'normal'))
        else:
            ttk.Label(toolbar, text='拍卖编号 / 角色').pack(side='left', padx=(0, 8))
            search = ttk.Entry(toolbar, width=28)
            search.pack(side='left', fill='x', expand=True)
            search.bind('<Return>', lambda event: self.search())
            self.controls.append((search, 'normal'))
        refresh = ttk.Button(toolbar, text='刷新', command=self.search)
        refresh.pack(side='right', padx=(8, 0))
        self.controls.append((refresh, 'normal'))
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
        for caption in actions:
            button = ttk.Button(footer, text=caption, state='disabled')
            button.pack(side='left', padx=(0, 8))
            self.actions.append(button)
        ttk.Label(footer, text='暂无数据').pack(side='left', padx=8)
        for caption in ('上一页', '下一页'):
            button = ttk.Button(footer, text=caption, state='disabled')
            button.pack(side='right', padx=(8, 0))
            self.actions.append(button)
        return page

    def build_settings(self):
        page = ttk.Frame(self.tabs, padding=16)
        self.tabs.add(page, text='竞拍设置')
        self.enabled = tk.BooleanVar(value=False)
        toggle = ttk.Checkbutton(page, text='允许新上架和出价', variable=self.enabled)
        toggle.grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 12))
        self.controls.append((toggle, 'normal'))
        self.settings = {}
        for row, (key, caption, value) in enumerate((
                ('listing_limit', '每角色同时在售上限', '5'),
                ('minimum_increment', '最小加价 P', '1'),
                ('listing_fee', '上架费 P', '0'),
                ('sale_fee', '成交费 P', '0')), start=1):
            ttk.Label(page, text=caption).grid(row=row, column=0, sticky='w', pady=6, padx=(0, 16))
            variable = tk.StringVar(value=value)
            entry = ttk.Entry(page, textvariable=variable, width=16)
            entry.grid(row=row, column=1, sticky='w', pady=6)
            self.settings[key] = variable
            self.controls.append((entry, 'normal'))
        ttk.Label(page, text='可选上架时长：4 / 8 / 12 小时').grid(row=5, column=0, columnspan=2, sticky='w', pady=(16, 8))
        ttk.Label(page, text='配置修改仅应用于后续上架。暂停新交易后，已有拍卖仍需结算和领取。',
                  wraplength=620).grid(row=6, column=0, columnspan=3, sticky='w', pady=8)
        button = ttk.Button(page, text='提交设置', state='disabled')
        button.grid(row=7, column=0, sticky='w', pady=12)
        self.actions.append(button)
        ttk.Label(page, text='当前展示规划默认值；接入服务端后读取实际配置。',
                  wraplength=620).grid(row=8, column=0, columnspan=3, sticky='w')

    def set_enabled(self, enabled):
        for control, state in self.controls:
            control.configure(state=state if enabled else 'disabled')
        for action in self.actions:
            action.configure(state='disabled')

    def search(self):
        if not self.app.connected or self.app.busy:
            return
        self.loaded = True
        self.info.set('竞拍界面已就绪，等待服务端接入；当前未读取或修改拍卖数据。')

    def tab_changed(self, event=None):
        self.search()

    def clear_results(self):
        self.generation += 1
        self.loaded = False
        for tree in self.tables:
            tree.delete(*tree.get_children())
