"""
模块：scripts/migrate_training_module.py
职责：培训模块第二轮整改的数据库结构迁移（幂等，可重复执行）。

标签：[Dify组] [P1]（培训模块结构迁移：外键收紧 / 新增列 / 孤儿数据备份）

============================ 实现思路与代码要点 ============================
为什么需要这个脚本
------------------
`create_all` 只建**缺失的表**，不会修改已存在表的结构。本轮整改涉及三类变更，
必须走显式 DDL：

1. `learning_records.plan_id / course_id`：NULL 允许 -> NOT NULL，外键
   SET NULL -> CASCADE。
   原因：MySQL 唯一索引对 NULL 不生效（NULL != NULL），一旦计划或课程被删，
   SET NULL 让记录变成孤儿，唯一键彻底失效，可以无限插入重复行；
   `get_record` 取多行还会抛 MultipleResultsFound（500）。
   执行前必须清理既有孤儿，否则 ALTER ... NOT NULL 会直接失败 ——
   脚本会先备份到 `learning_records_orphan_backup_<日期>` 再删，绝不裸删。

2. `learning_records.examined_at`：新增考核时间列。
   通过率的时间筛选此前借用 `created_at`（建记录的时间），口径不对。

3. `users.talent_id`：新增「登录账号 -> 人才档案」绑定列。
   员工自助上报进度需要判断「这条记录是不是本人」，没有绑定就只能全员只读。

执行方式
--------
    python scripts/migrate_training_module.py            # 对 .env 配置的库执行
    DB_HOST=121.43.198.251 DB_PASSWORD=xxx python scripts/migrate_training_module.py

脚本对每一步都先查 information_schema，已满足则跳过，可重复执行。
=========================================================================
"""

from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

import pymysql

# 允许 `python scripts/migrate_training_module.py` 直接运行（项目根加入搜索路径）
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from app.config.settings import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("migrate_training")

ORPHAN_BACKUP_TABLE = f"learning_records_orphan_backup_{date.today():%Y%m%d}"


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (table, column),
    )
    return int(cur.fetchone()[0]) > 0


def _column_nullable(cur, table: str, column: str) -> bool | None:
    cur.execute(
        "SELECT IS_NULLABLE FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (table, column),
    )
    row = cur.fetchone()
    return None if row is None else str(row[0]).upper() == "YES"


def _fk_delete_rule(cur, table: str, column: str) -> str | None:
    cur.execute(
        "SELECT rc.DELETE_RULE FROM information_schema.REFERENTIAL_CONSTRAINTS rc "
        "JOIN information_schema.KEY_COLUMN_USAGE ku "
        "  ON ku.CONSTRAINT_NAME = rc.CONSTRAINT_NAME "
        " AND ku.CONSTRAINT_SCHEMA = rc.CONSTRAINT_SCHEMA "
        "WHERE rc.CONSTRAINT_SCHEMA=DATABASE() AND rc.TABLE_NAME=%s "
        "  AND ku.COLUMN_NAME=%s LIMIT 1",
        (table, column),
    )
    row = cur.fetchone()
    return None if row is None else str(row[0]).upper()


def _fk_name(cur, table: str, column: str) -> str | None:
    cur.execute(
        "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE "
        "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s "
        "  AND REFERENCED_TABLE_NAME IS NOT NULL LIMIT 1",
        (table, column),
    )
    row = cur.fetchone()
    return None if row is None else str(row[0])


def _count(cur, sql: str) -> int:
    cur.execute(sql)
    return int(cur.fetchone()[0] or 0)


