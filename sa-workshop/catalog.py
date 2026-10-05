from __future__ import annotations

from dataclasses import dataclass


PAGE_SIZE = 50


def display(value, escaped=False):
    if value is None:
        return '—'
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
    level = f"(SELECT numeric_value FROM {kind}_attributes WHERE instance_id=p.id AND field_key='lv' ORDER BY ordinal LIMIT 1)"
    return Catalog(
        f'{kind}_instances p LEFT JOIN characters c ON c.id=p.character_id',
        f'p.id,p.unique_code,p.character_id,c.name AS owner,p.container,p.slot,{name} AS name,{level} AS level',
        'p.id', (('id', '实例编号'), ('name', '名称'), ('owner', '所属角色'),
                 ('container', '位置'), ('slot', '槽位'), ('level', '等级'), ('unique_code', '唯一编号')),
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
        'c.map_id,c.x,c.y,c.revision,c.saved_at', 'c.id',
        (('id', '角色编号'), ('name', '角色名称'), ('username', '账号'), ('slot', '角色槽位'),
         ('level', '等级'), ('gold', '金币'), ('bank_gold', '银行金币'), ('saved_at', '存档时间')),
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
        (('id', '敌人编号'), ('name', '名称'), ('tempno', '基板编号'), ('base_name', '基板名称'),
         ('lv_min', '最低等级'), ('lv_max', '最高等级'), ('exp', '经验')),
        ('e.name', 'p.name', 'CAST(e.tempno AS CHAR)'), filters=(('id', 'e.id'),)),
    'enemy_groups': Catalog(
        'enemy_groups g', 'g.id,g.name,g.enemy_id1,g.enemy_id2,g.enemy_id3,g.appearbyitemid,g.notappearbyitemid',
        'g.id', (('id', '组合编号'), ('name', '名称'), ('enemy_id1', '敌人 1'),
                 ('enemy_id2', '敌人 2'), ('enemy_id3', '敌人 3'),
                 ('appearbyitemid', '出现道具'), ('notappearbyitemid', '排除道具')),
        ('g.name',) + tuple(f'CAST(g.enemy_id{index} AS CHAR)' for index in range(1, 11)),
        filters=(('id', 'g.id'),)),
    'encounter_areas': Catalog(
        'encounter_areas e', 'e.source_order AS id,e.id AS area_id,e.floor,e.x1,e.y1,e.x2,e.y2,e.enemymaxnum',
        'e.source_order', (('id', '记录序号'), ('area_id', '区域编号'), ('floor', '地图'),
                          ('x1', '起点 X'), ('y1', '起点 Y'), ('x2', '终点 X'), ('y2', '终点 Y'),
                          ('enemymaxnum', '敌人数上限')),
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
    value = row.get(key)
    if key in ('enabled', 'online'):
        return '是' if value else '否'
    if key == 'slot' and value is not None and catalog is not CATALOGS['pet_templates']:
        return str(value + 1)
    if key == 'container':
        if row.get('character_id') is None:
            return '无归属'
        if value == 'warehouse':
            return '仓库'
        if value == 'carried':
            if catalog is CATALOGS['items']:
                return '装备' if row.get('slot') is not None and row['slot'] < 5 else '背包'
            return '随身'
    return display(value, escaped=catalog.escaped and key == 'name')


class Repository:
    def __init__(self, connection):
        self.connection = connection

    def page(self, kind, keyword, page=0, scope=None):
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

    def detail(self, kind, identity):
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
                        "SELECT a.field_key,a.numeric_value AS legacy_id,s.skill_id,s.name,s.enabled "
                        "FROM pet_attributes a LEFT JOIN skill_definitions s ON s.adapter='pet' AND s.legacy_id=a.numeric_value "
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
                    'enemy_templates': ('敌人字段', 'id'),
                    'enemy_groups': ('敌人组合字段', 'id'),
                    'encounter_areas': ('遇敌区域字段', 'source_order'),
                    'pet_capture_requirements': ('捕捉条件字段', 'pet_id'),
                }[kind]
                section(title, f'SELECT * FROM {kind} WHERE {key}=%s')
            elif kind == 'skills':
                section('技能字段', 'SELECT * FROM skill_definitions WHERE skill_id=%s')
                section('配置引用', 'SELECT * FROM skill_references WHERE skill_id=%s ORDER BY source_type,source_key,slot', limit=10)
        return {'sections': sections, 'links': links}
