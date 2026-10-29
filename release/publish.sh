#!/usr/bin/env bash
# One command to publish the har2client dataset and the leaderboard Space to the Hugging Face Hub.
#
#   HF_TOKEN=hf_xxx ./release/publish.sh <namespace> [--private] [--dry-run]
#   ./release/publish.sh <namespace> --dry-run      (no token, no network)
#
#   <namespace>   Hub user or organisation that will own the repos
#   --private     create the repos as private (default: public)
#   --dry-run     build, check and stage everything, but do not upload (no token needed)
#
# Steps: guard -> export the dataset (without the restricted Tier A specs) -> leak check -> Croissant check -> sync Space data
#        -> leak check again -> stage the Space -> leak check of the staged Space -> upload.
# The guard refuses a real publish while the card, croissant.json or the Space README hold TBD, TODO, "<code repository>" or
# "Open decision" (a dry run only prints a warning). Any failed check stops the script before anything is uploaded.
# Needs: python3, uv (upload only). Never put the token in a file.
set -euo pipefail

NAMESPACE="${1:-}"
[ -n "$NAMESPACE" ] || { echo "usage: HF_TOKEN=... $0 <namespace> [--private] [--dry-run]" >&2; exit 2; }
case "$NAMESPACE" in
  *[!A-Za-z0-9._-]*|-*) echo "error: '$NAMESPACE' is not a valid Hub namespace (flags go after it)" >&2; exit 2 ;;
esac
shift
EXTRA_ARGS=("$@")
DRY=0
for a in "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --private) ;;
    *) echo "error: unknown option '$a'" >&2; exit 2 ;;
  esac
done
# Placeholder guard first: a real publish with markers left in the public text stops here, before the token check and the build.
python3 "$(dirname "$0")/publish_guard.py" $([ "$DRY" = 1 ] && echo --dry-run)
if [ "$DRY" = 0 ] && [ -z "${HF_TOKEN:-}" ]; then echo "error: set HF_TOKEN" >&2; exit 2; fi

HERE="$(cd "$(dirname "$0")" && pwd)"
BUILD="$HERE/build"
DATASET_ID="$NAMESPACE/har2client"
# The code repository is public: the GitHub link is fixed in the card and the Space text. Tier A tasks whose spec licence is restricted are left out.
DROP_ARGS=(--drop-spec-class restricted)
# Case 3 v3 evaluation suite: base arm and hint arm (override with HEAL_V3_DIR / HEAL_V3_HINT_DIR).
V3_ARGS=(--heal-v3 "${HEAL_V3_DIR:-$HERE/../har2client/runs/heal_v3_eval}" --heal-v3-hint "${HEAL_V3_HINT_DIR:-$HERE/../har2client/runs/heal_v3_eval_hint}")

echo "1/8 export dataset"
python3 "$HERE/export_dataset.py" --repo-id "$DATASET_ID" "${DROP_ARGS[@]}" "${V3_ARGS[@]}" ${PROBE_DIR:+--probe "$PROBE_DIR"} ${HEAL_DIR:+--heal "$HEAL_DIR"}

# A missing task folder only prints a note in the exporter; do not upload a dataset that lost a tier.
python3 - "$BUILD/dataset/MANIFEST.json" <<'PY'
import json, sys
manifest = json.load(open(sys.argv[1]))
rows = {k: v["rows"] for k, v in manifest["configs"].items()}
missing = [k for k in ("tier_a", "tier_b", "heal_v3", "heal_v3_hint") if not rows.get(k)]
if missing:
    sys.exit(f"error: no rows exported for {missing}; check har2client/runs/set30, tierb50, heal_v3_eval and heal_v3_eval_hint")
if "restricted" not in manifest.get("dropped_spec_classes", []):
    sys.exit("error: the export did not drop the restricted Tier A specs")
if not manifest.get("results"):
    sys.exit("error: no results/ files in the export")
print("rows: " + ", ".join(f"{k}={v}" for k, v in rows.items()) + f"; results files: {len(manifest['results'])}")
PY

echo "2/8 leak check (dataset)"
python3 "$HERE/check_no_leak.py" "${V3_ARGS[@]}" ${PROBE_DIR:+--probe "$PROBE_DIR"} ${HEAL_DIR:+--heal "$HEAL_DIR"}

echo "3/8 Croissant check"
python3 "$HERE/validate_croissant.py" "$BUILD/dataset/croissant.json" --strict

echo "4/8 sync Space data"
python3 "$HERE/sync_space_data.py"

echo "5/8 leak check (dataset, results and Space data)"
python3 "$HERE/check_no_leak.py" "${V3_ARGS[@]}" ${PROBE_DIR:+--probe "$PROBE_DIR"} ${HEAL_DIR:+--heal "$HEAL_DIR"}

echo "6/8 stage Space"
rm -rf "$BUILD/space"
mkdir -p "$BUILD/space"
cp -R "$HERE/space/." "$BUILD/space/"
rm -rf "$BUILD/space/__pycache__"
python3 -c 'import json, sys; print(json.dumps({"dataset": "https://huggingface.co/datasets/" + sys.argv[1], "report": ""}))' "$DATASET_ID" > "$BUILD/space/data/links.json"

echo "7/8 leak check (staged Space)"
python3 "$HERE/check_no_leak.py" "${V3_ARGS[@]}" ${PROBE_DIR:+--probe "$PROBE_DIR"} ${HEAL_DIR:+--heal "$HEAL_DIR"} \
  --extra "$HERE/DATASET_CARD.md" "$HERE/croissant.json" "$BUILD/space"

echo "8/8 upload"
if [ "$DRY" = 1 ]; then
  # --dry-run needs no network: publish_hf.py imports huggingface_hub only when it uploads.
  python3 "$HERE/publish_hf.py" --namespace "$NAMESPACE" --dataset-dir "$BUILD/dataset" --space-dir "$BUILD/space" "${EXTRA_ARGS[@]}"
  exit 0
fi
uv run --quiet --with "huggingface_hub>=1.0" python "$HERE/publish_hf.py" \
  --namespace "$NAMESPACE" --dataset-dir "$BUILD/dataset" --space-dir "$BUILD/space" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
