"""
Entry point for PyInstaller frozen executable.
This wrapper properly sets up the Python path before importing the daemon package.
"""
import sys
import os

# When frozen by PyInstaller, sys._MEIPASS contains the bundled files directory
if getattr(sys, 'frozen', False):
    # Get the directory where the executable is located
    app_dir = os.path.dirname(sys.executable)
    
    # Add app directory to Python path so 'daemon' package can be imported
    if app_dir not in sys.path:
        sys.path.insert(0, app_dir)
    
    # Load .env file from app directory if it exists
    env_file = os.path.join(app_dir, '.env')
    if os.path.isfile(env_file):
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                # Skip comments and empty lines
                if line and not line.startswith('#'):
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        # Only set if not already set (env vars take precedence)
                        if key not in os.environ:
                            os.environ[key] = value

# Now import and run the main function
import daemon.__main__

# PP1-adjacent (2026-10-03, report-delivery-bug-family) — the
# 5th env-poison incident (occurrence #5 producer-deleter) came
# from a DevOps ``--version`` probe that BOOTED THE FULL STACK
# against ``ensemble_prod`` because the entry had no early-exit
# on the probe flag. The boot triggered ``_boot_db_preflight()``
# (DB connect under the live env) and the operator's
# ``QUEUE_DISCARD_ON_STARTUP`` armed config, so the probe alone
# wiped live task/message rows. The 4-line check below
# short-circuits the probe BEFORE the DB preflight — the import
# of ``daemon.__main__`` above is a module init (no DB connect),
# but ``_boot_db_preflight()`` is the dangerous step. ``-V`` and
# ``--version`` are the conventional probe flags; the check is
# ``in sys.argv`` (not argparse) because the entry does not own
# an arg parser and adding one is out of scope. The early-exit
# reads ``daemon.__version__`` (baked at ``daemon/__init__.py``)
# — no DB, no config load, no env side effects beyond what the
# .env block above already did. The follow-up commission owns
# the root fix: heartbeat/boot-epoch-gated refusal of
# ``discard_on_startup`` when a foreign live daemon exists
# (explicitly out of scope per the 2026-10-03 mid-flight
# direction).
if "--version" in sys.argv or "-V" in sys.argv:
    from daemon import __version__ as _ensemble_version
    print(f"Ensemble v{_ensemble_version}")
    sys.exit(0)

# Boot DB preflight (F-DR1-1, P2.3 B5.6): the FROZEN entry runs it HERE —
# before main() loads config or starts uvicorn — so the launcher's
# tempfail contract (exit 75 unreachable / 78 auth-refused, ADR-011) is
# owned by this entry itself, not inherited from main()'s internal call
# ordering. Same underlying function as the `python -m daemon` dev entry:
# same BOOT_DB_TIMEOUT_S budget, same exit codes, same log lines.
# main(run_preflight=False) then skips its internal call, so the probe
# fires EXACTLY ONCE per boot on every entry.
daemon.__main__._boot_db_preflight()
daemon.__main__.main(run_preflight=False)
