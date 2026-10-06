from __future__ import annotations

import select
import socketserver
import threading
from contextlib import contextmanager


class ConnectionFailure(Exception):
    pass


class QueryFailure(Exception):
    pass


class TunnelServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False


class Connection:
    def __init__(self):
        self.ssh = None
        self.tunnel = None
        self.thread = None
        self.mysql = None
        self.stop = threading.Event()

    def connect(self, profile, ssh_secret, mysql_password):
        self.disconnect()
        self.stop.clear()
        try:
            import paramiko
            import pymysql
        except ImportError:
            raise ConnectionFailure('连接需要 paramiko 和 PyMySQL 依赖。') from None
        profile.validate()
        try:
            self.ssh = paramiko.SSHClient()
            self.ssh.load_system_host_keys()
            if profile.known_hosts:
                self.ssh.load_host_keys(profile.known_hosts)
            self.ssh.set_missing_host_key_policy(paramiko.RejectPolicy())
            options = dict(hostname=profile.ssh_host, port=profile.ssh_port,
                           username=profile.ssh_user, timeout=10, auth_timeout=10,
                           banner_timeout=10, allow_agent=False, look_for_keys=False)
            if profile.auth == 'key':
                options.update(key_filename=profile.key_path, passphrase=ssh_secret or None)
            else:
                options['password'] = ssh_secret
            self.ssh.connect(**options)
            transport = self.ssh.get_transport()
            if transport is None or not transport.is_active():
                raise ConnectionFailure('SSH 连接未建立。')
            transport.set_keepalive(15)
            stop = self.stop
            destination = (profile.mysql_host, profile.mysql_port)

            class Forwarder(socketserver.BaseRequestHandler):
                def handle(handler):
                    channel = None
                    try:
                        channel = transport.open_channel('direct-tcpip', destination,
                                                         handler.client_address, timeout=10)
                        if channel is None:
                            return
                        channel.settimeout(10)
                        handler.request.settimeout(10)
                        while not stop.is_set():
                            readable, _, _ = select.select([handler.request, channel], [], [], 0.5)
                            if handler.request in readable:
                                data = handler.request.recv(65536)
                                if not data:
                                    break
                                channel.sendall(data)
                            if channel in readable:
                                data = channel.recv(65536)
                                if not data:
                                    break
                                handler.request.sendall(data)
                    except (OSError, EOFError, ValueError, paramiko.SSHException):
                        pass
                    finally:
                        if channel is not None:
                            channel.close()
                        handler.request.close()

            self.tunnel = TunnelServer(('127.0.0.1', 0), Forwarder)
            self.thread = threading.Thread(target=self.tunnel.serve_forever, daemon=True)
            self.thread.start()
        except paramiko.BadHostKeyException:
            self.disconnect()
            raise ConnectionFailure('SSH 主机密钥不匹配，请核实服务器身份。') from None
        except paramiko.AuthenticationException:
            self.disconnect()
            raise ConnectionFailure('SSH 认证失败，请检查用户、密码或私钥。') from None
        except ConnectionFailure:
            self.disconnect()
            raise
        except Exception:
            self.disconnect()
            raise ConnectionFailure('SSH 连接失败。请检查网络、私钥及 known_hosts 主机密钥记录。') from None
        try:
            self.mysql = pymysql.connect(host='127.0.0.1', port=self.tunnel.server_address[1],
                                         user=profile.mysql_user, password=mysql_password,
                                         database=profile.database, charset='utf8mb4',
                                         connect_timeout=10, read_timeout=10, write_timeout=10,
                                         autocommit=True)
            with self.mysql.cursor() as cursor:
                cursor.execute('SET SESSION TRANSACTION READ ONLY')
                cursor.execute('SELECT DATABASE(), VERSION()')
                database, version = cursor.fetchone()
                cursor.execute('SELECT version FROM schema_migrations ORDER BY version')
                migrations = [row[0] for row in cursor.fetchall()]
            if database != profile.database or 1 not in migrations:
                raise ConnectionFailure('数据库名称或玩家存储迁移标记不符合当前工具要求。')
            return f'数据库：{database}    MySQL：{version}    迁移标记：{migrations}'
        except ConnectionFailure:
            self.disconnect()
            raise
        except Exception:
            self.disconnect()
            raise ConnectionFailure('MySQL 连接或结构读取失败。请检查隧道目标、数据库账号、密码和查询权限。') from None

    def check(self):
        try:
            transport = self.ssh.get_transport() if self.ssh else None
            if transport is None or not transport.is_active() or self.mysql is None:
                raise ConnectionFailure('连接已断开。')
            self.mysql.ping(reconnect=False)
        except Exception:
            self.disconnect()
            raise ConnectionFailure('连接已断开，请检查网络后手动重新连接。') from None

    def query(self, sql, parameters=()):
        if not sql.lstrip().upper().startswith('SELECT '):
            raise QueryFailure('当前连接仅支持查询。')
        if self.mysql is None:
            raise ConnectionFailure('请先连接数据库。')
        try:
            with self.mysql.cursor() as cursor:
                cursor.execute(sql, parameters)
                columns = [column[0] for column in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]
        except Exception as exc:
            code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
            if code in (2006, 2013, 2055):
                self.disconnect()
                raise ConnectionFailure('查询期间连接断开，请手动重新连接。') from None
            raise QueryFailure(f'查询失败（错误码：{code or "未知"}），请检查表结构和查询权限。') from None

    @contextmanager
    def snapshot(self):
        if self.mysql is None:
            raise ConnectionFailure('请先连接数据库。')
        try:
            with self.mysql.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                cursor.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        except Exception:
            self.check()
            raise QueryFailure('无法建立只读查询快照，请检查数据库权限。') from None
        try:
            yield
        finally:
            if self.mysql is not None:
                try:
                    self.mysql.rollback()
                except Exception:
                    self.disconnect()
                    raise ConnectionFailure('查询快照结束时连接断开，请重新连接。') from None

    def disconnect(self):
        self.stop.set()
        mysql, tunnel, ssh = self.mysql, self.tunnel, self.ssh
        self.mysql = self.tunnel = self.ssh = None
        for resource in (mysql, tunnel, ssh):
            if resource is None:
                continue
            try:
                if resource is tunnel:
                    if self.thread and self.thread.is_alive():
                        resource.shutdown()
                    resource.server_close()
                else:
                    resource.close()
            except Exception:
                pass
        if self.thread:
            if self.thread.is_alive():
                self.thread.join(timeout=2)
            self.thread = None
