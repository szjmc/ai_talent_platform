-- ============================================================================
-- 99_cleanup.sql — 汇总报告 + 清理全部测试痕迹
-- 标签：[架构组] [P1]（可重复执行的前提：测试数据/过程/结果表全部清走）
-- 依赖：01-04 已执行。输出：分组汇总 + 失败明细 + 清理确认。
-- ============================================================================
SET NAMES utf8mb4;

-- ---------------------------------------------------------------- 汇总报告
SELECT SUBSTRING_INDEX(test_id, '-', 1) AS 套件,
       COUNT(*) AS 用例数, SUM(passed) AS 通过, SUM(1 - passed) AS 失败
FROM sqltest_results GROUP BY SUBSTRING_INDEX(test_id, '-', 1) ORDER BY 套件;
SELECT COUNT(*) AS 总用例, SUM(passed) AS 总通过, SUM(1 - passed) AS 总失败 FROM sqltest_results;
SELECT test_id, test_name, detail FROM sqltest_results WHERE passed = 0 ORDER BY test_id;

-- ---------------------------------------------------------------- 失败则报错退出（供 CI 判定）
DROP PROCEDURE IF EXISTS sqltest_fail_if_any;
DELIMITER $$
CREATE PROCEDURE sqltest_fail_if_any()
BEGIN
  DECLARE v_failed INT DEFAULT 0;
  SELECT SUM(1 - passed) INTO v_failed FROM sqltest_results;
  IF v_failed > 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'SQL 测试存在失败用例，详见上方失败明细';
  END IF;
END$$
DELIMITER ;
CALL sqltest_fail_if_any();

-- ---------------------------------------------------------------- 清理（仅在全部通过后执行）
CALL sqltest_cleanup_data();
DELETE FROM sqltest_results;
DROP TABLE IF EXISTS sqltest_results;
DROP PROCEDURE IF EXISTS sqltest_guard;
DROP PROCEDURE IF EXISTS sqltest_cleanup_data;
DROP PROCEDURE IF EXISTS sqltest_expect_error;
DROP PROCEDURE IF EXISTS sqltest_fail_if_any;
SELECT 'MySQL SQL 测试全部通过，测试痕迹已清理' AS 结果;
