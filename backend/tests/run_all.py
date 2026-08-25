#!/usr/bin/env python3
"""
Run every offline test in one command.

    cd backend
    python tests/run_all.py

Before this existed the suite was real but awkward to actually run:
sixteen files, each invoked by hand, each needing PYTHONPATH set first or
it fails on an import with an error that looks like a broken test rather
than a missing environment variable. A suite that takes a paragraph of
instructions to run is a suite that gets run when someone remembers.

Skipped here, deliberately, are the tests that reach the outside world -
they need an API key or would write to a real Loyverse account, so they
can't run unattended and shouldn't gate anything. They're still there to
run by hand when the thing they check is what changed.
"""

import os
import subprocess
import sys

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(BACKEND, "tests")

# Needs a real credential or writes to a real account - not runnable in CI.
NEEDS_NETWORK = {
    "test_scan_invoice.py",   # GEMINI_API_KEY, reads a real photo
    "test_sale_flow.py",      # creates a receipt in a real Loyverse account
}


def offline_tests() -> list[str]:
    return sorted(
        name for name in os.listdir(TESTS)
        if name.startswith("test_") and name.endswith(".py")
        and name not in NEEDS_NETWORK
    )


def main() -> int:
    env = {**os.environ, "PYTHONPATH": BACKEND}
    failures, total_checks = [], 0

    for name in offline_tests():
        print(f"{name:<34} ", end="", flush=True)
        proc = subprocess.run([sys.executable, os.path.join(TESTS, name)],
                              cwd=BACKEND, env=env,
                              capture_output=True, text=True)
        checks = _checks_in(proc.stdout)
        total_checks += checks
        if proc.returncode == 0:
            print(f"ok    {checks} checks")
        else:
            print("FAILED")
            failures.append((name, proc.stdout, proc.stderr))

    print("-" * 52)
    if not failures:
        print(f"all {len(offline_tests())} files ok - {total_checks} checks passed")
        return 0

    # The failing checks themselves, not just which file - a name alone
    # sends whoever is reading this back to re-run it by hand.
    for name, out, err in failures:
        print(f"\n=== {name} ===")
        for line in out.splitlines():
            if "[FAIL]" in line:
                print(" ", line.strip())
        if err.strip():
            print(err.strip()[-1500:])
    print(f"\n{len(failures)} of {len(offline_tests())} files failed")
    return 1


def _checks_in(output: str) -> int:
    for line in reversed(output.splitlines()):
        if line.endswith("checks passed"):
            try:
                return int(line.split("/")[0])
            except ValueError:
                return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
