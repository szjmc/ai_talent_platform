-- ============================================================================
-- 99_cleanup.sql — 汇总报告 + 清理全部测试痕迹
-- 标签：[Vanna组] [P1]
-- 依赖：01-04 已执行。存在失败用例时抛异常（psql ON_ERROR_STOP 下非零退出）。
-- ============================================================================

SELECT split_part(test_id, '-', 1) AS 套件, count(*) AS 用例数,
       count(*) FILTER (WHERE passed) AS 通过, count(*) FILTER (WHERE NOT passed) AS 失败
FROM sqltest_results GROUP BY 1 ORDER BY 1;
SELECT count(*) AS 总用例, count(*) FILTER (WHERE passed) AS 总通过, count(*) FILTER (WHERE NOT passed) AS 总失败 FROM sqltest_results;
SELECT test_id, test_name, detail FROM sqltest_results WHERE NOT passed ORDER BY test_id;

DO $$
DECLARE v_failed int;
BEGIN
  SELECT count(*) INTO v_failed FROM sqltest_results WHERE NOT passed;
  IF v_failed > 0 THEN
    RAISE EXCEPTION 'SQL 测试存在 % 个失败用例，详见上方失败明细', v_failed;
  END IF;
END $$;

-- 清理测试数据与辅助对象（仅在全部通过后执行）
DELETE FROM fact_talent_snapshot WHERE id BETWEEN 900000 AND 900999;
DELETE FROM fact_assessment_history WHERE id BETWEEN 900000 AND 900999;
DELETE FROM fact_training_track WHERE id BETWEEN 900000 AND 900999;
DELETE FROM fact_behavior_event WHERE id BETWEEN 900000 AND 900999;
DELETE FROM dim_talent_wide WHERE talent_id BETWEEN 900000 AND 900999;
DELETE FROM dim_organization WHERE org_id BETWEEN 900000 AND 900999;
DROP FUNCTION IF EXISTS sqltest_check(text, text, text, text);
DROP FUNCTION IF EXISTS sqltest_expect_error(text, text, text, text);
DROP TABLE IF EXISTS sqltest_results;
SELECT 'PgSQL SQL 测试全部通过，测试痕迹已清理' AS 结果;
