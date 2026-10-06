from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Profile:
    name: str = '新服务器'
    ssh_host: str = ''
    ssh_port: int = 22
    ssh_user: str = ''
    auth: str = 'password'
    key_path: str = ''
    known_hosts: str = ''
    mysql_host: str = '127.0.0.1'
    mysql_port: int = 3306
    mysql_user: str = ''
    database: str = 'sa'
    ssh_secret: str = ''
    mysql_password: str = ''

    def validate(self):
        for label, value in (('配置名称', self.name), ('SSH 地址', self.ssh_host),
                             ('SSH 用户', self.ssh_user), ('MySQL 地址', self.mysql_host),
                             ('MySQL 用户', self.mysql_user), ('数据库名称', self.database)):
            if not value.strip():
                raise ValueError(f'请填写{label}。')
        if not 1 <= self.ssh_port <= 65535 or not 1 <= self.mysql_port <= 65535:
            raise ValueError('端口必须在 1～65535 之间。')
        if self.auth not in ('password', 'key'):
            raise ValueError('SSH 认证方式无效。')
        if self.auth == 'key' and not Path(self.key_path).is_file():
            raise ValueError('请选择有效的 SSH 私钥文件。')
        if self.known_hosts and not Path(self.known_hosts).is_file():
            raise ValueError('主机密钥记录文件不存在。')


class Settings:
    def __init__(self):
        self.path = Path(__file__).resolve().parent / 'config.json'
        self.draft = None
        self.selected = -1

    def load(self):
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            self.draft = data.get('draft')
            self.selected = data.get('selected', -1)
            data = data.get('profiles', [])
        if not isinstance(data, list):
            raise ValueError('连接配置格式错误。')
        return [Profile(**entry) for entry in data]

    def save(self, profiles, draft=None, selected=-1):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'profiles': [asdict(profile) for profile in profiles],
                           'draft': draft, 'selected': selected},
                          stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
