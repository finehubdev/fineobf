import argparse
import sys

from .obfuscator import obfuscate

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="darcobfuscator",
        description="Darc Obfuscator — custom-VM Luau obfuscator.")
    ap.add_argument("input", nargs="?", help="input .lua file (or - for stdin)")
    ap.add_argument("-o", "--output", help="output file (default: stdout)")
    ap.add_argument("--no-roblox-check", action="store_true",
                    help="disable the Roblox environment integrity check")
    ap.add_argument("--no-anti-tamper", action="store_true",
                    help="disable anti-tamper / anti-deobfuscation guards")
    ap.add_argument("--executor", action="store_true",
                    help="build for scripts run THROUGH an exploit executor: "
                         "turns off the anti-tamper/env-logging/hook guards that "
                         "would otherwise detect and refuse the executor. Keeps "
                         "the VM, encryption and per-build mutation.")
    ap.add_argument("--anti-log", dest="anti_log", action="store_true",
                    default=None,
                    help="inject decoy env noise to pollute env-logging traces "
                         "(default: on for --executor builds)")
    ap.add_argument("--no-anti-log", dest="anti_log", action="store_false",
                    help="disable decoy env-noise injection")
    ap.add_argument("--no-rename", action="store_true",
                    help="disable identifier renaming (for debugging)")
    ap.add_argument("--silent-fail", action="store_true",
                    help="on a failed guard, return silently instead of erroring")
    ap.add_argument("--diagnose", action="store_true",
                    help="emit a descriptive error naming which guard failed "
                         "(for testing why a build refuses to run; do not ship)")
    ap.add_argument("--seed", type=int, help="deterministic naming seed")
    args = ap.parse_args(argv)

    if args.input in (None, "-"):
        source = sys.stdin.read()
    else:
        with open(args.input, "r", encoding="utf-8") as f:
            source = f.read()

    opts = {
        "roblox_check": not args.no_roblox_check,
        "anti_tamper": not args.no_anti_tamper,
        "rename": not args.no_rename,
        "on_fail": "silent" if args.silent_fail else "error",
        "diagnostic": args.diagnose,
        "target": "executor" if args.executor else "roblox",
        "anti_log": args.anti_log,
        "seed": args.seed,
    }

    try:
        out = obfuscate(source, opts)
    except SyntaxError as e:
        print(f"darcobfuscator: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"darcobfuscator: internal error: {e}", file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"wrote {len(out)} bytes -> {args.output}", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
