#!/bin/bash
# Records the real tool output inside a fake home. usage: run_demo.sh <work dir>   (must not exist)
set -euo pipefail
SKILL="$(cd "$(dirname "$0")/.." && pwd)"
W="$1"; [ -e "$W" ] && { echo "work dir exists"; exit 1; }
mkdir -p "$W/out"
python3 "$SKILL/demo/build_sandbox.py" "$W/home" >/dev/null
export HOME="$W/home"
[ "$HOME" = "$W/home" ] || exit 1
cd "$W"
python3 -I "$SKILL/scripts/scan.py" --skip simulators temp apps --out "$W/scan.json" >/dev/null 2>&1
python3 -I "$SKILL/scripts/summary.py" "$W/scan.json" > "$W/out/1_dashboard.txt"
SUM="python3 -I $SKILL/scripts/summary.py $W/scan.json"
APPLY="python3 -I $SKILL/scripts/apply.py --scan $W/scan.json"
# items: the safe caches, the old project that is fully pushed, and the idle node_modules of the only-copy project
N=$(python3 -I - <<PY
import json
d = json.load(open("$W/scan.json"))
pick = [n for n, i in enumerate(d["items"]) if i["risk"] == "safe" and i["group"] != "Project folders"]
pick += [n for n, i in enumerate(d["items"]) if i["group"].startswith("Old projects")]
print(",".join(map(str, pick)))
PY
)
KEEPN=$(python3 -I -c "
import json; d=json.load(open('$W/scan.json'))
print([n for n,i in enumerate(d['items']) if i['risk']=='keep'][0])")
$SUM --plan "$N" > "$W/out/2_plan.txt"
$APPLY --plan "$(cat $W/out/2_plan.txt)" > "$W/out/3_dryrun.txt"
TOKEN=$(grep -o "TOKEN [0-9a-f]*" "$W/out/3_dryrun.txt" | cut -d' ' -f2)
$SUM --plan "$KEEPN" > "$W/out/4_keep_plan.txt"
$APPLY --plan "$(cat $W/out/4_keep_plan.txt)" > "$W/out/4_keep_refused.txt" || true
$APPLY --plan "$(cat $W/out/2_plan.txt)" --yes --token "$TOKEN" > "$W/out/5_execute.txt"
echo "$N" > "$W/out/items.txt"
