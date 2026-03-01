import sys
import time

def main():
    path = sys.argv[1]

    before = time.time()
    with open(path, "rb") as f:
        data = f.read()
    after = time.time()

    duration = after - before
    fmt = "%Y-%m-%d %H:%M:%S.%f"
    print(f"before: {time.strftime(fmt, time.localtime(before))}")
    print(f"after:  {time.strftime(fmt, time.localtime(after))}")
    print(f"duration: {duration:.6f}s")

    _ = len(data)

if __name__ == "__main__":
    main()
