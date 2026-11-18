#!/usr/bin/env bash
#
# Regenerates requirements.lock from requirements.txt.
#
# requirements.txt pins versions. A pinned version can still be re-uploaded
# under the same number, or served by a mirror nobody audited, so the lock
# adds a hash per artifact and pip refuses anything that does not match byte
# for byte.
#
# Run after changing requirements.txt. A test fails when the two disagree.
set -euo pipefail

readonly ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly SCRATCH="$(mktemp -d)"
trap 'rm -rf "${SCRATCH}"' EXIT

cd "${ROOT}"
python -m pip download -r requirements.txt -d "${SCRATCH}/wheels" --quiet

python - "${SCRATCH}/wheels" "${ROOT}/requirements.lock" <<'PYTHON'
import hashlib
import pathlib
import re
import sys

wheels, destination = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])

# One distribution can ship several artifacts, and the lock has to accept
# whichever one pip picks for the platform it runs on.
by_release: dict[str, list[str]] = {}
for path in sorted(wheels.iterdir()):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if path.name.endswith(".whl"):
        name, version = path.name.split("-")[0], path.name.split("-")[1]
    else:
        base = re.sub(r"\.tar\.gz$|\.zip$", "", path.name)
        name, _, version = base.rpartition("-")
    by_release.setdefault(f"{name.replace('_', '-').lower()}=={version}", []).append(digest)

lines = [
    "# Generated, do not edit by hand.",
    "#",
    "# Exact pins AND a hash per artifact, so pip refuses anything that does",
    "# not match byte for byte. requirements.txt pins versions; a version can",
    "# still be re-uploaded or served by a compromised mirror, and a hash is",
    "# what closes that gap.",
    "#",
    "# Regenerate with scripts/generate-lock.sh after changing requirements.txt.",
    "",
]
for release in sorted(by_release):
    lines.append(f"{release} \\")
    digests = sorted(by_release[release])
    for index, digest in enumerate(digests):
        tail = "" if index == len(digests) - 1 else " \\"
        lines.append(f"    --hash=sha256:{digest}{tail}")

destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"locked {len(by_release)} distributions")
PYTHON