def migrate(host: str, port: int, user: str, password: str, database: str) -> int:
    conn = pymysql.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=True,
    )
    cur = conn.cursor()
    logger.info("目标库：%s:%s/%s", host, port, database)
    failures = 0

    # ---------------------------------------------------------- 1. 孤儿学习记录
    orphans = _count(
        cur,
        "SELECT COUNT(*) FROM learning_records WHERE plan_id IS NULL OR course_id IS NULL",
    )
    if orphans:
        logger.info("发现 %s 条孤儿学习记录（plan_id / course_id 为空）", orphans)
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS `{ORPHAN_BACKUP_TABLE}` AS "
            "SELECT * FROM learning_records WHERE plan_id IS NULL OR course_id IS NULL"
        )
        backed_up = _count(cur, f"SELECT COUNT(*) FROM `{ORPHAN_BACKUP_TABLE}`")
        logger.info("已备份到 %s（%s 行）", ORPHAN_BACKUP_TABLE, backed_up)
        cur.execute(
            "DELETE FROM learning_records WHERE plan_id IS NULL OR course_id IS NULL"
        )
        logger.info("已删除孤儿记录 %s 行（备份表保留，可随时回查）", cur.rowcount)
    else:
        logger.info("无孤儿学习记录，跳过清理")

    # ---------------------------------------------------------- 2. 外键改 CASCADE
    for column, parent in (
        ("plan_id", "training_plans"),
        ("course_id", "courses"),
    ):
        fk = _fk_name(cur, "learning_records", column)
        rule = _fk_delete_rule(cur, "learning_records", column)
        if fk and rule == "CASCADE":
            logger.info("%s 外键已是 CASCADE，跳过", column)
            continue
        try:
            cur.execute(f"ALTER TABLE learning_records DROP FOREIGN KEY `{fk}`")
            logger.info("已删除旧外键 %s（原规则 %s）", fk, rule)
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除外键 %s 失败（可能不存在）：%s", fk, exc)

        nullable = _column_nullable(cur, "learning_records", column)
        if nullable:
            remaining = _count(
                cur, f"SELECT COUNT(*) FROM learning_records WHERE `{column}` IS NULL"
            )
            if remaining:
                logger.error(
                    "%s 仍有 %s 行为空，无法收紧为 NOT NULL；请先确认这些记录归属",
                    column,
                    remaining,
                )
                failures += 1
                continue
            comment = "计划 ID（FK -> training_plans.id，CASCADE）" if column == "plan_id" else "课程 ID（FK -> courses.id，CASCADE）"
            cur.execute(
                f"ALTER TABLE learning_records MODIFY `{column}` BIGINT NOT NULL "
                f"COMMENT '{comment}'"
            )
            logger.info("%s 已收紧为 NOT NULL", column)

        cur.execute(
            f"ALTER TABLE learning_records ADD CONSTRAINT `fk_learning_records_{column}_{parent}` "
            f"FOREIGN KEY (`{column}`) REFERENCES `{parent}` (`id`) ON DELETE CASCADE"
        )
        logger.info("%s 外键已重建为 ON DELETE CASCADE", column)

    # ---------------------------------------------------------- 3. examined_at
    if _column_exists(cur, "learning_records", "examined_at"):
        logger.info("learning_records.examined_at 已存在，跳过")
    else:
        cur.execute(
            "ALTER TABLE learning_records ADD COLUMN `examined_at` DATETIME NULL "
            "COMMENT '最近一次考核时间（通过率按此字段做时间筛选；为空表示从未考核）' "
            "AFTER `passed`"
        )
        logger.info("已新增 learning_records.examined_at")
        # 历史已考核记录：用 updated_at 兜底回填，避免上线后统计窗口内「全部消失」
        cur.execute(
            "UPDATE learning_records SET examined_at = updated_at "
            "WHERE passed IS NOT NULL AND examined_at IS NULL"
        )
        logger.info("已按 updated_at 回填历史考核时间 %s 行", cur.rowcount)

    # ---------------------------------------------------------- 4. users.talent_id
    if _column_exists(cur, "users", "talent_id"):
        logger.info("users.talent_id 已存在，跳过")
    else:
        cur.execute(
            "ALTER TABLE users ADD COLUMN `talent_id` BIGINT NULL "
            "COMMENT '关联的人才档案 ID（逻辑外键 -> talents.id）' AFTER `position_title`"
        )
        cur.execute("ALTER TABLE users ADD INDEX `ix_users_talent_id` (`talent_id`)")
        logger.info("已新增 users.talent_id + 索引")

    # ---------------------------------------------------------- 5. 新表
    cur.execute("SHOW TABLES LIKE 'training_notifications'")
    if cur.fetchone():
        logger.info("training_notifications 已存在，跳过")
    else:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS `training_notifications` (
              `plan_id` bigint NOT NULL COMMENT '计划 ID（FK -> training_plans.id，CASCADE）',
              `talent_id` bigint DEFAULT NULL COMMENT '接收人才 ID（收件人）',
              `type` varchar(48) NOT NULL COMMENT '事件类型（training.*）',
              `level` varchar(20) NOT NULL DEFAULT '正常' COMMENT '级别：正常 / 提醒 / HR预警',
              `title` varchar(128) NOT NULL COMMENT '标题',
              `content` varchar(512) NOT NULL COMMENT '正文',
              `notify_date` date NOT NULL COMMENT '通知日期（与 type 组成幂等键，避免重复推送）',
              `is_read` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否已读',
              `read_at` datetime DEFAULT NULL COMMENT '已读时间',
              `id` bigint NOT NULL AUTO_INCREMENT COMMENT '主键 ID',
              `version` int NOT NULL DEFAULT '1' COMMENT '乐观锁版本号',
              `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
              `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '更新时间',
              `is_deleted` tinyint(1) NOT NULL DEFAULT '0' COMMENT '是否已删除',
              `deleted_at` datetime DEFAULT NULL COMMENT '删除时间',
              `created_by` bigint DEFAULT NULL COMMENT '创建人用户 ID',
              `updated_by` bigint DEFAULT NULL COMMENT '最后修改人用户 ID',
              PRIMARY KEY (`id`),
              UNIQUE KEY `uq_training_notifications_dedup` (`plan_id`,`type`,`notify_date`),
              KEY `ix_training_notifications_talent_type` (`talent_id`,`type`),
              CONSTRAINT `fk_training_notifications_plan` FOREIGN KEY (`plan_id`)
                REFERENCES `training_plans` (`id`) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='培训站内信表'
            """
        )
        logger.info("已创建 training_notifications")

    cur.close()
    conn.close()
    if failures:
        logger.error("迁移完成，但有 %s 项需要人工处理（见上方 ERROR）", failures)
        return 1
    logger.info("迁移完成，全部结构已就绪")
    return 0


def main() -> int:
    return migrate(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
    )


if __name__ == "__main__":
    sys.exit(main())
