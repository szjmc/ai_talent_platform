# 分析库同步 · 部署说明（方案①：系统级定时）

与「方案②：应用内 APScheduler」的关系：
- 两种方式**可以并存**，靠同一把 Redis 锁（`lock:sync:analytics`）互斥，不会重复执行；
- 未启用 Redis 时只有进程内锁，请**只保留一种**触发方式，否则会并发跑两份同步；
- 推荐：生产用 cron / systemd timer（不依赖 Web 进程存活），开发环境用 APScheduler 或手工执行。

## 方式 A：cron

```bash
crontab -e
# 粘贴 deploy/crontab.example 中的两行，改好路径
```

## 方式 B：systemd timer

```bash
sudo cp deploy/analytics-sync.service /etc/systemd/system/
sudo cp deploy/analytics-sync.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now analytics-sync.timer

systemctl list-timers analytics-sync.timer   # 查看下次触发时间
journalctl -u analytics-sync.service -n 50   # 查看最近一次执行日志
```

## 验证与排障

```bash
# 手工跑一次增量，观察输出行数与日志
python scripts/sync_analytics.py

# 查看水位与上次执行状态（控制表，不属于 6 张分析表）
psql -d ai_talent_analytics -c "SELECT * FROM analytics_sync_state;"

# 接口侧自检（需服务已启动）
curl -s localhost:8000/api/v1/system/info | jq '.data.analytics_sync'
```
