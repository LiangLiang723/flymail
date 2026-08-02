"""FlyMail V2 formal Worker entrypoint."""

from flymail.workers.main import main


if __name__ == "__main__":
    raise SystemExit(main())
