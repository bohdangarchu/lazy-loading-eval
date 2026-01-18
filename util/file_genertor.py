from random import getrandbits

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

if __name__ == "__main__":
    create_random_file(700, "big_file1")
    create_random_file(700, "big_file2")