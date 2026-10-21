from __future__ import annotations

from connection import QueryFailure


class AuctionRepository:
    page_size = 50

    def __init__(self, connection):
        self.connection = connection

    def settings(self):
        rows = self.connection.query('SELECT * FROM auction_settings WHERE id=1')
        if len(rows) != 1:
            raise QueryFailure('缺少竞拍配置，请确认已执行竞拍迁移 SQL。')
        return rows[0]

    def save_settings(self, original, values):
        updated = {}
        for key, lower, upper in (('enabled', 0, 1), ('listing_limit', 1, 100),
                                  ('minimum_increment', 1, 2147483647)):
            try:
                value = int(values[key])
            except (ValueError, TypeError):
                raise ValueError('开关、上架上限及最小加价必须为整数。') from None
            if not lower <= value <= upper:
                raise ValueError(f'{key} 必须在 {lower}～{upper} 之间。')
            updated[key] = value
        with self.connection.transaction():
            rows = self.connection.query('SELECT * FROM auction_settings WHERE id=1 FOR UPDATE')
            if not rows or rows[0] != original:
                raise QueryFailure('竞拍配置已被修改，请重新读取后再保存。')
            self.connection.write(
                'UPDATE auction_settings SET enabled=%s,listing_limit=%s,minimum_increment=%s,'
                'listing_fee=0,sale_fee=0,version=version+1 WHERE id=1',
                (updated['enabled'], updated['listing_limit'], updated['minimum_increment']))
        return self.settings()

    def listing_detail(self, identity):
        rows = self.connection.query(
            'SELECT l.*,UTC_TIMESTAMP(6) AS server_utc,'
            'c.name AS highest_bidder_name,b.name AS buyer_name '
            'FROM auction_listings l LEFT JOIN characters c ON c.id=l.highest_bidder_id '
            'LEFT JOIN characters b ON b.id=l.buyer_id WHERE l.id=%s', (identity,))
        if not rows:
            raise QueryFailure('该拍卖不存在，请刷新列表。')
        return rows[0]

    def cancel(self, original, operator, reason, request_id):
        operator, reason = operator.strip(), reason.strip()
        try:
            operator, reason = operator.encode('gbk'), reason.encode('gbk')
        except UnicodeEncodeError:
            raise ValueError('操作人和原因必须能够使用 GBK 编码。') from None
        if not operator or len(operator) > 128 or not reason or len(reason) > 512:
            raise ValueError('操作人和原因不能为空，长度分别不能超过 128 和 512 字节。')
        with self.connection.transaction():
            existing = self.connection.query(
                'SELECT listing_id,operator_name,reason FROM auction_admin_commands WHERE request_id=%s',
                (request_id,))
            if existing:
                if (existing[0]['listing_id'] != original['id'] or
                        existing[0]['operator_name'] != operator or existing[0]['reason'] != reason):
                    raise QueryFailure('此请求编号已提交，请先在管理操作确认原请求结果。')
                return request_id
            rows = self.connection.query(
                "SELECT version,status,expires_at>UTC_TIMESTAMP(6) AS live "
                'FROM auction_listings WHERE id=%s FOR UPDATE', (original['id'],))
            if (not rows or rows[0]['version'] != original['version'] or
                    rows[0]['status'] != 'active' or not rows[0]['live']):
                raise QueryFailure('拍卖已变化或到期，请刷新后重新申请撤回。')
            pending = self.connection.query(
                "SELECT id FROM auction_admin_commands WHERE listing_id=%s AND status='pending' LIMIT 1",
                (original['id'],))
            if pending:
                raise QueryFailure(f'该拍卖已有待处理指令 #{pending[0]["id"]}，请查看管理操作。')
            self.connection.write(
                'INSERT INTO auction_admin_commands '
                '(request_id,action,listing_id,expected_version,operator_name,reason) '
                "VALUES (%s,'cancel',%s,%s,%s,%s)",
                (request_id, original['id'], original['version'], operator, reason))
        return request_id

    def page(self, kind, offset, keyword='', asset_type='all', status='all'):
        if offset < 0 or offset % self.page_size:
            raise ValueError('分页位置无效。')
        keyword = keyword.strip()
        try:
            term = keyword.encode('gbk')
        except UnicodeEncodeError:
            raise ValueError('查询关键词必须能够使用 GBK 编码。') from None
        if len(term) > 128:
            raise ValueError('查询关键词不能超过 128 字节。')
        params, conditions = [], []
        if kind == 'listings':
            sql = ('SELECT l.id,l.type,l.name,COALESCE(s.name,l.seller_name) AS seller,'
                   "COALESCE(b.name,CONCAT('#',l.buyer_id),'') AS buyer,"
                   'l.current_price AS price,l.buyout_price AS buyout,l.status,'
                   'l.expires_at AS expires,l.version FROM auction_listings l '
                   'LEFT JOIN characters s ON s.id=l.seller_id '
                   'LEFT JOIN characters b ON b.id=l.buyer_id')
            if asset_type in ('pet', 'item'):
                conditions.append('l.type=%s')
                params.append(asset_type)
            if status in ('active', 'sold', 'expired', 'cancelled'):
                conditions.append('l.status=%s')
                params.append(status)
            search = ('CAST(l.id AS CHAR)', 'l.name', 's.name', 'l.seller_name', 'b.name')
            order = 'l.id'
        elif kind == 'bids':
            sql = ('SELECT b.id,b.listing_id AS listing,'
                   "COALESCE(c.name,CONCAT('#',b.bidder_id)) AS bidder,b.amount,b.charged,"
                   "CASE WHEN l.status='active' THEN IF(l.highest_bidder_id=b.bidder_id "
                   "AND l.current_price=b.amount,'领先','已被超价') ELSE l.status END AS status,"
                   'b.created_at AS created FROM auction_bids b JOIN auction_listings l ON l.id=b.listing_id '
                   'LEFT JOIN characters c ON c.id=b.bidder_id')
            search, order = ('CAST(b.listing_id AS CHAR)', 'c.name'), 'b.id'
        elif kind == 'claims':
            sql = ('SELECT c.id,c.listing_id AS listing,'
                   "COALESCE(p.name,CONCAT('#',c.recipient_id)) AS recipient,c.kind,"
                   "IF(c.kind='asset',l.name,CAST(c.amount AS CHAR)) AS asset,c.status,"
                   'c.created_at AS created FROM auction_claims c JOIN auction_listings l ON l.id=c.listing_id '
                   'LEFT JOIN characters p ON p.id=c.recipient_id')
            conditions.append("c.status='pending'")
            search, order = ('CAST(c.listing_id AS CHAR)', 'p.name'), 'c.id'
        elif kind == 'ledger':
            sql = ('SELECT g.id,g.listing_id AS listing,'
                   "COALESCE(c.name,CONCAT('#',g.character_id)) AS `character`,g.event AS kind,"
                   'g.amount,g.reference_key AS operation,g.created_at AS created FROM auction_ledger g '
                   'LEFT JOIN characters c ON c.id=g.character_id')
            search, order = ('CAST(g.listing_id AS CHAR)', 'c.name'), 'g.id'
        elif kind == 'admin':
            sql = ('SELECT a.id,a.request_id,a.action,a.listing_id AS target,a.operator_name AS operator,'
                   'a.reason,a.status,a.result,a.created_at AS created FROM auction_admin_commands a')
            search, order = ('CAST(a.listing_id AS CHAR)', 'a.operator_name', 'a.request_id'), 'a.id'
        else:
            raise ValueError('未知竞拍栏目。')
        if keyword.isascii() and keyword.isdecimal():
            if len(keyword) > 20 or int(keyword) > 18446744073709551615:
                raise ValueError('拍卖编号超出有效范围。')
            identity_field = {'listings': 'l.id', 'bids': 'b.listing_id', 'claims': 'c.listing_id',
                              'ledger': 'g.listing_id', 'admin': 'a.listing_id'}[kind]
            conditions.append(f'{identity_field}=%s')
            params.append(int(keyword))
        elif term:
            conditions.append('(' + ' OR '.join(f'LOCATE(%s,{field})>0' for field in search) + ')')
            params.extend([term] * len(search))
        if conditions:
            sql += ' WHERE ' + ' AND '.join(conditions)
        rows = self.connection.query(sql + f' ORDER BY {order} DESC LIMIT %s OFFSET %s',
                                     (*params, self.page_size + 1, offset))
        return rows[:self.page_size], len(rows) > self.page_size
