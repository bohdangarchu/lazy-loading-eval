#!/bin/bash
set -euo pipefail

# ── gather disk totals ────────────────────────────────────────────
eval "$(df -h / | awk 'NR==2 {print "size="$2" used="$3" avail="$4" pct="$5}')"

echo ""
echo "  Total used: ${used} / ${size} (${pct})"
echo ""

# ── known locations (label, path, description) ────────────────────
locations=(
  "nerdctl volumes|/var/lib/nerdctl|container volumes"
  "~/.cache|$HOME/.cache|pip, huggingface, jetbrains, go-build"
  "~/workspace|$HOME/workspace|repos"
  "~/.2dfs|$HOME/.2dfs|2dfs builder cache"
  "go/|$HOME/go|Go toolchain/modules"
  "containerd-stargz|/var/lib/containerd-stargz-grpc|stargz snapshotter cache"
  "containerd|/var/lib/containerd|containerd content store"
  "buildkit|/var/lib/buildkit|buildkit cache"
  "/tmp|/tmp|temporary files"
  "snap|/snap|snap packages"
)

# ── measure each location ─────────────────────────────────────────
declare -a rows=()
declare -a raw_bytes=()

for entry in "${locations[@]}"; do
  IFS='|' read -r label path desc <<< "$entry"
  [ -d "$path" ] || continue
  total=$(sudo du -sh "$path" 2>/dev/null | awk '{print $1}')
  total_bytes=$(sudo du -sb "$path" 2>/dev/null | awk '{print $1}')

  # top 3 subdirectories for detail
  details=""
  while IFS= read -r line; do
    sub_size=$(echo "$line" | awk '{print $1}')
    sub_name=$(echo "$line" | awk '{print $2}' | xargs basename)
    if [ -n "$details" ]; then
      details="${details}, ${sub_name} ${sub_size}"
    else
      details="${sub_name} ${sub_size}"
    fi
  done < <(sudo du -h --max-depth=1 "$path" 2>/dev/null | sort -rh | head -4 | tail -3)

  [ -n "$details" ] && note="$details" || note="$desc"
  rows+=("${label}|${total}|${note}")
  raw_bytes+=("$total_bytes")
done

# ── find column widths ────────────────────────────────────────────
max_w=4; max_s=4; max_n=5
for row in "${rows[@]}"; do
  IFS='|' read -r w s n <<< "$row"
  (( ${#w} > max_w )) && max_w=${#w}
  (( ${#s} > max_s )) && max_s=${#s}
  (( ${#n} > max_n )) && max_n=${#n}
done

# ── draw table ────────────────────────────────────────────────────
hline_w=$(printf '─%.0s' $(seq 1 $((max_w + 2))))
hline_s=$(printf '─%.0s' $(seq 1 $((max_s + 2))))
hline_n=$(printf '─%.0s' $(seq 1 $((max_n + 2))))

printf "  ┌%s┬%s┬%s┐\n" "$hline_w" "$hline_s" "$hline_n"
printf "  │ %-${max_w}s │ %-${max_s}s │ %-${max_n}s │\n" "What" "Size" "Notes"
printf "  ├%s┼%s┼%s┤\n" "$hline_w" "$hline_s" "$hline_n"

first=true
for row in "${rows[@]}"; do
  IFS='|' read -r w s n <<< "$row"
  if $first; then
    first=false
  else
    printf "  ├%s┼%s┼%s┤\n" "$hline_w" "$hline_s" "$hline_n"
  fi
  printf "  │ %-${max_w}s │ %-${max_s}s │ %-${max_n}s │\n" "$w" "$s" "$n"
done

printf "  └%s┴%s┴%s┘\n" "$hline_w" "$hline_s" "$hline_n"
echo ""

# ── highlight biggest offender ────────────────────────────────────
biggest_idx=0
biggest_val=0
for i in "${!raw_bytes[@]}"; do
  if (( raw_bytes[i] > biggest_val )); then
    biggest_val=${raw_bytes[i]}
    biggest_idx=$i
  fi
done

IFS='|' read -r blabel bsize bnote <<< "${rows[$biggest_idx]}"
echo "  Biggest: ${blabel} (${bsize}) — ${bnote}"
echo ""
