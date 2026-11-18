#!/usr/bin/env bash
#
# Fails when prose carries a character that marks machine drafting.
#
# The em-dash is banned outright and the en-dash is allowed only between
# digits, per the writing rules. The invisible characters belong to no rule
# because nobody types them deliberately: they arrive through copy and paste
# from a rendered page, and they survive into published content where any
# reader's tooling can grep for them.
#
# This checks characters and nothing else. Anthropic's text watermark lives in
# word choice, and its documentation states that nothing gets added to the
# output, so a clean run here says nothing at all about the watermark. Keeping
# that distinction visible matters more than catching either one.
#
# Written in bash with grep -P rather than in a scripting language, so the same
# file installs into every repository without adding an interpreter to any
# toolchain. The Maven image carries no python3; every one of these carries
# GNU grep.
#
# Usage: check-text-characters.sh PATH [PATH ...]
set -uo pipefail

# The patterns below match raw UTF-8 bytes, which requires grep to read bytes
# rather than characters, so the locale gets pinned rather than inherited. C
# exists in every environment, unlike C.UTF-8, and leaving the locale to the
# caller made this check find nothing under a UTF-8 locale and refuse to run
# under a C one.
export LC_ALL=C

# Patterns match raw UTF-8 bytes rather than code points. A \x{2014} escape
# needs PCRE in UTF mode, which depends on the locale, and the Maven and Rust
# toolchain images run a C locale where grep rejects it outright. Byte
# sequences behave identically in every locale.
#
# Em-dash, the zero-width family, the invisible spaces, the curly quotes, and
# the ellipsis character.
readonly BANNED='\xE2\x80\x94|\xE2\x80[\x8B-\x8D]|\xE2\x81\xA0|\xC2\xA0|\xE2\x80\xAF|\xE2\x80\x89|\xC2\xAD|\xCD\x8F|\xE2\x80[\x98\x99\x9C\x9D]|\xE2\x80\xA6'
# An en-dash earns one exemption, a range between digits such as 2026-2027.
readonly EN_DASH_CONNECTOR='(?<![0-9])\xE2\x80\x93|\xE2\x80\x93(?![0-9])'
# A byte-order mark at the very start records the file's encoding, which .NET
# and MSBuild tooling write on purpose. Anywhere else it arrived by accident,
# so the leading one gets stripped before the search rather than reported.
readonly STRAY_BOM='\xEF\xBB\xBF'

readonly EXTENSIONS='md|mdx|txt|astro|ts|tsx|js|jsx|json|yml|yaml|py|rs|go|java|cs|sh|ps1|sql|toml|xml|csproj|html|css'
# Generated output carries whatever characters its generator chose, so a
# mutation report or a coverage page says nothing about prose written here.
readonly SKIP='node_modules|/target/|/dist/|/\.git/|/\.astro/|mutants\.out|/bin/|/obj/|__pycache__|/\.venv/|/carousels/|StrykerOutput|TestResults|/coverage/|pit-reports|playwright-report|/\.nyc_output/|htmlcov|/cypress/(videos|screenshots)/|\.backup-|/reports/'

if [ "$#" -eq 0 ]; then
  echo 'usage: check-text-characters.sh PATH [PATH ...]' >&2
  exit 2
fi

# A pipeline reports the status of its last command and nothing else, which is
# why both of the fail-open defects below lived here: sed failing on a file it
# could not read and grep answering 1 on the empty input it got read as a clean
# file, and find failing on a subtree it could not read read as a smaller tree.
# Both readers now write to a file and get their status checked on its own.
readonly SCRATCH="$(mktemp -d)"
trap 'rm -rf "${SCRATCH}"' EXIT
readonly LIST="${SCRATCH}/files"
readonly WALK_ERRORS="${SCRATCH}/walk-errors"
readonly SCRUBBED="${SCRATCH}/scrubbed"

scanned=0
findings=0
broke=0

# Reads the file once, with the leading byte-order mark removed so a deliberate
# encoding marker never reports while the same character mid-file still does.
# Answers non-zero when the file could not be read, which the caller treats as a
# failure rather than as a file with nothing in it.
scrub() {
  local file="$1"
  if ! sed '1s/^\xEF\xBB\xBF//' "${file}" > "${SCRUBBED}" 2>/dev/null; then
    printf 'could not read %s\n' "${file}" >&2
    return 1
  fi
}

report() {
  local file="$1" pattern="$2" reason="$3" hits status
  # -a treats the scrubbed copy as text, so a stray control byte cannot silence
  # a match. The file name comes from the caller because the bytes being read
  # live in the scrubbed copy, which is not the name a reader needs to see.
  hits="$(grep -naP "${pattern}" "${SCRUBBED}")"
  status=$?
  # grep answers 0 for a match and 1 for none. Anything higher means grep
  # itself failed, and a check that treats its own failure as a clean result
  # reports green for whatever it never read.
  if [ "${status}" -gt 1 ]; then
    printf 'grep failed with status %s on %s\n' "${status}" "${file}" >&2
    broke=$((broke + 1))
    return
  fi
  if [ -n "${hits}" ]; then
    while IFS= read -r hit; do
      printf '  %s:%s: %s\n' "${file}" "${hit%%:*}" "${reason}" >&2
      findings=$((findings + 1))
    done <<< "${hits}"
  fi
}

for root in "$@"; do
  if [ ! -e "${root}" ]; then
    echo "path does not exist: ${root}" >&2
    exit 2
  fi
  # find runs alone rather than at the head of a pipeline, so its status
  # survives, and its stderr lands in a file rather than in /dev/null, where a
  # directory it could not read looked exactly like a directory holding
  # nothing. Whatever it did reach still gets scanned, because findings from
  # part of a tree beat no findings at all, and the failure still fails the run.
  if ! find "${root}" -type f > "${LIST}" 2> "${WALK_ERRORS}"; then
    printf 'could not walk every path under %s\n' "${root}" >&2
    sed 's/^/  /' "${WALK_ERRORS}" >&2
    broke=$((broke + 1))
  fi

  while IFS= read -r file; do
    scanned=$((scanned + 1))
    if ! scrub "${file}"; then
      broke=$((broke + 1))
      continue
    fi
    report "${file}" "${BANNED}" 'banned character: em-dash, an invisible character, a curly quote, or an ellipsis character'
    report "${file}" "${EN_DASH_CONNECTOR}" 'en-dash outside a number range; use a comma and a conjunction'
    report "${file}" "${STRAY_BOM}" 'stray byte-order mark inside the file'
  done < <(grep -E "\.(${EXTENSIONS})$" "${LIST}" \
    | grep -Ev "${SKIP}" \
    | sort)
done

if [ "${broke}" -gt 0 ]; then
  echo >&2
  echo "text character check could not read ${broke} path(s)." >&2
  echo 'Treat this as a failure rather than a pass: a check that could not read a file knows nothing about it.' >&2
  exit 2
fi

if [ "${findings}" -gt 0 ]; then
  echo >&2
  echo "text character check failed, ${findings} finding(s) across ${scanned} files." >&2
  echo 'Each one reads as machine drafting to anybody who has seen AI output, and each one is a character a reader can grep for.' >&2
  exit 1
fi

printf 'text characters ok: %s files scanned, no banned characters\n' "${scanned}"
