#!/bin/bash
# Wrapper for model_runner batch + calibration_check — uses the workspace venv
# Output: clean 3-line summary for Telegram delivery

cd /hermesdata/worldcup-2026-handicap || exit 1
export WORKSPACE="/hermesdata/worldcup-2026-handicap"
VENV="/hermesdata/worldcup-2026-handicap/.venv/bin/python3"
LOCKFILE="/tmp/wc26-dc-fit.lock"

# PID lock: exit silently if another instance is running
exec 200>"$LOCKFILE" || exit 1
flock -n 200 || { echo "[cron_dc_fit] Lock held by another instance — skipping."; exit 0; }

# Ensure we're on the free tier
if [ "${WC26_PRE_TOURNAMENT:-true}" = "true" ]; then
    echo "[cron_dc_fit] Pre-tournament mode — no paid API calls."
fi

# Run batch fit
echo "⚙️  DC 模型拟合..."
$VENV scripts/model_runner.py --mode batch > /tmp/wc26-dc-fit-mr.log 2>&1
MR_EXIT=$?

if [ $MR_EXIT -eq 0 ]; then
    # Parse results from log
    N_FIXTURES=$(grep -oP 'Found \K\d+(?= upcoming fixtures)' /tmp/wc26-dc-fit-mr.log || echo "?")
    N_MATCHES=$(grep -oP 'Used \K\d+(?= weighted matches)' /tmp/wc26-dc-fit-mr.log || echo "?")
    echo "✅ 模型拟合完成 — ${N_MATCHES} 场历史比赛 / ${N_FIXTURES} 场待预测比赛"
else
    echo "❌ 模型拟合失败 (exit $MR_EXIT)"
    head -5 /tmp/wc26-dc-fit-mr.log
fi

# Update calibration
echo "📊 校准状态更新..."
$VENV scripts/calibration_check.py --mode update > /tmp/wc26-dc-fit-cal.log 2>&1
CAL_EXIT=$?

if [ $CAL_EXIT -eq 0 ]; then
    CAL_STATUS=$(grep -oPm1 '"calibration_status":\s*"\K[^"]+' /hermesdata/worldcup-2026-handicap/reports/artifacts/model-calibration-cache.json 2>/dev/null || echo "unknown")
    echo "✅ 校准更新完成 — 状态: ${CAL_STATUS}"
else
    echo "❌ 校准更新失败 (exit $CAL_EXIT)"
fi

# Exit code
FINAL_EXIT=$((MR_EXIT | CAL_EXIT))
exit $FINAL_EXIT
