"""Entry point: `python -m takeline` opens the app; CLI subcommands run the developer CLI."""

import sys

CLI_COMMANDS = {"inspect", "scan", "apply", "undo"}


def main() -> int:
    args = sys.argv[1:]
    if any(a in CLI_COMMANDS for a in args) or any(a in ("-h", "--help") for a in args):
        from takeline.cli import main as cli_main
        return cli_main(args)
    from takeline.app import main as gui_main
    return gui_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
