"""fleetctl: send remote commands and inspect desired vs. reported state.

    ./fleetctl send TR-002 stop            ask a trailer to stop (it may refuse!)
    ./fleetctl send TR-002 start
    ./fleetctl send TR-002 stop --stale    a command that already expired in transit
    ./fleetctl resend <command_id>         send the SAME command again (idempotency)
    ./fleetctl status                      desired vs. reported + recent commands

The cloud side of the command path: record the command, set DESIRED state,
publish it with an MQTT message expiry, then wait for the trailer's answer.
"""
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg

from .commands import DESIRED, STATUS_SQL, create_command, publish
from .main import DB_DSN


def wait_for_answer(db, command_id, timeout):
    print("waiting for the trailer's answer", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = db.execute(f"SELECT {STATUS_SQL}, reason, ack_count FROM commands WHERE command_id = %s",
                         (command_id,)).fetchone()
        db.commit()
        if row and row[0] != "pending":
            print(f"\n  -> {row[0].upper()}: {row[1] or ''}")
            return
        print(".", end="", flush=True)
        time.sleep(0.5)
    print("\n  -> no answer yet (trailer offline?). It stays PENDING until it answers or expires.")
    print("     Check later with: ./fleetctl status")


def cmd_send(args):
    with psycopg.connect(DB_DSN) as db:
        try:
            command_id, _ = create_command(db, args.trailer, args.action, args.user,
                                           expires_s=args.expires, stale=args.stale)
        except ValueError as e:
            sys.exit(str(e))
        print(f"sent {args.action.upper()} to {args.trailer}   command_id {command_id}")
        print(f"DESIRED mode is now '{DESIRED[args.action]}'"
              + ("  (sent already expired, on purpose)" if args.stale else f"  (expires in {args.expires} s)"))
        wait_for_answer(db, command_id, timeout=15)


def cmd_resend(args):
    with psycopg.connect(DB_DSN) as db:
        row = db.execute("SELECT trailer_id, action, expires_at FROM commands WHERE command_id::text LIKE %s",
                         (args.command_id + "%",)).fetchone()
        if not row:
            sys.exit(f"no command {args.command_id}")
        trailer_id, action, expires_at = row
        full_id = db.execute("SELECT command_id FROM commands WHERE command_id::text LIKE %s",
                             (args.command_id + "%",)).fetchone()[0]
        before = db.execute("SELECT ack_count FROM commands WHERE command_id = %s", (full_id,)).fetchone()[0]
        db.commit()
        publish(trailer_id, full_id, action, max(expires_at, datetime.now(timezone.utc) + timedelta(seconds=30)))
        print(f"re-sent the SAME command {full_id} ({action} -> {trailer_id})")
        for _ in range(20):
            time.sleep(0.5)
            n = db.execute("SELECT ack_count FROM commands WHERE command_id = %s", (full_id,)).fetchone()[0]
            db.commit()
            if n > before:
                print(f"  -> trailer answered again (answers so far: {n}) but did NOT act again: idempotent")
                return
        print("  -> no second answer yet")


def cmd_status(args):
    with psycopg.connect(DB_DSN) as db:
        rows = db.execute(
            "SELECT trailer_id, coalesce(desired_mode, '-'), coalesce(reported_mode, '-'), "
            "CASE WHEN desired_mode IS NULL OR desired_mode = reported_mode THEN 'converged' "
            "ELSE 'NOT converged' END FROM device_state ORDER BY 1").fetchall()
        print("\nDESIRED vs REPORTED")
        print(f"  {'trailer':8} {'desired':9} {'reported':9} state")
        for r in rows:
            print(f"  {r[0]:8} {r[1]:9} {r[2]:9} {r[3]}")
        rows = db.execute(
            f"SELECT to_char(created_at AT TIME ZONE 'America/Phoenix', 'HH24:MI:SS'), trailer_id, action, "
            f"{STATUS_SQL}, coalesce(reason, ''), ack_count, left(command_id::text, 8) "
            f"FROM commands ORDER BY created_at DESC LIMIT 10").fetchall()
        print("\nRECENT COMMANDS (newest first)")
        for r in rows:
            print(f"  {r[0]}  {r[1]:7} {r[2]:5} {r[3].upper():8} answers={r[5]}  id={r[6]}  {r[4]}")
        print()


def main():
    p = argparse.ArgumentParser(prog="fleetctl", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("trailer")
    s.add_argument("action")
    s.add_argument("--expires", type=int, default=60, help="seconds until the command is void")
    s.add_argument("--stale", action="store_true", help="send a command that has already expired")
    s.add_argument("--user", default="ryan@demo")
    s.set_defaults(fn=cmd_send)
    r = sub.add_parser("resend")
    r.add_argument("command_id", help="full id or its first 8 characters")
    r.set_defaults(fn=cmd_resend)
    st = sub.add_parser("status")
    st.set_defaults(fn=cmd_status)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
