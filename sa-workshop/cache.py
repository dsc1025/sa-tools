from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from connection import QueryFailure


CACHED_KINDS = frozenset(('pet_templates', 'enemy_templates', 'enemy_groups',
                          'encounter_areas', 'pet_capture_requirements',
                          'item_templates', 'skills'))


def encode(value):
    if isinstance(value, bytes):
        return {'__type__': 'bytes', 'value': value.hex()}
    if isinstance(value, (datetime, date)):
        return {'__type__': type(value).__name__, 'value': value.isoformat()}
    if isinstance(value, Decimal):
        return {'__type__': 'decimal', 'value': str(value)}
    if isinstance(value, timedelta):
        return {'__type__': 'timedelta', 'value': [value.days, value.seconds, value.microseconds]}
    raise TypeError(f'无法缓存数据类型：{type(value).__name__}')


def decode(value):
    kind = value.get('__type__')
    if kind == 'bytes':
        return bytes.fromhex(value['value'])
    if kind == 'datetime':
        return datetime.fromisoformat(value['value'])
    if kind == 'date':
        return date.fromisoformat(value['value'])
    if kind == 'decimal':
        return Decimal(value['value'])
    if kind == 'timedelta':
        return timedelta(*value['value'])
    return value


class CatalogCache:
    def __init__(self, profile):
        source = [profile.ssh_host, profile.ssh_port, profile.ssh_user,
                  profile.mysql_host, profile.mysql_port, profile.mysql_user, profile.database]
        identity = hashlib.sha256(json.dumps(source).encode('utf-8')).hexdigest()
        self.directory = Path(__file__).resolve().parent / 'cache' / identity

    def read(self, kind):
        try:
            data = json.loads((self.directory / f'{kind}.json').read_text(encoding='utf-8'),
                              object_hook=decode)
            if isinstance(data, dict) and data.get('version') == 1 and isinstance(data.get('entries'), dict):
                return data['entries']
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            pass
        return {}

    def result(self, kind, key, fetch, refresh=False):
        entries = self.read(kind)
        key = json.dumps(key, ensure_ascii=False, sort_keys=True)
        if not refresh and key in entries:
            return entries[key]
        result = fetch()
        if refresh:
            entries = {}
        entries[key] = result
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                             dir=self.directory, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({'version': 1, 'entries': entries}, stream,
                          default=encode, ensure_ascii=False, indent=2)
            os.replace(temporary, self.directory / f'{kind}.json')
        except (OSError, TypeError, ValueError):
            raise QueryFailure('数据已读取，但本地缓存保存失败，请检查项目缓存目录的权限和可用空间。') from None
        finally:
            if temporary and temporary.exists():
                temporary.unlink()
        return result
