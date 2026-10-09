from __future__ import annotations

from dataclasses import dataclass
import re

from connection import QueryFailure
from cache import CACHED_KINDS, CatalogCache


PET_TEMPLATE_FIELDS = {
    'name': ('宠物默认名称。', None, None),
    'initnum': ('初始能力计算倍率。', 0, 2147483647 // 265),
    'lvuppoint': ('等级成长倍率，不是可分配点数。', 0, 2147483647 // 265),
    'basevital': ('体力基础成长参数，0～255。', 0, 255),
    'basestr': ('腕力基础成长参数，0～255。', 0, 255),
    'basetgh': ('耐力基础成长参数，0～255。', 0, 255),
    'basedex': ('速度基础成长参数，0～255。', 0, 255),
    'baseper': ('隐藏感知成长参数，0表示不参与随机成长分配。', 0, 255),
    'modai': ('忠诚计算系数，同等条件下越大忠诚越低。', -2147483648, 2147483647),
    'get': ('捕捉基础加成，不是捕捉成功率。', -2147483648, 2147483647),
    'earthat': ('地属性原始值。', -2147483648, 2147483647),
    'waterat': ('水属性原始值。', -2147483648, 2147483647),
    'fireat': ('火属性原始值。', -2147483648, 2147483647),
    'windat': ('风属性原始值。', -2147483648, 2147483647),
    'poison': ('毒抗性修正。', -2147483648, 2147483647),
    'paralysis': ('麻痹抗性修正。', -2147483648, 2147483647),
    'sleep': ('睡眠抗性修正。', -2147483648, 2147483647),
    'stone': ('石化抗性修正。', -2147483648, 2147483647),
    'drunk': ('酒醉抗性修正。', -2147483648, 2147483647),
    'confusion': ('混乱抗性修正。', -2147483648, 2147483647),
    'rare': ('稀有度分类，参与出售价格等计算。', -2147483648, 2147483647),
    'slot': ('技能槽数，0～7。', 0, 7),
    'imgnumber': ('基础形象编号，需对应有效资源。', 0, 2147483647),
    'size': ('体型分类：0普通，1大型。', 0, 1),
    'limitlevel': ('单独等级限制；正数启用，仍受全局等级上限约束。', -2147483648, 2147483647),
}

PET_TEMPLATE_FIELDS.update({
    f'pet_skill_id{index}': ('默认技能统一编号：0为空，NULL使用旧编号。', 0, 2147483647)
    for index in range(1, 8)
})


PAGE_SIZE = 50
CHARACTER_STATS = (b'vi', b'str', b'tou', b'dx')
CHARACTER_FIELDS = (
    ((b'name', '名称'), (b'lv', '等级'), (b'nexp', '经验'), (b'trn', '转生次数'),
     (b'memberpoint', '会员积分'), (b'vipride', '会员标记'), (b'gld', '金币'),
     (b'bankgld', '银行金币'), (b'hp', '当前生命'), (b'mp', '当前魔力'),
     (b'bi', '当前形象编号'), (b'bbi', '基础形象编号'), (b'fb', '头像编号'),
     (b'ownt', '自定义称号'), (b'ieqt', '佩戴称号槽位'), (b'duel', 'DP（决斗积分）')),
    ((b'vi', '体力'), (b'str', '腕力'), (b'tou', '耐力'), (b'dx', '速度'),
     (b'perception', '感知'), (b'chr', '魅力'), (b'luc', '运气'),
     (b'skup', '剩余属性点'), (b'mmp', '最大魔力'),
     (b'aea', '地属性'), (b'awa', '水属性'), (b'afi', '火属性'), (b'awi', '风属性')),
)


def character_value(row):
    if row['field_key'] in CHARACTER_STATS:
        try:
            raw = int(row['field_value'])
            return str(raw // 100 if raw >= 0 else -(-raw // 100))
        except (ValueError, TypeError):
            pass
    return display(row['field_value'])


def character_points(original, values):
    rows = {}
    for row in original['attributes']:
        if row['field_key'] in (*CHARACTER_STATS, b'skup'):
            if row['field_key'] in rows:
                raise ValueError('属性记录重复，请先检查存档。')
            rows[row['field_key']] = row
    if len(rows) != 5:
        raise ValueError('四项属性或剩余属性点记录缺失，无法分配点数。')
    remaining = int(rows[b'skup']['field_value'])
    for key in CHARACTER_STATS:
        row = rows[key]
        raw = int(row['field_value'])
        text = values.get(row['ordinal'], character_value(row))
        if raw < 0 or not re.fullmatch(r'[0-9]+', text):
            raise ValueError('四项属性必须为非负整数点数。')
        delta = int(text) - raw // 100
        if not 0 <= raw + delta * 100 < (1 << 31):
            raise ValueError('属性超出服务器可保存的范围。')
        remaining -= delta
    if not 0 <= remaining < (1 << 31):
        raise ValueError('剩余属性点不足，请减少分配或先从其他属性退回点数。')
    return rows[b'skup'], remaining


def display(value, escaped=False):
    if value is None:
        return 'NULL'
    if isinstance(value, bytes):
        raw = value
        if escaped:
            result = bytearray()
            index = 0
            escapes = {ord('n'): 10, ord('c'): 44, ord('z'): 124, ord('y'): 92}
            while index < len(value):
                byte = value[index]
                if byte >= 128 and index + 1 < len(value):
                    result.extend(value[index:index + 2])
                    index += 2
                    continue
                if byte == 92 and index + 1 < len(value):
                    index += 1
                    byte = escapes.get(value[index], value[index])
                result.append(byte)
                index += 1
            value = bytes(result)
        try:
            return value.decode('gbk')
        except UnicodeDecodeError:
            return f'无法按 GBK 解码；原始字节：{raw.hex(" ")}'
    return str(value)


def escaped_bytes(text):
    value = text.encode('gbk')
    result = bytearray()
    index = 0
    escapes = {10: b'\\n', 44: b'\\c', 124: b'\\z', 92: b'\\y'}
    while index < len(value):
        byte = value[index]
        if byte >= 128 and index + 1 < len(value):
            result.extend(value[index:index + 2])
            index += 2
        else:
            result.extend(escapes.get(byte, bytes([byte])))
            index += 1
    return bytes(result)


@dataclass(frozen=True)
class Catalog:
    source: str
    select: str
    key: str
    columns: tuple
    search: tuple
    binary: bool = False
    escaped: bool = False
    filters: tuple = ()


def instance_catalog(kind):
    name_key = 'name' if kind == 'pet' else 'na'
    name = f"(SELECT field_value FROM {kind}_attributes WHERE instance_id=p.id AND field_key='{name_key}' ORDER BY ordinal LIMIT 1)"
    if kind == 'pet':
        nickname = "(SELECT field_value FROM pet_attributes WHERE instance_id=p.id AND field_key='ownt' ORDER BY ordinal LIMIT 1)"
        name = f"COALESCE(NULLIF({nickname},''),{name})"
    level = f"(SELECT numeric_value FROM {kind}_attributes WHERE instance_id=p.id AND field_key='lv' ORDER BY ordinal LIMIT 1)"
    identity = 'template_instance_id' if kind == 'pet' else 'id'
    columns = ((identity, '实例编号'), ('name', '名称'))
    if kind != 'pet':
        columns += (('owner', '所属角色'),)
    columns += (('container', '位置'), ('slot', '槽位'), ('level', '等级'), ('unique_code', '唯一编号'))
    return Catalog(
        f'{kind}_instances p LEFT JOIN characters c ON c.id=p.character_id',
        f'p.id,p.unique_code,p.character_id,c.name AS owner,p.container,p.slot,{name} AS name,{level} AS level'
        + (",(SELECT numeric_value FROM pet_attributes WHERE instance_id=p.id "
           "AND field_key='source_instance_id' ORDER BY ordinal LIMIT 1) AS template_instance_id"
           if kind == 'pet' else ''),
        'p.id', columns,
        (name, 'c.name', 'p.unique_code'), True, True,
        (('id', 'p.id'), ('character_id', 'p.character_id')))


def ride_catalog(config_type, columns):
    return Catalog(
        f"(SELECT * FROM ride_configurations WHERE config_type='{config_type}') r",
        '(r.entry_index * 64 + r.slot_index) AS id,r.entry_index,r.slot_index,r.value1,r.value2,r.value3,r.value4',
        '(r.entry_index * 64 + r.slot_index)', columns,
        tuple(f'CAST(r.{field} AS CHAR)' for field in ('entry_index', 'slot_index', 'value1', 'value2', 'value3', 'value4')))


CATALOGS = {
    'ride_base': ride_catalog('base',
        (('entry_index', '记录序号'), ('value1', '骑乘形象'), ('value2', '人物形象'),
         ('value3', '宠物形象'), ('value4', '旧基板编号'))),
    'ride_pet': ride_catalog('pet',
        (('entry_index', '许可槽位'), ('value1', '宠物形象'), ('value2', '备用宠物形象'))),
    'ride_image': ride_catalog('image',
        (('entry_index', '人物类型'), ('slot_index', '骑乘槽位'), ('value1', '骑乘形象'))),
    'ride_player': ride_catalog('player',
        (('entry_index', '记录序号'), ('value1', '人物基础形象'), ('value2', '人物类型'), ('value3', '原性别码'))),
    'ride_leader': ride_catalog('leader',
        (('entry_index', '记录序号'), ('value1', '庄园楼层'), ('value2', '族长许可槽位'),
         ('value3', '长老许可槽位'), ('value4', '族员许可槽位'))),
    'accounts': Catalog(
        "accounts a LEFT JOIN account_permissions g ON g.account_id=a.id AND g.permission='gm.level' "
        'LEFT JOIN login_sessions s ON s.account_id=a.id',
        'a.id,a.username,a.enabled,a.created_at,COALESCE(g.level,0) AS gm_level,'
        '(s.account_id IS NOT NULL) AS online', 'a.id',
        (('id', '账户编号'), ('username', '账号'), ('enabled', '启用'), ('gm_level', 'GM等级'),
         ('online', '有登录会话'), ('created_at', '创建时间')), ('a.username',), True,
        filters=(('id', 'a.id'),)),
    'characters': Catalog(
        'character_summary c JOIN accounts a ON a.id=c.account_id',
        'c.id,c.account_id,a.username,c.name,c.slot,c.level,c.gold,c.bank_gold,c.hp,'
        'c.map_id,c.x,c.y,c.revision,c.saved_at,'
        "(SELECT numeric_value FROM character_attributes WHERE character_id=c.id AND field_key='memberpoint' ORDER BY ordinal LIMIT 1) AS memberpoint", 'c.id',
        (('name', '名称'), ('level', '等级'), ('memberpoint', '会员积分'), ('saved_at', '存档时间')),
        ('c.name', 'a.username'), True, filters=(('id', 'c.id'), ('account_id', 'c.account_id'))),
    'pets': instance_catalog('pet'),
    'items': instance_catalog('item'),
    'pet_templates': Catalog(
        'pet_base_templates p', 'p.tempno AS id,p.name,p.imgnumber,p.slot,p.limitlevel', 'p.tempno',
        (('id', '模板编号'), ('name', '名称'), ('imgnumber', '形象编号'),
         ('slot', '技能槽数'), ('limitlevel', '等级上限')), ('p.name',), filters=(('id', 'p.tempno'),)),
    'enemy_templates': Catalog(
        'enemy_templates e LEFT JOIN pet_base_templates p ON p.tempno=e.tempno',
        'e.id,e.name,e.tempno,p.name AS base_name,e.lv_min,e.lv_max,e.exp', 'e.id',
        (('id', '实例编号'), ('name', '名称'), ('tempno', '基板编号'), ('base_name', '基板名称'),
         ('lv_min', '最低等级'), ('lv_max', '最高等级'), ('exp', '经验')),
        ('e.name', 'p.name', 'CAST(e.tempno AS CHAR)'), filters=(('id', 'e.id'),)),
    'enemy_groups': Catalog(
        'enemy_groups g', 'g.id,g.name,g.enemy_id1,g.enemy_id2,g.enemy_id3,g.appearbyitemid,g.notappearbyitemid',
        'g.id', (('id', '组合编号'), ('name', '名称'), ('enemy_id1', '实例 1'),
                 ('enemy_id2', '实例 2'), ('enemy_id3', '实例 3'),
                 ('appearbyitemid', '出现道具'), ('notappearbyitemid', '排除道具')),
        ('g.name',) + tuple(f'CAST(g.enemy_id{index} AS CHAR)' for index in range(1, 11)),
        filters=(('id', 'g.id'),)),
    'encounter_areas': Catalog(
        'encounter_areas e', 'e.source_order AS id,e.id AS area_id,e.floor,e.x1,e.y1,e.x2,e.y2,e.enemymaxnum',
        'e.source_order', (('id', '记录序号'), ('area_id', '区域编号'), ('floor', '地图'),
                          ('x1', '起点 X'), ('y1', '起点 Y'), ('x2', '终点 X'), ('y2', '终点 Y'),
                          ('enemymaxnum', '实例数上限')),
        ('CAST(e.id AS CHAR)', 'CAST(e.floor AS CHAR)'), filters=(('id', 'e.source_order'),)),
    'pet_capture_requirements': Catalog(
        'pet_capture_requirements r LEFT JOIN pet_base_templates p ON p.tempno=r.pet_id',
        'r.pet_id AS id,p.name,r.item_id1,r.item_id2,r.item_id3', 'r.pet_id',
        (('id', '基板编号'), ('name', '宠物名称'), ('item_id1', '道具 1'),
         ('item_id2', '道具 2'), ('item_id3', '道具 3')),
        ('p.name',) + tuple(f'CAST(r.item_id{index} AS CHAR)' for index in range(1, 16)),
        filters=(('id', 'r.pet_id'),)),
    'item_templates': Catalog(
        'item_templates p', 'p.id,p.name,p.type,p.cost,p.imagenumber,p.skill_id,p.pet_skill_id', 'p.id',
        (('id', '模板编号'), ('name', '名称'), ('type', '类型'), ('cost', '价格'),
         ('imagenumber', '图像编号'), ('skill_id', '魔法统一编号'), ('pet_skill_id', '宠技统一编号')),
        ('p.name', 'p.secretname'), filters=(('id', 'p.id'),)),
    'skills': Catalog(
        'skill_definitions s', 's.skill_id AS id,s.name,s.adapter,s.legacy_id,s.enabled,s.function_name',
        's.skill_id', (('id', '统一编号'), ('name', '名称'), ('adapter', '适配器'),
                       ('legacy_id', '旧编号'), ('enabled', '启用'), ('function_name', '处理函数')),
        ('s.name', 's.skill_key', 's.function_name'), filters=(('id', 's.skill_id'),)),
}


def cell(row, key, catalog):
    return display(row.get(key))


class Repository:
    def __init__(self, connection):
        self.connection = connection
        self.cache = None

    def configure_cache(self, profile):
        self.cache = CatalogCache(profile)

    def account(self, identity, locked=False):
        suffix = ' FOR UPDATE' if locked else ''
        rows = self.connection.query(
            'SELECT id,username,enabled,created_at FROM accounts WHERE id=%s LIMIT 1' + suffix, (identity,))
        if not rows:
            raise QueryFailure('账户已不存在，请刷新列表。')
        permissions = self.connection.query(
            "SELECT level FROM account_permissions WHERE account_id=%s AND permission='gm.level' LIMIT 1" + suffix,
            (identity,))
        return {**rows[0], 'gm_level': permissions[0]['level'] if permissions else None}

    def account_form(self, identity):
        with self.connection.snapshot():
            return self.account(identity)

    def save_account(self, original, enabled, gm_level):
        if type(enabled) is not int or enabled not in (0, 1):
            raise ValueError('启用状态必须为0或1。')
        if gm_level is not None and (type(gm_level) is not int or not 0 <= gm_level <= 4):
            raise ValueError('GM等级必须在0到4之间。')
        if gm_level is None and original['gm_level'] is not None:
            raise ValueError('不能将已有GM等级改为空值。')
        identity = original['id']
        with self.connection.transaction():
            current = self.account(identity, locked=True)
            if current != original:
                raise QueryFailure('账户已被其他操作修改，请关闭表单并重新打开后再保存。')
            if enabled != current['enabled']:
                self.connection.write('UPDATE accounts SET enabled=%s WHERE id=%s', (enabled, identity))
            if gm_level != current['gm_level']:
                if current['gm_level'] is None:
                    self.connection.write(
                        'INSERT INTO account_permissions(account_id,permission,level) VALUES(%s,%s,%s)',
                        (identity, 'gm.level', gm_level))
                else:
                    self.connection.write(
                        "UPDATE account_permissions SET level=%s WHERE account_id=%s AND permission='gm.level'",
                        (gm_level, identity))
            result = self.account(identity)
        return result

    def player_record(self, kind, identity, locked=False):
        suffix = ' FOR UPDATE' if locked else ''
        if kind == 'characters':
            rows = self.connection.query(
                'SELECT id,account_id,slot,name,revision,saved_at FROM characters WHERE id=%s LIMIT 1' + suffix,
                (identity,))
            if not rows:
                raise QueryFailure('角色已不存在，请刷新列表。')
            return {'character': rows[0], 'instance': None}
        raise ValueError('只有角色支持属性编辑。')

    def player_attributes(self, kind, identity, locked=False):
        if kind != 'characters':
            raise ValueError('只有角色支持属性编辑。')
        keys = tuple(key for group in CHARACTER_FIELDS for key, label in group)
        placeholders = ','.join('%s' for key in keys)
        return self.connection.query(
            'SELECT ordinal,field_key,field_value,numeric_value FROM character_attributes '
            f'WHERE character_id=%s AND field_key IN ({placeholders}) '
            f'ORDER BY FIELD(field_key,{placeholders}),ordinal' + (' FOR UPDATE' if locked else ''),
            (identity, *keys, *keys))

    def player_form(self, kind, identity):
        if kind != 'characters':
            raise ValueError('无效的编辑记录。')
        with self.connection.snapshot():
            record = self.player_record(kind, identity)
            attributes = self.player_attributes(kind, identity)
        return {'kind': kind, 'id': identity, **record, 'attributes': attributes}

    @staticmethod
    def attribute_editable(kind, row):
        key = row['field_key']
        if key in (b'lv', b'trn', b'skup'):
            return False
        if kind != 'characters' or key not in dict(field for group in CHARACTER_FIELDS for field in group):
            return False
        if key in (b'name', b'ownt'):
            return True
        return row['numeric_value'] is not None

    def save_player(self, original, values):
        kind, identity = original['kind'], original['id']
        if kind != 'characters':
            raise ValueError('只有角色支持属性编辑。')
        labels = dict(field for group in CHARACTER_FIELDS for field in group)
        changes = []
        for row in original['attributes']:
            ordinal = row['ordinal']
            if ordinal not in values:
                continue
            text = values[ordinal]
            if text == character_value(row):
                continue
            if row['field_key'] in CHARACTER_STATS:
                if not re.fullmatch(r'[0-9]+', text):
                    raise ValueError(f'{labels[row["field_key"]]}必须为非负整数点数。')
                raw = int(row['field_value'])
                text = str(raw + (int(text) - raw // 100) * 100)
            try:
                value = text.encode('gbk')
            except UnicodeEncodeError:
                raise ValueError(f'{labels[row["field_key"]]}包含GBK无法保存的字符。') from None
            if value == row['field_value']:
                continue
            if not self.attribute_editable(kind, row):
                raise ValueError('该字段不允许修改。')
            key = row['field_key']
            numeric = None
            if key in (b'name', b'ownt'):
                if any(char in text for char in ('|', '\n', '\r', '\x00')):
                    raise ValueError('名称中的分隔符和换行须使用存档转义格式。')
                if len(display(value, escaped=True).encode('gbk')) > 63:
                    raise ValueError('名称最多63个GBK字节。')
                if kind == 'characters' and key == b'name' and not value:
                    raise ValueError('角色名称不能为空。')
                if re.fullmatch(r'[+-]?[0-9]+', text):
                    number = int(text)
                    if -(1 << 63) <= number < (1 << 63):
                        numeric = number
            else:
                if not re.fullmatch(r'[+-]?[0-9]+', text):
                    raise ValueError(f'{labels[key]}必须为整数。')
                numeric = int(text)
                if not -(1 << 31) <= numeric < (1 << 31):
                    raise ValueError(f'{labels[key]}超出服务器可保存的范围。')
            changes.append((row, value, numeric))
        if not changes:
            return original
        if any(row['field_key'] in CHARACTER_STATS for row, value, numeric in changes):
            points, remaining = character_points(original, values)
            if remaining != int(points['field_value']):
                changes.append((points, str(remaining).encode('ascii'), remaining))
        with self.connection.transaction():
            account_id = original['character']['account_id']
            accounts = self.connection.query('SELECT id FROM accounts WHERE id=%s LIMIT 1 FOR UPDATE', (account_id,))
            if not accounts:
                raise QueryFailure('所属账户已不存在。')
            sessions = self.connection.query('SELECT account_id FROM login_sessions WHERE account_id=%s LIMIT 1 FOR UPDATE', (account_id,))
            if sessions:
                raise QueryFailure('所属账户有登录会话，请退出游戏后再保存。')
            current = self.player_record(kind, identity, locked=True)
            attributes = self.player_attributes(kind, identity, locked=True)
            if current != {'character': original['character'], 'instance': original['instance']} or attributes != original['attributes']:
                raise QueryFailure('记录已被其他操作修改，请重新打开表单。')
            for row, value, numeric in changes:
                self.connection.write(
                    'UPDATE character_attributes SET field_value=%s,numeric_value=%s WHERE character_id=%s AND ordinal=%s',
                    (value, numeric, identity, row['ordinal']))
                if kind == 'characters' and row['field_key'] == b'name':
                    self.connection.write('UPDATE characters SET name=%s WHERE id=%s',
                                          (display(value, escaped=True).encode('gbk'), identity))
            self.connection.write('UPDATE characters SET revision=revision+1,saved_at=CURRENT_TIMESTAMP(6) WHERE id=%s',
                                  (original['character']['id'],))
            record = self.player_record(kind, identity)
            attributes = self.player_attributes(kind, identity)
        return {'kind': kind, 'id': identity, **record, 'attributes': attributes}

    def page(self, kind, keyword, page=0, scope=None, refresh=False):
        if self.cache is not None and kind in CACHED_KINDS:
            key = ['page', repr(CATALOGS[kind]), keyword.strip(), page, scope]
            return self.cache.result(kind, key,
                                     lambda: self.query_page(kind, keyword, page, scope), refresh)
        return self.query_page(kind, keyword, page, scope)

    def query_page(self, kind, keyword, page=0, scope=None):
        catalog = CATALOGS[kind]
        conditions, parameters = [], []
        keyword = keyword.strip()
        if len(keyword) > 200:
            raise ValueError('搜索内容最多200个字符。')
        if keyword:
            try:
                needle = keyword.encode('gbk') if catalog.binary else keyword
                stored = escaped_bytes(keyword) if catalog.escaped else needle
            except UnicodeEncodeError:
                raise ValueError('玩家存档搜索内容包含 GBK 无法表示的字符。') from None
            matches = [f'LOCATE(%s,{field}) > 0' for field in catalog.search]
            parameters.extend(stored if index == 0 and catalog.escaped else needle
                              for index in range(len(catalog.search)))
            if keyword.isascii() and keyword.isdigit() and len(keyword) <= 20:
                number = int(keyword)
                if number <= 18446744073709551615:
                    matches.append(f'{catalog.key}=%s')
                    parameters.append(number)
            conditions.append('(' + ' OR '.join(matches) + ')')
        if scope:
            field, number = scope
            column = dict(catalog.filters)[field]
            conditions.append(f'{column}=%s')
            parameters.append(number)
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        with self.connection.snapshot():
            sql = f'SELECT {catalog.select} FROM {catalog.source}{where} ORDER BY {catalog.key}'
            if page is None:
                rows = self.connection.query(sql, parameters)
                total = len(rows)
            else:
                total = self.connection.query(f'SELECT COUNT(*) AS total FROM {catalog.source}{where}', parameters)[0]['total']
                page = min(max(0, page), max(0, (total - 1) // PAGE_SIZE))
                rows = self.connection.query(sql + ' LIMIT %s OFFSET %s',
                                             (*parameters, PAGE_SIZE, page * PAGE_SIZE))
        return {'rows': rows, 'total': total, 'page': page}

    def pet_template_form(self, identity):
        with self.connection.snapshot():
            rows = self.connection.query('SELECT * FROM pet_base_templates WHERE tempno=%s LIMIT 1', (identity,))
            skills = self.connection.query(
                "SELECT skill_id,name,adapter FROM skill_definitions WHERE enabled=1 "
                "AND legacy_id IS NOT NULL AND legacy_id>=0 AND ((adapter='pet' AND legacy_id<1073741824) "
                "OR (adapter='magic' AND skill_id<1073741824 AND legacy_id<=65535 "
                "AND field_type<>2 "
                "AND ((function_name='MAGIC_AttMagic' AND dead_target=0 AND target_type IN (1,4,8,9,10,11)) "
                "OR (function_name<>'MAGIC_AttMagic' AND target_type IN (0,1,2,3,4,8,9) "
                "AND NOT (function_name='MAGIC_Recovery' AND target_type=4))) "
                "AND function_name IN ('MAGIC_AttMagic','MAGIC_Recovery','MAGIC_OtherRecovery',"
                "'MAGIC_StatusRecovery','MAGIC_Ressurect'))) ORDER BY skill_id")
        if not rows:
            raise QueryFailure('基板已不存在，请刷新列表。')
        return {'template': rows[0], 'skills': skills}

    def save_pet_template(self, original, values):
        changes = {}
        for key, text in values.items():
            if key not in PET_TEMPLATE_FIELDS or key not in original:
                raise ValueError('包含不可修改的基板字段。')
            if text == display(original[key]):
                continue
            meaning, minimum, maximum = PET_TEMPLATE_FIELDS[key]
            if key.startswith('pet_skill_id') and text == 'NULL':
                value = None
            elif key == 'name':
                if not text or chr(0) in text:
                    raise ValueError('名称不能为空或包含空字符。')
                try:
                    encoded = text.encode('gbk')
                except UnicodeEncodeError:
                    raise ValueError('名称包含服务端 GBK 不支持的字符。') from None
                if len(encoded) >= 64:
                    raise ValueError('名称按 GBK 编码后必须少于64字节。')
                value = encoded if isinstance(original[key], bytes) else text
            else:
                if not re.fullmatch(r'-?[0-9]+', text):
                    raise ValueError(f'{key} 必须为整数。')
                value = int(text)
                if not minimum <= value <= maximum:
                    raise ValueError(f'{key} 必须在 {minimum} 到 {maximum} 之间。')
            changes[key] = value
        with self.connection.transaction():
            rows = self.connection.query('SELECT * FROM pet_base_templates WHERE tempno=%s LIMIT 1 FOR UPDATE',
                                         (original['tempno'],))
            if not rows or rows[0] != original:
                raise QueryFailure('基板已被其他操作修改，请关闭表单并重新打开。')
            if {'initnum', 'lvuppoint'} & changes.keys():
                updated = {**rows[0], **changes}
                levels = self.connection.query(
                    'SELECT lv_min,lv_max FROM enemy_templates WHERE tempno=%s FOR UPDATE',
                    (original['tempno'],))
                max_level = max([1] + [int(row[key]) for row in levels
                                       for key in ('lv_min', 'lv_max')])
                # Growth is clamped to 255, then receives up to 10 random points.
                multiplier = (max_level - 1) * int(updated['lvuppoint']) + int(updated['initnum'])
                if (int(updated['initnum']) < 0 or int(updated['lvuppoint']) < 0
                        or multiplier > 2147483647 // 265):
                    raise ValueError('初始能力与等级成长倍率过大，按实例生成等级计算会导致属性溢出。')
            for key, value in changes.items():
                if key.startswith('pet_skill_id') and value is not None and value > 0:
                    skills = self.connection.query(
                        "SELECT skill_id FROM skill_definitions WHERE skill_id=%s AND enabled=1 "
                        "AND legacy_id IS NOT NULL AND legacy_id>=0 AND ((adapter='pet' AND legacy_id<1073741824) "
                        "OR (adapter='magic' AND skill_id<1073741824 AND legacy_id<=65535 "
                        "AND field_type<>2 "
                        "AND ((function_name='MAGIC_AttMagic' AND dead_target=0 AND target_type IN (1,4,8,9,10,11)) "
                "OR (function_name<>'MAGIC_AttMagic' AND target_type IN (0,1,2,3,4,8,9) "
                "AND NOT (function_name='MAGIC_Recovery' AND target_type=4))) "
                "AND function_name IN ('MAGIC_AttMagic','MAGIC_Recovery','MAGIC_OtherRecovery',"
                        "'MAGIC_StatusRecovery','MAGIC_Ressurect'))) LIMIT 1 FOR UPDATE",
                        (value,))
                    if not skills:
                        raise ValueError(f'{key} 必须选择已启用且可由宠物使用的技能统一编号。')
            if changes:
                assignments = ','.join(f'`{key}`=%s' for key in changes)
                self.connection.write(f'UPDATE pet_base_templates SET {assignments} WHERE tempno=%s',
                                      (*changes.values(), original['tempno']))
            result = {**rows[0], **changes}
        return result

    def enemy_template_form(self, identity):
        with self.connection.snapshot():
            rows = self.connection.query('SELECT * FROM enemy_templates WHERE id=%s LIMIT 1', (identity,))
        if not rows:
            raise QueryFailure('实例已不存在，请刷新列表。')
        return {'sections': [('实例字段', rows, False)], 'links': []}

    def save_enemy_template(self, original, values):
        changes = {}
        for key, text in values.items():
            if key not in ('name', 'lv_min', 'lv_max', 'createminnum', 'createmaxnum') or key not in original:
                raise ValueError('包含不可修改的实例字段。')
            if text == display(original[key]):
                continue
            if key == 'name':
                if chr(0) in text:
                    raise ValueError('名称不能包含空字符。')
                try:
                    encoded = text.encode('gbk')
                except UnicodeEncodeError:
                    raise ValueError('名称包含服务端 GBK 不支持的字符。') from None
                if len(encoded) >= 64:
                    raise ValueError('名称按 GBK 编码后必须少于64字节。')
                value = encoded if isinstance(original[key], bytes) else text
            else:
                if not re.fullmatch(r'[0-9]+', text):
                    raise ValueError(f'{key} 必须为非负整数。')
                value = int(text)
                if value > 2147483647:
                    raise ValueError(f'{key} 不能超过2147483647。')
            changes[key] = value
        with self.connection.transaction():
            bases = self.connection.query(
                'SELECT initnum,lvuppoint FROM pet_base_templates WHERE tempno=%s LIMIT 1 FOR UPDATE',
                (original['tempno'],))
            rows = self.connection.query('SELECT * FROM enemy_templates WHERE id=%s LIMIT 1 FOR UPDATE',
                                         (original['id'],))
            if not rows or rows[0] != original:
                raise QueryFailure('实例已被其他操作修改，请关闭表单并重新打开。')
            updated = {**rows[0], **changes}
            if {'lv_min', 'lv_max'} & changes.keys():
                low, high = int(updated['lv_min']), int(updated['lv_max'])
                if not 0 <= low <= high or high < 1:
                    raise ValueError('最高等级必须至少为1，最低等级不能大于最高等级。')
                if not bases:
                    raise ValueError('关联基板不存在，无法校验生成等级。')
                initial, growth = int(bases[0]['initnum']), int(bases[0]['lvuppoint'])
                if initial < 0 or growth < 0 or (high - 1) * growth + initial > 2147483647 // 265:
                    raise ValueError('等级过高，结合基板成长倍率会导致属性溢出。')
            if {'createminnum', 'createmaxnum'} & changes.keys():
                if not 0 <= int(updated['createminnum']) <= int(updated['createmaxnum']):
                    raise ValueError('最小生成数量不能大于最大生成数量，且不能为负数。')
            if changes:
                assignments = ','.join(f'`{key}`=%s' for key in changes)
                self.connection.write(f'UPDATE enemy_templates SET {assignments} WHERE id=%s',
                                      (*changes.values(), original['id']))
        return updated

    def detail(self, kind, identity):
        if self.cache is not None and kind in CACHED_KINDS:
            result = self.cache.result(kind, ['detail', repr(CATALOGS[kind]), identity],
                                       lambda: self.query_detail(kind, identity))
        else:
            result = self.query_detail(kind, identity)
        if kind == 'pet_templates':
            hidden = tuple(f'{prefix}{index}' for prefix in
                           ('atomfixname', 'atombaseadd', 'atomfixmin', 'atomfixmax')
                           for index in range(1, 6))
            result = {**result, 'sections': [
                (title, [{key: value for key, value in row.items() if key not in hidden}
                         for row in rows], attributes)
                for title, rows, attributes in result['sections']]}
        return result

    def query_detail(self, kind, identity):
        sections, links = [], []

        def section(title, sql, parameters=(identity,), attributes=False, limit=1):
            rows = self.connection.query(sql + ' LIMIT %s', (*parameters, limit))
            if limit > 1:
                title = f'{title}（最多{limit}条）'
            sections.append((title, rows, attributes))
            return rows

        with self.connection.snapshot():
            if kind in ('ride_base', 'ride_pet', 'ride_image', 'ride_player', 'ride_leader'):
                entry, slot = divmod(int(identity), 64)
                section('骑乘配置',
                        'SELECT config_type,entry_index,slot_index,value1,value2,value3,value4 '
                        'FROM ride_configurations WHERE config_type=%s AND entry_index=%s AND slot_index=%s',
                        (kind.removeprefix('ride_'), entry, slot))
            elif kind == 'accounts':
                rows = section('账户', 'SELECT id,username,enabled,created_at FROM accounts WHERE id=%s')
                section('权限', 'SELECT permission,level FROM account_permissions WHERE account_id=%s ORDER BY permission', limit=10)
                section('角色', 'SELECT id,slot,name,revision,saved_at FROM characters WHERE account_id=%s ORDER BY slot', limit=10)
                if rows:
                    section('账号封禁', "SELECT reason,expires_at,created_at FROM login_bans WHERE subject_type='account' AND subject=%s ORDER BY created_at DESC", (rows[0]['username'],), limit=10)
                links.append(('查看所属角色', 'characters', ('account_id', identity)))
            elif kind == 'characters':
                rows = section('角色', 'SELECT c.id,c.account_id,a.username,c.slot,c.name,c.revision,c.saved_at '
                               'FROM characters c JOIN accounts a ON a.id=c.account_id WHERE c.id=%s')
                section('属性', 'SELECT ordinal,field_key,field_value,numeric_value FROM character_attributes WHERE character_id=%s ORDER BY ordinal', attributes=True, limit=10)
                section('已学旧角色技能', 'SELECT slot,skill_id,level AS stored_level,level DIV 100 AS display_level FROM character_skills WHERE character_id=%s ORDER BY slot', limit=10)
                section('称号', 'SELECT slot,title_id FROM character_titles WHERE character_id=%s ORDER BY slot', limit=10)
                section('指令授权', 'SELECT command_value,remaining_uses FROM character_command_grants WHERE character_id=%s ORDER BY command_value', limit=10)
                if rows:
                    links.append(('查看账户', 'accounts', ('id', rows[0]['account_id'])))
                links.extend((('查看宠物', 'pets', ('character_id', identity)),
                              ('查看装备、背包和仓库', 'items', ('character_id', identity))))
            elif kind in ('pets', 'items'):
                prefix = 'pet' if kind == 'pets' else 'item'
                rows = section('归属', f'SELECT p.id,p.unique_code,p.character_id,c.name AS owner,p.container,p.slot,p.ordinal '
                               f'FROM {prefix}_instances p LEFT JOIN characters c ON c.id=p.character_id WHERE p.id=%s')
                attributes = section('实例属性', f'SELECT ordinal,field_key,field_value,numeric_value FROM {prefix}_attributes WHERE instance_id=%s ORDER BY ordinal', attributes=True, limit=10)
                if rows and rows[0]['character_id'] is not None:
                    links.append(('查看所属角色', 'characters', ('id', rows[0]['character_id'])))
                if kind == 'items':
                    template = next((row['numeric_value'] for row in attributes if row['field_key'] == b'id'), None)
                    if template is not None:
                        links.append(('查看物品模板', 'item_templates', ('id', int(template))))
                else:
                    skills = self.connection.query(
                        "SELECT a.field_key,a.numeric_value AS stored_id,s.skill_id,s.name,s.adapter,s.enabled "
                        "FROM pet_attributes a LEFT JOIN skill_definitions s ON "
                        "(a.numeric_value<1073741824 AND s.adapter='pet' AND s.legacy_id=a.numeric_value) OR "
                        "(a.numeric_value>1073741824 AND s.adapter='magic' AND s.skill_id=a.numeric_value-1073741824) "
                        "WHERE a.instance_id=%s AND CAST(a.field_key AS CHAR CHARACTER SET ascii) REGEXP '^psk[0-6]$' ORDER BY a.field_key LIMIT 10", (identity,))
                    sections.append(('宠技编号映射（最多10条）', skills, False))
                    growth_rows = self.connection.query(
                        "SELECT numeric_value FROM pet_attributes WHERE instance_id=%s AND field_key='pet_growth64' ORDER BY ordinal LIMIT 1", (identity,))
                    growth = growth_rows[0]['numeric_value'] if growth_rows else None
                    if growth is not None:
                        value = int(growth)
                        sections.append(('成长参数', [dict(zip(('体力', '力量', '耐力', '敏捷', '感知'),
                                             ((value >> shift) & 255 for shift in (24, 16, 8, 0, 32))))], False))
            elif kind in ('pet_templates', 'item_templates'):
                table, key = ('pet_base_templates', 'tempno') if kind == 'pet_templates' else ('item_templates', 'id')
                section('模板字段', f'SELECT * FROM {table} WHERE {key}=%s')
            elif kind in ('enemy_templates', 'enemy_groups', 'encounter_areas', 'pet_capture_requirements'):
                title, key = {
                    'enemy_templates': ('实例字段', 'id'),
                    'enemy_groups': ('实例组合字段', 'id'),
                    'encounter_areas': ('遇敌区域字段', 'source_order'),
                    'pet_capture_requirements': ('捕捉条件字段', 'pet_id'),
                }[kind]
                section(title, f'SELECT * FROM {kind} WHERE {key}=%s')
            elif kind == 'skills':
                section('技能字段', 'SELECT * FROM skill_definitions WHERE skill_id=%s')
                section('配置引用', 'SELECT * FROM skill_references WHERE skill_id=%s ORDER BY source_type,source_key,slot', limit=10)
        return {'sections': sections, 'links': links}
