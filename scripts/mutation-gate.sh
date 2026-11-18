#!/usr/bin/env bash
#
# Fails when the mutation kill rate falls below the floor.
#
# Reads killed apart from timed-out. mutmut's summary line counts a timeout
# alongside a kill, and a mutant that times out was never shown to be caught
# by anything: it was shown to be slow. Counting it as a kill inflates the
# score by exactly the number of mutants nobody tested.
#
# Usage: mutation-gate.sh [FLOOR_PERCENT]
set -uo pipefail

readonly FLOOR="${1:-70}"
readonly OUTPUT="$(mktemp)"
trap 'rm -f "${OUTPUT}"' EXIT

python -m mutmut run > "${OUTPUT}" 2>&1
readonly RUN_STATUS=$?

# The counter line redraws in place, so the last carriage-separated field is
# the final tally.
summary="$(tr '\r' '\n' < "${OUTPUT}" | grep -E '[0-9]+/[0-9]+' | tail -1)"
if [ -z "${summary}" ]; then
  echo 'mutation run produced no tally, so nothing can be concluded from it' >&2
  tail -20 "${OUTPUT}" >&2
  exit 2
fi

total="$(sed -E 's|.*/([0-9]+).*|\1|' <<< "${summary}")"
killed="$(grep -oE '🎉 [0-9]+' <<< "${summary}" | grep -oE '[0-9]+')"
timeout="$(grep -oE '⏰ [0-9]+' <<< "${summary}" | grep -oE '[0-9]+')"

if [ -z "${total}" ] || [ -z "${killed}" ]; then
  echo "could not read the tally from: ${summary}" >&2
  exit 2
fi

rate="$(awk -v k="${killed}" -v t="${total}" 'BEGIN { printf "%.1f", (t ? k * 100 / t : 0) }')"
printf 'mutation: %s killed of %s = %s%%, %s timed out (counted apart), floor %s%%\n' \
  "${killed}" "${total}" "${rate}" "${timeout:-0}" "${FLOOR}"

if [ "${RUN_STATUS}" -ne 0 ]; then
  echo 'mutmut exited non-zero; treating the run as a failure rather than reading its tally' >&2
  exit "${RUN_STATUS}"
fi

awk -v r="${rate}" -v f="${FLOOR}" 'BEGIN { exit (r + 0 >= f + 0) ? 0 : 1 }' || {
  echo "mutation kill rate ${rate}% is below the ${FLOOR}% floor" >&2
  exit 1
}
