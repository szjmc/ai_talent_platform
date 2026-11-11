"""
模块：tests/test_mysql_ddl_defaults.py
职责：MySQL 建表 DDL 合法性守门（离线，不需要数据库服务）。

标签：[架构组] [P0]（自动建表回归守门：ERR 1101 一类的 DDL 非法默认值）

背景（真实事故）：
    启动时 `init_db()` 执行 `Base.metadata.create_all`，在 exam_sessions 表处报
        pymysql.err.OperationalError: (1101, "BLOB, TEXT, GEOMETRY or JSON column
        'question_ids' can't have a default value")
    原因是 question_ids / violations 两个 JSON 列声明了字面量服务端默认值
    `server_default=text("'[]'")`。MySQL 对 JSON / TEXT / BLOB / GEOMETRY 列：
      - 不允许字面量默认值（ERR 1101，任何版本都报）；
      - 8.0.13+ 只允许**表达式**默认值 `DEFAULT ('[]')`，5.7 / MariaDB 依旧不支持。
    create_all 在第一个失败的表处中断，其后的表全部不建 —— 服务虽能启动，
    但邀约 / 笔试等接口一用就 500，属于「初始化静默失败」类高危问题。

守门策略（全部离线，纯 SQLAlchemy 元数据 + MySQL 方言编译）：
1. 任何 MySQL 表的 JSON / TEXT / BLOB / GEOMETRY 列都不得带 `server_default`；
   需要默认值请用 ORM 侧 `default=`（INSERT 时由 SQLAlchemy 补值）；
2. exam_sessions 的 question_ids / violations 必须保留 ORM 默认，
   保证 `ExamSession(...)` 不传这两列也能落库；
3. 全部业务表（含本轮新增的 3 张在线监考表）的 DDL 必须能用 MySQL 方言编译（不引入 DDL 语法错误）；
4. 自动建表必须**逐表隔离**：`create_tables_individually` 遇到单表 DDL 错误时
   只记录失败表，其余表照常创建（防止「第一张表失败 → 后面全部不建」再演）。

运行：pytest tests/test_mysql_ddl_defaults.py -q
"""

from __future__ import annotations

import inspect

from sqlalchemy.dialects import mysql
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateTable

from app.infrastructure.database.session import create_tables_individually
from app.models import Base
from app.models.invitation import ExamSession

# MySQL 不允许带默认值的大对象 / 半结构化列类型（按 SQLAlchemy 类型类名判定）
FORBIDDEN_DEFAULT_TYPES = {
    "JSON",
    "TEXT",
    "TINYTEXT",
    "MEDIUMTEXT",
    "LONGTEXT",
    "BLOB",
    "TINYBLOB",
    "MEDIUMBLOB",
    "LONGBLOB",
    "GEOMETRY",
    "POINT",
    "LINESTRING",
    "POLYGON",
}


def _forbidden_columns():
    """列出所有「非法默认值」的列：(表名, 列名, 类型, 默认值表达式)。"""
    offenders = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if column.server_default is None:
                continue
            # JSON 在 SQLAlchemy 中会派生出 JSON / JSONB 等子类，取最近的大写类名
            type_names = {cls.__name__.upper() for cls in type(column.type).__mro__}
            if type_names & FORBIDDEN_DEFAULT_TYPES:
                arg = getattr(column.server_default, "arg", column.server_default)
                offenders.append((table.name, column.name, str(column.type), str(arg)))
    return offenders


def test_no_json_text_blob_column_declares_server_default():
    """JSON / TEXT / BLOB / GEOMETRY 列一律不得声明 server_default（否则建表直接失败）。"""
    offenders = _forbidden_columns()
    assert not offenders, (
        "以下列声明了 MySQL 不允许的服务端默认值，create_all 会报 ERR 1101 并中断建表：\n"
        + "\n".join(f"  {table}.{column} ({type_}) DEFAULT {default}" for table, column, type_, default in offenders)
        + "\n修复方式：删除 server_default，改用 ORM 侧 default=（如 default=list）。"
    )


def test_exam_sessions_ddl_has_no_json_default():
    """回归用例：exam_sessions 的建表语句里不能再出现 JSON 列的 DEFAULT 子句。"""
    ddl = str(CreateTable(Base.metadata.tables["exam_sessions"]).compile(dialect=mysql.dialect()))
    for column in ("question_ids", "violations", "answers", "proctor_state", "score_detail"):
        line = next(line for line in ddl.splitlines() if line.strip().startswith(f"{column} JSON"))
        assert "DEFAULT" not in line, f"{column} 仍带默认值：{line.strip()}"
    assert "question_ids JSON NOT NULL" in ddl


def test_exam_session_orm_defaults_still_fill_json_columns():
    """去掉 server_default 后，ORM 侧默认值必须仍在，保证不传列也能落库。

    注意：`default=` 是 INSERT 时（flush）由 SQLAlchemy 补值，实例刚构造时属性仍是 None，
    因此这里校验列的 default 定义，而不是构造后的属性值。
    """
    columns = ExamSession.__table__.c
    for name in ("question_ids", "violations"):
        column = columns[name]
        assert column.server_default is None, f"{name} 不应再有服务端默认值"
        assert column.default is not None, f"{name} 缺少 ORM 默认值，插入时会是 NULL"
        assert column.default.is_callable, f"{name} 的 ORM 默认值应为可调用（插入时补 []）"
        # SQLAlchemy 会把可调用对象包一层（接收 ExecutionContext），unwrap 回原始 callable
        assert inspect.unwrap(column.default.arg) is list, f"{name} 的 ORM 默认值应为 list"
    assert columns["question_ids"].nullable is False

    # 显式传值不受影响
    session = ExamSession(invitation_id=1, token="t" * 16, question_ids=[3, 1, 2], violations=[{"type": "no_face"}])
    assert session.question_ids == [3, 1, 2]
    assert session.violations == [{"type": "no_face"}]


def test_every_table_ddl_compiles_for_mysql():
    """全部表都能用 MySQL 方言编译出 DDL（含索引），避免语法级建表失败。"""
    dialect = mysql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert ddl.startswith("\nCREATE TABLE") or ddl.startswith("CREATE TABLE")
        assert "AUTO_INCREMENT" in ddl or table.primary_key.columns


class _FakeDdlConnection:
    """模拟同步连接：只关心 create_all 的 tables 参数，指定表抛建表错误。"""

    def __init__(self, broken: set[str]) -> None:
        self.dialect = mysql.dialect()
        self.broken = broken
        self.created: list[str] = []

    def _run_ddl_visitor(self, visitor, element, **kw):  # noqa: ANN001, D401
        for table in kw.get("tables") or []:
            if table.name in self.broken:
                raise OperationalError(
                    "CREATE TABLE x", {},
                    Exception("(1101, \"BLOB, TEXT, GEOMETRY or JSON column can't have a default value\")"),
                )
            self.created.append(table.name)
        return None


def test_create_tables_individually_isolates_broken_table():
    """单表 DDL 失败不得中断其余表；失败表名与原因要回传给调用方。"""
    conn = _FakeDdlConnection(broken={"exam_sessions"})
    failures = create_tables_individually(conn)

    assert [name for name, _ in failures] == ["exam_sessions"]
    assert "1101" in failures[0][1]
    assert "exam_sessions" not in conn.created
    # 其余表全部照常创建（含 exam_sessions 之后的表）
    assert len(conn.created) == len(Base.metadata.tables) - 1
    assert "talent_invitations" in conn.created and "users" in conn.created
