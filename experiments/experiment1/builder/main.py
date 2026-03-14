import sys
import time
from datetime import datetime

def main():
    path = sys.argv[1]

    before = time.time()
    with open(path, "rb") as f:
        data = f.read()
    after = time.time()

    duration = after - before
    fmt = "%Y-%m-%d %H:%M:%S.%f"
    print(f"before: {datetime.fromtimestamp(before).strftime(fmt)}")
    print(f"after:  {datetime.fromtimestamp(after).strftime(fmt)}")
    print(f"duration: {duration:.6f}s")

    _ = len(data)

if __name__ == "__main__":
    main()
