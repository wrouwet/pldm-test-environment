#!/bin/bash
# Run the suite and tee a clean (ANSI-stripped) copy to test_report.txt,
# same convention as the sibling ipmi-test-environment / mctp-test-
# environment run scripts. pipefail so a real test failure isn't hidden
# behind tee's exit code.
set -euo pipefail
cd "$(dirname "$0")"
.venv/bin/pytest tests/ "$@" 2>&1 | tee >(sed -E 's/\x1b\[[0-9;]*[a-zA-Z]//g' > test_report.txt)
