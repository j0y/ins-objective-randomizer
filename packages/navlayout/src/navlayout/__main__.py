from .cli import main

if __name__ == "__main__":
    # `main` returns the exit code - a refusal, a kill switch that tripped, a
    # map that emitted nothing - and dropping it on the floor makes every one
    # of those look like success to a caller. tools/make-presets.sh read `0`
    # for "16 of 16 layouts refused" for as long as this said `main()`.
    raise SystemExit(main())
