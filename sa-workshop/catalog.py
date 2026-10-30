from __future__ import annotations

from dataclasses import dataclass
import re

from connection import QueryFailure
from cache import CACHED_KINDS, CatalogCache


PET_TEMPLATE_FIELDS = {
    'name': ('宠物默认名称。', None, None),
    'initnum': ('初始能力计算倍率。', 0, 2147483647 // 265),
    'lvuppoint': ('lvuppoint 参数。', 0, 2147483647 // 265),
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
    f'pet_skill_id{index}': ('默认技能统一编号：0为空，NULL使用旧编号。', 0, 1073741823)
    for index in range(1, 8)
})


PET_TEMPLATE_SKILL_FILTER = (
    "enabled=1 AND skill_id>0 AND skill_id<1073741824 "
    "AND ((adapter='pet' AND legacy_id>=0 AND legacy_id<1073741824) "
    "OR (adapter IN ('magic','profession') AND field_type BETWEEN 0 AND 2 "
    "AND target_type BETWEEN 0 AND 11))"
)


ENEMY_GROUP_FIELDS = {
    'name': ('组合名称。', None, None),
    'appearbyitemid': ('携带此道具时出现。', -1, 2147483647),
    'notappearbyitemid': ('携带此道具时不出现。', -1, 2147483647),
}
ENEMY_GROUP_FIELDS.update({
    f'enemy_id{index}': (f'第 {index} 个实例编号。', -1, 2147483647)
    for index in range(1, 11)
})
ENEMY_GROUP_FIELDS.update({
    f'createprob{index}': (f'第 {index} 个实例的生成权重。', -1, 2147483647)
    for index in range(1, 11)
})


ENCOUNTER_AREA_FIELDS = {
    'id': ('区域编号。', -2147483648, 2147483647),
    'floor': ('地图编号。', -2147483648, 2147483647),
    'x1': ('起点 X。', -2147483648, 2147483647),
    'y1': ('起点 Y。', -2147483648, 2147483647),
    'x2': ('终点 X。', -2147483648, 2147483647),
    'y2': ('终点 Y。', -2147483648, 2147483647),
    'encountprob_min': ('遇敌概率下限。', -2147483648, 2147483647),
    'encountprob_max': ('遇敌概率上限。', -2147483648, 2147483647),
    'enemymaxnum': ('实例数上限。', 1, 10),
    'zorder': ('区域重叠优先级。', -2147483648, 2147483647),
    'event_now': ('进行中的事件编号。', -2147483648, 2147483647),
    'event_end': ('已完成的事件编号。', -2147483648, 2147483647),
    'enemy_group': ('事件替换组合编号。', -1, 2147483647),
}
ENCOUNTER_AREA_FIELDS.update({
    f'groupid{index}': (f'第 {index} 个实例组合编号。', -1, 2147483647)
    for index in range(1, 11)
})
ENCOUNTER_AREA_FIELDS.update({
    f'createprob{index}': (f'第 {index} 个实例组合的生成权重。', -1, 2147483647)
    for index in range(1, 11)
})


PAGE_SIZE = 50
ITEM_TEMPLATE_RANGES = ('attacknum', 'attack', 'defence', 'quick', 'hp', 'mp',
                        'luck', 'charm', 'avoid')
ITEM_TEMPLATE_FIELDS = tuple(f'{key}_{bound}' for key in ITEM_TEMPLATE_RANGES
                             for bound in ('min', 'max')) + ('damcrushe', 'maxdmce', 'skill_id')
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
    source = (f'{kind}_instances p LEFT JOIN characters c ON c.id=p.character_id '
              f'LEFT JOIN {kind}_attributes a ON a.instance_id=p.id')
    if kind == 'pet':
        name = "COALESCE(NULLIF(a.ownt,''),a.name)"
        level = 'a.lv'
    else:
        source += ' LEFT JOIN item_templates t ON t.id=a.id'
        name = 'COALESCE(a.na,CAST(CONVERT(t.name USING gbk) AS BINARY))'
        level = 'COALESCE(a.lv,t.level)'
    identity = 'template_instance_id' if kind == 'pet' else 'id'
    columns = ((identity, '实例编号'), ('name', '名称'))
    if kind != 'pet':
        columns += (('owner', '所属角色'),)
    columns += (('container', '位置'), ('slot', '槽位'), ('level', '等级'), ('unique_code', '唯一编号'))
    return Catalog(
        source,
        f'p.id,p.unique_code,p.character_id,c.name AS owner,p.container,p.slot,{name} AS name,{level} AS level'
        + (',a.source_instance_id AS template_instance_id' if kind == 'pet' else ''),
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
        "accounts a LEFT JOIN account_permissions g ON g.account_id=a.id AND g.permission='gm.level'",
        'a.id,a.username,a.enabled,a.created_at,COALESCE(g.level,0) AS gm_level', 'a.id',
        (('id', '账户编号'), ('username', '账号'), ('enabled', '启用'), ('gm_level', 'GM等级'),
         ('created_at', '创建时间')), ('a.username',), True,
        filters=(('id', 'a.id'),)),
    'characters': Catalog(
        'character_summary c JOIN accounts a ON a.id=c.account_id JOIN characters t ON t.id=c.id',
        'c.id,c.account_id,a.username,c.name,c.slot,c.level,c.gold,c.bank_gold,c.hp,'
        'c.map_id,c.x,c.y,c.revision,c.saved_at,t.tower_progress,'
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
        'enemy_groups g', 'g.id,g.name,('
        + '+'.join(f'(g.enemy_id{index}<>-1)' for index in range(1, 11))
        + ') AS instance_count,g.appearbyitemid,g.notappearbyitemid',
        'g.id', (('id', '组合编号'), ('name', '名称'), ('instance_count', '实例数'),
                 ('appearbyitemid', '出现道具'), ('notappearbyitemid', '排除道具')),
        ('g.name',) + tuple(f'CAST(g.enemy_id{index} AS CHAR)' for index in range(1, 11)),
        filters=(('id', 'g.id'),)),
    'encounter_areas': Catalog(
        'encounter_areas e', 'e.source_order AS id,e.id AS area_id,e.floor,'
        '(SELECT m.map_name FROM map_index m WHERE m.map_id=e.floor ORDER BY m.id LIMIT 1) AS map_name,('
        + '+'.join(f'(e.groupid{index}<>-1)' for index in range(1, 11))
        + ') AS group_count,e.enemymaxnum',
        'e.source_order', (('id', '记录序号'), ('area_id', '区域编号'), ('floor', '地图编号'),
                          ('map_name', '地图名称'),
                          ('group_count', '实例组合数'),
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
        'item_templates p', 'p.id,p.name,p.type,p.cost,p.imagenumber,p.skill_id,p.usefunc', 'p.id',
        (('id', '模板编号'), ('name', '名称'), ('type', '类型'), ('cost', '价格'),
         ('imagenumber', '图像编号'), ('skill_id', '统一技能编号'), ('usefunc', 'usefunc')),
        ('p.name', 'p.secretname'), filters=(('id', 'p.id'),)),
    'black_shop_items': Catalog(
        'black_shop_items b JOIN item_templates p ON p.id=b.item_id',
        'p.id,p.name,p.type,b.enabled,b.cost,b.pearl,b.stock,'
        'CASE WHEN b.cost IS NULL THEN p.cost ELSE b.cost END AS gold_cost,'
        'p.imagenumber,p.level',
        'b.item_id',
        (('id', '道具编号'), ('name', '名称'), ('type', '类型'),
         ('enabled', '启用'), ('gold_cost', '金币价格'),
         ('pearl', 'P 价格'), ('stock', '库存'),
         ('imagenumber', '图像编号'), ('level', '等级')),
        ('p.name', 'p.secretname'), filters=(('id', 'b.item_id'),)),
    'skills': Catalog(
        'skill_definitions s', 's.skill_id AS id,s.name,s.adapter,s.legacy_id,s.enabled,s.function_registered,s.function_name',
        's.skill_id', (('id', '统一编号'), ('name', '名称'), ('adapter', '适配器'),
                       ('legacy_id', '旧编号'), ('enabled', '启用'), ('function_registered', '函数已注册'),
                       ('function_name', '处理函数')),
        ('s.name', 's.skill_key', 's.function_name'), filters=(('id', 's.skill_id'),)),
}


def cell(row, key, catalog):
    if catalog is CATALOGS['black_shop_items']:
        if key == 'stock' and row.get(key) == -1:
            return '不限量'
        if (key == 'gold_cost' and row.get(key) == -1) or (key == 'pearl' and row.get(key) in (None, -1)):
            return '未启用'
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
                'SELECT id,account_id,slot,name,tower_progress,revision,saved_at FROM characters WHERE id=%s LIMIT 1' + suffix,
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

    def save_player(self, original, values, tower_progress=None):
        kind, identity = original['kind'], original['id']
        if kind != 'characters':
            raise ValueError('只有角色支持属性编辑。')
        if tower_progress is None:
            tower_progress = original['character']['tower_progress']
        if not isinstance(tower_progress, str) or not re.fullmatch(r'[0-9]{1,8}(?:0[1-9]|10)10', tower_progress):
            raise ValueError('闯关进度须为5～12位数字：1～8位阶层、两位关卡01～10、总关数10，例如00110。')
        tower_changed = tower_progress != original['character']['tower_progress']
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
        if not changes and not tower_changed:
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
            if tower_changed:
                self.connection.write('UPDATE characters SET tower_progress=%s WHERE id=%s',
                                      (tower_progress, identity))
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
            order = 'b.category,b.sort_order,b.item_id' if kind == 'black_shop_items' else catalog.key
            sql = f'SELECT {catalog.select} FROM {catalog.source}{where} ORDER BY {order}'
            if page is None:
                rows = self.connection.query(sql, parameters)
                total = len(rows)
            else:
                total = self.connection.query(f'SELECT COUNT(*) AS total FROM {catalog.source}{where}', parameters)[0]['total']
                page = min(max(0, page), max(0, (total - 1) // PAGE_SIZE))
                rows = self.connection.query(sql + ' LIMIT %s OFFSET %s',
                                             (*parameters, PAGE_SIZE, page * PAGE_SIZE))
        return {'rows': rows, 'total': total, 'page': page}

    def lite_pet_list(self, keyword, growth, selected_only):
        keyword = keyword.strip()
        if len(keyword) > 200:
            raise ValueError('搜索内容最多200个字符。')
        conditions, parameters = [], []
        if keyword:
            conditions.append('(LOCATE(%s,p.name)>0 OR CAST(p.tempno AS CHAR)=%s OR CAST(p.imgnumber AS CHAR)=%s)')
            parameters.extend((keyword, keyword, keyword))
        if growth.strip():
            try:
                value = int(growth)
            except ValueError:
                raise ValueError('lvuppoint 必须为整数。') from None
            if not 0 <= value <= 2147483647:
                raise ValueError('lvuppoint 超出范围。')
            conditions.append('p.lvuppoint=%s')
            parameters.append(value)
        if selected_only:
            conditions.append('s.tempno IS NOT NULL')
        where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
        return self.connection.query(
            'SELECT p.tempno,p.name,p.imgnumber,p.lvuppoint,(s.tempno IS NOT NULL) AS selected '
            'FROM pet_base_templates p LEFT JOIN lite_pet_selection s ON s.tempno=p.tempno'
            + where + ' ORDER BY p.tempno', parameters)

    def lite_pet_detail(self, identity):
        return self.connection.query('SELECT tempno FROM lite_pet_selection WHERE tempno=%s', (identity,))

    def change_lite_pet_selection(self, identities, add):
        identities = sorted(set(identities))
        if not identities:
            raise ValueError('请先选择宠物基板。')
        if not add:
            placeholders = ','.join('%s' for _ in identities)
            with self.connection.transaction():
                self.connection.write('DELETE FROM lite_pet_selection WHERE tempno IN (' + placeholders + ')',
                                      tuple(identities))
            return
        with self.connection.transaction():
            bases = self.connection.query(
                'SELECT tempno,imgnumber FROM pet_base_templates ORDER BY tempno FOR UPDATE')
            by_id = {row['tempno']: row['imgnumber'] for row in bases}
            if any(identity not in by_id for identity in identities):
                raise ValueError('选中的基板已不存在，请刷新后重试。')
            selected = self.connection.query('SELECT tempno FROM lite_pet_selection ORDER BY tempno FOR UPDATE')
            existing = {row['tempno'] for row in selected}
            if add:
                images = {by_id[identity]: identity for identity in existing if identity in by_id}
                for identity in identities:
                    if identity in existing:
                        continue
                    image = by_id[identity]
                    if image in images:
                        raise ValueError(f'基板 #{identity} 与基板 #{images[image]} 的形象编号 {image} 重复，请选择其中一条。')
                    images[image] = identity
                for identity in identities:
                    if identity not in existing:
                        self.connection.write('INSERT INTO lite_pet_selection (tempno) VALUES (%s)', (identity,))

    def sync_lite_pet_selection(self):
        if self.cache is None:
            raise QueryFailure('宠物基板缓存尚未配置，请重新连接数据库。')
        with self.connection.transaction():
            bases = self.cache.result(
                'lite_pet_templates', ['all'],
                lambda: self.connection.query(
                    'SELECT tempno,imgnumber,lvuppoint FROM pet_base_templates ORDER BY tempno FOR UPDATE'),
                refresh=True)
            selected = self.connection.query('SELECT tempno FROM lite_pet_selection ORDER BY tempno FOR UPDATE')
            existing = {row['tempno'] for row in selected}
            images = {row['imgnumber'] for row in bases if row['tempno'] in existing}
            counts = {}
            for row in bases:
                if row['lvuppoint'] == 4:
                    image = row['imgnumber']
                    counts[image] = counts.get(image, 0) + 1
            identities = [row['tempno'] for row in bases
                          if row['lvuppoint'] == 4 and counts[row['imgnumber']] == 1
                          and row['imgnumber'] not in images]
            if identities:
                placeholders = ','.join('(%s)' for _ in identities)
                self.connection.write('INSERT INTO lite_pet_selection (tempno) VALUES ' + placeholders,
                                      tuple(identities))

    def template_enemies(self, identity):
        return self.connection.query(
            'SELECT id,name,lv_min,lv_max FROM enemy_templates WHERE tempno=%s ORDER BY id', (identity,))

    def enemy_groups(self, identity):
        conditions = ' OR '.join(f'enemy_id{index}=%s' for index in range(1, 11))
        return self.connection.query(
            f'SELECT id,name FROM enemy_groups WHERE {conditions} ORDER BY id', (identity,) * 10)

    def group_areas(self, identity):
        conditions = ' OR '.join(f'e.groupid{index}=%s' for index in range(1, 11))
        return self.connection.query(
            'SELECT e.source_order AS id,e.id AS area_id,e.floor,'
            '(SELECT m.map_name FROM map_index m WHERE m.map_id=e.floor ORDER BY m.id LIMIT 1) AS map_name '
            f'FROM encounter_areas e WHERE {conditions} OR e.enemy_group=%s ORDER BY e.source_order',
            (identity,) * 11)

    def new_pet_template_form(self):
        return self.pet_template_form(None)

    def black_shop_form(self, identity=None):
        items = self.page('item_templates', '', page=None)['rows']
        rows = []
        if identity is not None:
            with self.connection.snapshot():
                rows = self.connection.query('SELECT * FROM black_shop_items WHERE item_id=%s', (identity,))
        if identity is not None and not rows:
            raise QueryFailure('商品已不存在，请刷新列表。')
        return {'items': items, 'original': rows[0] if rows else None}

    def save_black_shop(self, original, values):
        updated = {}
        for key in ('item_id', 'enabled', 'cost', 'pearl', 'stock'):
            text = values[key].strip()
            if key in ('cost', 'pearl') and text.upper() in ('', 'NULL'):
                updated[key] = None
                continue
            try:
                value = int(text)
            except ValueError:
                raise ValueError(f'{key} 必须为整数。') from None
            lower, upper = {'item_id': (0, 2147483647),
                            'enabled': (0, 1),
                            'cost': (-1, 2147483647), 'pearl': (-1, 2147483647),
                            'stock': (-1, 2147483647)}[key]
            if not lower <= value <= upper:
                raise ValueError(f'{key} 必须在 {lower}～{upper} 之间。')
            updated[key] = value
        updated['category'] = original['category'] if original else 0
        updated['sort_order'] = original['sort_order'] if original else 0
        identity = updated['item_id']
        with self.connection.transaction():
            rows = self.connection.query(
                'SELECT * FROM black_shop_items WHERE item_id=%s FOR UPDATE', (identity,))
            if original is None and rows:
                raise ValueError('该道具已在黑市中，请刷新列表后编辑。')
            if original is not None and (identity != original['item_id'] or not rows or rows[0] != original):
                raise QueryFailure('商品配置或库存已变化，请关闭表单并重新读取。')
            if not self.connection.query('SELECT id FROM item_templates WHERE id=%s FOR UPDATE', (identity,)):
                raise ValueError('请选择有效的道具基板。')
            if original is None:
                fields = ','.join(updated)
                placeholders = ','.join('%s' for key in updated)
                self.connection.write(f'INSERT INTO black_shop_items ({fields}) VALUES ({placeholders})',
                                      tuple(updated.values()))
            else:
                changes = {key: value for key, value in updated.items() if key != 'item_id'}
                assignments = ','.join(f'{key}=%s' for key in changes)
                self.connection.write(f'UPDATE black_shop_items SET {assignments} WHERE item_id=%s',
                                      (*changes.values(), identity))
        return updated

    def delete_black_shop(self, original):
        with self.connection.transaction():
            rows = self.connection.query(
                'SELECT * FROM black_shop_items WHERE item_id=%s FOR UPDATE', (original['item_id'],))
            if not rows or rows[0] != original:
                raise QueryFailure('商品配置或库存已变化，请刷新后重试。')
            self.connection.write('DELETE FROM black_shop_items WHERE item_id=%s', (original['item_id'],))

    def pet_template_delete_form(self, identity):
        enemies = self.connection.query(
            'SELECT id FROM enemy_templates WHERE tempno=%s LIMIT 1', (identity,))
        if enemies:
            raise ValueError(f'基板 #{identity} 有关联实例 #{enemies[0]["id"]}，不能删除。')
        return self.pet_template_form(identity)

    def delete_pet_template(self, original):
        identity = original['tempno']
        with self.connection.transaction():
            rows = self.connection.query(
                'SELECT * FROM pet_base_templates WHERE tempno=%s LIMIT 1 FOR UPDATE', (identity,))
            if not rows or rows[0] != original:
                raise QueryFailure('基板已被其他操作修改，请刷新后重试。')
            enemies = self.connection.query(
                'SELECT id FROM enemy_templates WHERE tempno=%s LIMIT 1 FOR UPDATE', (identity,))
            requirements = self.connection.query(
                'SELECT pet_id FROM pet_capture_requirements WHERE pet_id=%s LIMIT 1 FOR UPDATE', (identity,))
            if enemies:
                raise ValueError(f'基板 #{identity} 有关联实例 #{enemies[0]["id"]}，不能删除。')
            if requirements:
                raise ValueError(f'基板 #{identity} 被捕捉条件引用，不能删除。')
            self.connection.write('DELETE FROM pet_base_templates WHERE tempno=%s', (identity,))

    def pet_template_form(self, identity):
        with self.connection.snapshot():
            rows = self.connection.query('SELECT * FROM pet_base_templates WHERE tempno=%s LIMIT 1', (identity,))
            skills = self.connection.query(
                'SELECT skill_id,name,adapter,function_registered FROM skill_definitions WHERE '
                + PET_TEMPLATE_SKILL_FILTER + ' ORDER BY skill_id')
        if identity is None:
            template = {key: 0 for key in PET_TEMPLATE_FIELDS}
            template.update(source_order=0, tempno='', name='', petflg=0, critical=0, counter=0)
            for index in range(1, 8):
                template[f'petskill{index}'] = -1
            for index in range(1, 6):
                template[f'atomfixname{index}'] = ''
                for prefix in ('atombaseadd', 'atomfixmin', 'atomfixmax'):
                    template[f'{prefix}{index}'] = 0
            return {'template': template, 'skills': skills}
        if not rows:
            raise QueryFailure('基板已不存在，请刷新列表。')
        return {'template': rows[0], 'skills': skills}

    def save_pet_template(self, original, values, creating=False):
        if creating:
            text = str(original['tempno'])
            if not re.fullmatch(r'[0-9]+', text) or int(text) > 2147483647:
                raise ValueError('基板编号必须在0到2147483647之间。')
            original = {**original, 'tempno': int(text)}
            if not values.get('name'):
                raise ValueError('名称不能为空。')
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
            if creating:
                orders = self.connection.query(
                    'SELECT source_order FROM pet_base_templates ORDER BY source_order DESC LIMIT 1 FOR UPDATE')
                original['source_order'] = int(orders[0]['source_order']) + 1 if orders else 0
                if original['source_order'] > 4294967295:
                    raise ValueError('记录加载顺序已达到上限，无法新增。')
                if self.connection.query('SELECT tempno FROM pet_base_templates WHERE tempno=%s FOR UPDATE',
                                         (original['tempno'],)):
                    raise ValueError('基板编号已存在，请使用其他编号。')
                keys = tuple(original)
                columns = ','.join(f'`{key}`' for key in keys)
                placeholders = ','.join('%s' for key in keys)
                self.connection.write(
                    f'INSERT INTO pet_base_templates ({columns}) VALUES ({placeholders})',
                    tuple(original[key] for key in keys))
            rows = self.connection.query('SELECT * FROM pet_base_templates WHERE tempno=%s LIMIT 1 FOR UPDATE',
                                         (original['tempno'],))
            if not rows or (not creating and rows[0] != original):
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
                    raise ValueError('initnum 与 lvuppoint 过大，按实例生成等级计算会导致属性溢出。')
            for key, value in changes.items():
                if key.startswith('pet_skill_id') and value is not None and value > 0:
                    skills = self.connection.query(
                        'SELECT skill_id FROM skill_definitions WHERE skill_id=%s AND '
                        + PET_TEMPLATE_SKILL_FILTER + ' LIMIT 1 FOR UPDATE',
                        (value,))
                    if not skills:
                        raise ValueError(f'{key} 必须选择已启用且可加载到宠物技能槽的统一编号。')
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

    def new_enemy_template_form(self):
        template = dict(source_order=0, name='', tacticsoption='at:1;1;1|gu:0|es:0|rn:|wa:0;0;0;0;0;0;0;',
                        act_condition='', id='', tempno='', lv_min=1, lv_max=1,
                        createmaxnum=1, createminnum=1, tactics=1, exp=-1,
                        duelpoint=-1, style=0, petflg=1)
        template.update({f'item{index}': 0 for index in range(1, 11)})
        template.update({f'itemprob{index}': 0 for index in range(1, 11)})
        return {'sections': [('实例字段', [template], False)], 'links': []}

    def delete_enemy_template(self, original):
        with self.connection.transaction():
            rows = self.connection.query('SELECT * FROM enemy_templates WHERE id=%s LIMIT 1 FOR UPDATE',
                                         (original['id'],))
            if not rows or rows[0] != original:
                raise QueryFailure('实例已被其他操作修改，请刷新后重试。')
            conditions = ' OR '.join(f'enemy_id{index}=%s' for index in range(1, 11))
            groups = self.connection.query(
                f'SELECT id FROM enemy_groups WHERE {conditions} LIMIT 1 FOR UPDATE',
                (original['id'],) * 10)
            if groups:
                raise ValueError(f'实例被组合 #{groups[0]["id"]} 引用，不能删除。')
            remaining = self.connection.query('SELECT id FROM enemy_templates WHERE id<>%s LIMIT 1 FOR UPDATE',
                                              (original['id'],))
            if not remaining:
                raise ValueError('服务端要求至少保留一个实例，不能删除最后一个实例。')
            self.connection.write('DELETE FROM enemy_templates WHERE id=%s', (original['id'],))

    def save_enemy_template(self, original, values, creating=False):
        editable = ('tempno', 'name', 'lv_min', 'lv_max', 'createminnum', 'createmaxnum',
                    'item1', 'item2', 'itemprob1', 'itemprob2')
        changes = {}
        for key, text in values.items():
            if key not in editable or key not in original:
                raise ValueError('包含不可修改的实例字段。')
            if not creating and text == display(original[key]):
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
        if creating and not all(key in changes for key in editable):
            raise ValueError('请填写完整的实例字段。')
        with self.connection.transaction():
            bases = self.connection.query(
                'SELECT initnum,lvuppoint FROM pet_base_templates WHERE tempno=%s LIMIT 1 FOR UPDATE',
                (changes.get('tempno', original['tempno']),))
            rows = [original] if creating else self.connection.query(
                'SELECT * FROM enemy_templates WHERE id=%s LIMIT 1 FOR UPDATE', (original['id'],))
            if not rows or rows[0] != original:
                raise QueryFailure('实例已被其他操作修改，请关闭表单并重新打开。')
            updated = {**rows[0], **changes}
            if creating or {'tempno', 'lv_min', 'lv_max'} & changes.keys():
                low, high = int(updated['lv_min']), int(updated['lv_max'])
                if not 0 <= low <= high or high < 1:
                    raise ValueError('最高等级必须至少为1，最低等级不能大于最高等级。')
                if not bases:
                    raise ValueError('关联基板不存在，无法校验生成等级。')
                initial, growth = int(bases[0]['initnum']), int(bases[0]['lvuppoint'])
                if initial < 0 or growth < 0 or (high - 1) * growth + initial > 2147483647 // 265:
                    raise ValueError('等级过高，结合基板 lvuppoint 会导致属性溢出。')
            if creating or {'createminnum', 'createmaxnum'} & changes.keys():
                if not 0 <= int(updated['createminnum']) <= int(updated['createmaxnum']):
                    raise ValueError('最小生成数量不能大于最大生成数量，且不能为负数。')
            if creating:
                orders = self.connection.query(
                    'SELECT source_order FROM enemy_templates ORDER BY source_order DESC LIMIT 1 FOR UPDATE')
                updated['source_order'] = int(orders[0]['source_order']) + 1 if orders else 0
                if updated['source_order'] > 4294967295:
                    raise ValueError('记录加载顺序已达到上限，无法新增。')
                identities = self.connection.query(
                    'SELECT id FROM enemy_templates ORDER BY id DESC LIMIT 1 FOR UPDATE')
                updated['id'] = max(0, int(identities[0]['id']) + 1) if identities else 0
                if updated['id'] > 2147483647:
                    raise ValueError('实例编号已达到上限，无法新增。')
                columns = ','.join(f'`{key}`' for key in updated)
                placeholders = ','.join('%s' for key in updated)
                self.connection.write(f'INSERT INTO enemy_templates ({columns}) VALUES ({placeholders})',
                                      tuple(updated.values()))
            elif changes:
                assignments = ','.join(f'`{key}`=%s' for key in changes)
                self.connection.write(f'UPDATE enemy_templates SET {assignments} WHERE id=%s',
                                      (*changes.values(), original['id']))
        return updated

    def enemy_group_form(self, identity):
        with self.connection.snapshot():
            rows = self.connection.query('SELECT * FROM enemy_groups WHERE id=%s LIMIT 1', (identity,))
        if not rows:
            raise QueryFailure('实例组合已不存在，请刷新列表。')
        return {'sections': [('实例组合字段', rows, False)], 'links': []}

    def save_enemy_group(self, original, values):
        changes = {}
        for key, text in values.items():
            if key not in ENEMY_GROUP_FIELDS or key not in original:
                raise ValueError('包含不可修改的实例组合字段。')
            if text == display(original[key]):
                continue
            if key == 'name':
                if chr(0) in text:
                    raise ValueError('名称不能包含空字符。')
                try:
                    encoded = text.encode('gbk')
                except UnicodeEncodeError:
                    raise ValueError('名称包含服务端 GBK 不支持的字符。') from None
                if len(encoded) >= 32:
                    raise ValueError('名称按 GBK 编码后必须少于32字节。')
                value = encoded if isinstance(original[key], bytes) else text
            else:
                if not re.fullmatch(r'-?[0-9]+', text):
                    raise ValueError(f'{key} 必须为整数。')
                value = int(text)
                meaning, minimum, maximum = ENEMY_GROUP_FIELDS[key]
                if not minimum <= value <= maximum:
                    raise ValueError(f'{key} 必须在 {minimum} 到 {maximum} 之间。')
            changes[key] = value
        with self.connection.transaction():
            rows = self.connection.query('SELECT * FROM enemy_groups WHERE id=%s LIMIT 1 FOR UPDATE',
                                         (original['id'],))
            if not rows or rows[0] != original:
                raise QueryFailure('实例组合已被其他操作修改，请关闭表单并重新打开。')
            updated = {**rows[0], **changes}
            if any(key.startswith(('enemy_id', 'createprob')) for key in changes):
                members = [int(updated[f'enemy_id{index}']) for index in range(1, 11)
                           if int(updated[f'enemy_id{index}']) != -1]
                if not members or len(members) != len(set(members)):
                    raise ValueError('组合至少需要一个实例，且实例编号不能重复。')
                weights = [int(updated[f'createprob{index}']) for index in range(1, 11)
                           if int(updated[f'enemy_id{index}']) != -1]
                if any(weight < 0 for weight in weights) or not 1 <= sum(weights) <= 2147483647:
                    raise ValueError('有效实例的权重不能为负数，权重总和必须在1到2147483647之间。')
                placeholders = ','.join('%s' for member in members)
                found = self.connection.query(
                    f'SELECT id FROM enemy_templates WHERE id IN ({placeholders}) FOR UPDATE', tuple(members))
                if {row['id'] for row in found} != set(members):
                    raise ValueError('组合包含不存在的实例编号。')
            if changes:
                assignments = ','.join(f'`{key}`=%s' for key in changes)
                self.connection.write(f'UPDATE enemy_groups SET {assignments} WHERE id=%s',
                                      (*changes.values(), original['id']))
        return updated

    def encounter_area_form(self, identity):
        with self.connection.snapshot():
            rows = self.connection.query(
                'SELECT * FROM encounter_areas WHERE source_order=%s LIMIT 1', (identity,))
            map_name = self.map_name(rows[0]['floor']) if rows else None
        if not rows:
            raise QueryFailure('遇敌区域已不存在，请刷新列表。')
        return {'sections': [('遇敌区域字段', rows, False)], 'links': [], 'map_name': map_name}

    def map_name(self, identity):
        rows = self.connection.query(
            'SELECT map_name FROM map_index WHERE map_id=%s ORDER BY id LIMIT 1', (identity,))
        return rows[0]['map_name'] if rows else None

    def save_encounter_area(self, original, values):
        changes = {}
        for key, text in values.items():
            if key not in ENCOUNTER_AREA_FIELDS or key not in original:
                raise ValueError('包含不可修改的遇敌区域字段。')
            if text == display(original[key]):
                continue
            if not re.fullmatch(r'-?[0-9]+', text):
                raise ValueError(f'{key} 必须为整数。')
            value = int(text)
            meaning, minimum, maximum = ENCOUNTER_AREA_FIELDS[key]
            if not minimum <= value <= maximum:
                raise ValueError(f'{key} 必须在 {minimum} 到 {maximum} 之间。')
            changes[key] = value
        with self.connection.transaction():
            rows = self.connection.query(
                'SELECT * FROM encounter_areas WHERE source_order=%s LIMIT 1 FOR UPDATE',
                (original['source_order'],))
            if not rows or rows[0] != original:
                raise QueryFailure('遇敌区域已被其他操作修改，请关闭表单并重新打开。')
            updated = {**rows[0], **changes}
            if {'x1', 'x2', 'y1', 'y2'} & changes.keys():
                if any(abs(int(updated[f'{axis}2']) - int(updated[f'{axis}1'])) > 2147483647
                       for axis in ('x', 'y')):
                    raise ValueError('区域坐标跨度不能超过2147483647。')
            if any(key.startswith(('groupid', 'createprob')) for key in changes):
                groups = [int(updated[f'groupid{index}']) for index in range(1, 11)
                          if int(updated[f'groupid{index}']) != -1]
                if len(groups) != len(set(groups)):
                    raise ValueError('实例组合编号不能重复。')
                weights = [int(updated[f'createprob{index}']) for index in range(1, 11)
                           if int(updated[f'groupid{index}']) != -1]
                if groups and (any(weight < 0 for weight in weights)
                               or not 1 <= sum(weights) <= 2147483647):
                    raise ValueError('有效组合的权重不能为负数，权重总和必须在1到2147483647之间。')
            references = {int(updated[key]) for key in changes
                          if key.startswith('groupid') or key == 'enemy_group'} - {-1}
            if references:
                placeholders = ','.join('%s' for identity in references)
                found = self.connection.query(
                    f'SELECT id FROM enemy_groups WHERE id IN ({placeholders}) FOR UPDATE',
                    tuple(references))
                if {row['id'] for row in found} != references:
                    raise ValueError('包含不存在的实例组合编号。')
            if changes:
                assignments = ','.join(f'`{key}`=%s' for key in changes)
                self.connection.write(f'UPDATE encounter_areas SET {assignments} WHERE source_order=%s',
                                      (*changes.values(), original['source_order']))
            map_name = self.map_name(updated['floor'])
        return {'area': updated, 'map_name': map_name}

    def item_skill_filter(self, template):
        if display(template.get('usefunc')) == 'ITEM_useSkillCanned':
            return PET_TEMPLATE_SKILL_FILTER
        return PET_TEMPLATE_SKILL_FILTER + " AND (adapter!='magic' OR legacy_id<=65535)"

    def save_item_template(self, original, values):
        changes = {}
        for key, text in values.items():
            if key not in ITEM_TEMPLATE_FIELDS or key not in original:
                raise ValueError('包含不可修改的道具基板字段。')
            if text == display(original[key]):
                continue
            if text == 'NULL':
                value = None
            else:
                if not re.fullmatch(r'-?[0-9]+', text):
                    raise ValueError(f'{key} 必须为整数或 NULL。')
                value = int(text)
                if not -2147483648 <= value <= 2147483647:
                    raise ValueError(f'{key} 超出32位整数范围。')
                if key == 'skill_id' and not 0 <= value < 1073741824:
                    raise ValueError('skill_id 必须在0到1073741823之间，或为 NULL。')
            changes[key] = value
        with self.connection.transaction():
            rows = self.connection.query(
                'SELECT * FROM item_templates WHERE id=%s LIMIT 1 FOR UPDATE', (original['id'],))
            if not rows or rows[0] != original:
                raise QueryFailure('道具基板已被其他操作修改，请关闭表单并重新打开。')
            updated = {**rows[0], **changes}
            if changes.get('skill_id', 0) is not None and changes.get('skill_id', 0) > 0:
                skills = self.connection.query(
                    'SELECT skill_id FROM skill_definitions WHERE skill_id=%s AND '
                    + self.item_skill_filter(updated) + ' LIMIT 1 FOR UPDATE', (updated['skill_id'],))
                if not skills:
                    raise ValueError('请选择已启用且可用于此道具的统一技能编号。')
            for key in ITEM_TEMPLATE_RANGES:
                lower, upper = f'{key}_min', f'{key}_max'
                if not {lower, upper} & changes.keys():
                    continue
                minimum, maximum = updated[lower], updated[upper]
                if (minimum is None) != (maximum is None):
                    raise ValueError(f'{lower} 和 {upper} 必须同时为 NULL 或同时为整数。')
                if minimum is not None and (minimum > maximum or maximum - minimum >= 2147483647):
                    raise ValueError(f'{key} 下限不能大于上限，且范围跨度必须小于2147483647。')
            if changes:
                assignments = ','.join(f'`{key}`=%s' for key in changes)
                self.connection.write(f'UPDATE item_templates SET {assignments} WHERE id=%s',
                                      (*changes.values(), original['id']))
        return updated

    def detail(self, kind, identity):
        if self.cache is not None and kind in CACHED_KINDS and kind != 'item_templates':
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
        skills = []

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
                section('角色', 'SELECT id,slot,name,tower_progress,revision,saved_at FROM characters WHERE account_id=%s ORDER BY slot', limit=10)
                if rows:
                    section('账号封禁', "SELECT reason,expires_at,created_at FROM login_bans WHERE subject_type='account' AND subject=%s ORDER BY created_at DESC", (rows[0]['username'],), limit=10)
                links.append(('查看所属角色', 'characters', ('account_id', identity)))
            elif kind == 'characters':
                rows = section('角色', 'SELECT c.id,c.account_id,a.username,c.slot,c.name,c.tower_progress,c.revision,c.saved_at '
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
                        "(a.numeric_value>1073741824 AND s.adapter IN ('magic','profession') AND s.skill_id=a.numeric_value-1073741824) "
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
                rows = section('模板字段', f'SELECT * FROM {table} WHERE {key}=%s')
                if kind == 'item_templates' and rows:
                    skills = self.connection.query(
                        'SELECT skill_id,name,adapter,function_registered FROM skill_definitions WHERE '
                        + self.item_skill_filter(rows[0]) + ' ORDER BY skill_id')
            elif kind in ('enemy_templates', 'enemy_groups', 'encounter_areas', 'pet_capture_requirements'):
                title, key = {
                    'enemy_templates': ('实例字段', 'id'),
                    'enemy_groups': ('实例组合字段', 'id'),
                    'encounter_areas': ('遇敌区域字段', 'source_order'),
                    'pet_capture_requirements': ('捕捉条件字段', 'pet_id'),
                }[kind]
                section(title, f'SELECT * FROM {kind} WHERE {key}=%s')
            elif kind == 'black_shop_items':
                section('黑市配置', 'SELECT * FROM black_shop_items WHERE item_id=%s')
                section('道具基板', 'SELECT * FROM item_templates WHERE id=%s')
            elif kind == 'skills':
                section('技能字段', 'SELECT * FROM skill_definitions WHERE skill_id=%s')
                section('配置引用', 'SELECT * FROM skill_references WHERE skill_id=%s ORDER BY source_type,source_key,slot', limit=10)
        return {'sections': sections, 'links': links, 'skills': skills}
