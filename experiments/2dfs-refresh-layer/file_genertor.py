from random import getrandbits
import json

def create_random_file(file_size_mb, filename):
    file_size_bytes = file_size_mb * 1024 * 1024

    with open(filename, "wb") as f:
        while file_size_bytes > 0:
            # Generate random bytes (chunk size of 1 MB)
            random_bytes = getrandbits(8 * 1024 * 1024)  # 1 MB
            # Write the random bytes to the file
            f.write(random_bytes.to_bytes(1024 * 1024, byteorder='big'))
            # Update remaining bytes
            file_size_bytes -= 1024 * 1024

def write_2dfs_json(src_files, output_path: str = "2dfs.json") -> None:
    data = {
        "allotments": [
            {
                "src": f"./{src}",
                "dst": f"/{src}",
                "row": 0,
                "col": idx,
            }
            for idx, src in enumerate(src_files)
        ]
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)

if __name__ == "__main__":
    file_names = ["big_file1", "big_file2"]
    for file_name in file_names:
        create_random_file(700, file_name)
    write_2dfs_json(file_names)