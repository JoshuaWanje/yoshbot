import sys, traceback

sys.argv = ["movers.py", "web"]

try:
    import movers
    movers.main()
except SystemExit as e:
    with open("debug_output.txt", "w") as f:
        f.write(f"SystemExit was called with: {e}\n")
except Exception:
    with open("debug_output.txt", "w") as f:
        f.write(traceback.format_exc())