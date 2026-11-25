#!/data/data/com.termux/files/usr/bin/bash
#
# Optional helper: keeps a free-tier FreeLLMAPI instance warm by polling its
# model catalog every 10 minutes. It reads the key from the Freenerf config.
# This is separate from the agent and never runs automatically.

URL="https://freellmapi-3d3g.onrender.com/v1/models"
CONFIG="${FREENERF_CONFIG:-$HOME/.config/freenerf/config.json}"
INTERVAL=300

KEY=$(CONFIG_PATH="$CONFIG" python -c '
import json, os
path = os.environ["CONFIG_PATH"]
try:
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
    print(config.get("freellmapi_api_key", ""))
except Exception:
    print("")
')

if [ -z "$KEY" ]; then
    echo "[!] No FreeLLMAPI key found in $CONFIG"
    exit 1
fi

echo "Freenerf keepalive started"
echo "Interval: 10 minutes"

while true; do
    TIME=$(date '+%Y-%m-%d %H:%M:%S')

    CODE=$(curl -sS -o /dev/null -w "%{http_code}" \
        --max-time 90 \
        -H "Authorization: Bearer $KEY" \
        "$URL" 2>/dev/null)

    if [ "$CODE" = "200" ]; then
        echo "[$TIME] ONLINE - HTTP $CODE"
    else
        echo "[$TIME] HTTP $CODE"
    fi

    sleep "$INTERVAL"
done
