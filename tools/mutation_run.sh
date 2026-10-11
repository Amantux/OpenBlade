#!/usr/bin/env bash
# Mutation lane: run mutmut (config: [tool.mutmut] in pyproject.toml) over the
# safety-critical modules and ratchet surviving mutants against
# mutation/baseline.txt. More survivors than the baseline for any module => exit 1.
#
# Env:
#   MUTATION_MODULES      space-separated module paths to mutate
#                         (default: every module listed in mutation/baseline.txt)
#   MUTATION_TIMEOUT      wall-clock budget in seconds (default 3600); exceeding it exits 2
#   MUTATION_MAX_CHILDREN mutmut worker count (default: mutmut's own, = cpu count)
#   PYTHON                interpreter with mutmut installed (default: python)
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-python}"
BASELINE="mutation/baseline.txt"
TIMEOUT="${MUTATION_TIMEOUT:-3600}"

if [[ -n "${MUTATION_MODULES:-}" ]]; then
  read -r -a modules <<<"$MUTATION_MODULES"
else
  mapfile -t modules < <(grep -vE '^\s*(#|$)' "$BASELINE" | awk '{print $1}')
fi

# mutmut names mutants "<dotted.module>.<x_func>__mutmut_<n>"; select by module glob.
globs=()
for mod in "${modules[@]}"; do
  [[ -f "$mod" ]] || { echo "mutation: no such module: $mod" >&2; exit 2; }
  dotted="${mod%.py}"
  globs+=("${dotted//\//.}.*")
done

# Fresh run every time: a stale mutants/ cache would report old results.
rm -rf mutants

run_args=(run)
[[ -n "${MUTATION_MAX_CHILDREN:-}" ]] && run_args+=(--max-children "$MUTATION_MAX_CHILDREN")
set +e
timeout "$TIMEOUT" "$PY" -m mutmut "${run_args[@]}" "${globs[@]}"
rc=$?
set -e
if [[ $rc -eq 124 ]]; then
  echo "mutation: time budget of ${TIMEOUT}s exceeded - result is NOT a pass" >&2
  exit 2
elif [[ $rc -ne 0 ]]; then
  echo "mutation: mutmut run failed (exit $rc)" >&2
  exit "$rc"
fi

results="$("$PY" -m mutmut results)"
fail=0
for mod in "${modules[@]}"; do
  dotted="${mod%.py}"
  prefix="${dotted//\//.}."
  survived=$(grep -F "    ${prefix}" <<<"$results" | grep -cE ': (survived|no tests)$' || true)
  allowed=$(awk -v m="$mod" '$1 == m {print $2}' "$BASELINE")
  if [[ -z "$allowed" ]]; then
    echo "mutation: $mod survived=$survived but has no baseline entry - add one to $BASELINE" >&2
    fail=1
  elif ((survived > allowed)); then
    echo "mutation: $mod survived=$survived > baseline=$allowed (new survivors: kill them or justify)" >&2
    fail=1
  else
    echo "mutation: $mod survived=$survived <= baseline=$allowed"
    ((survived < allowed)) && echo "mutation:   ratchet: lower $mod baseline to $survived"
  fi
done
exit "$fail"
