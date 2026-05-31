import socket
import webbrowser
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

PORT = 1234
TIMEOUT = 0.6
MAX_WORKERS = 100

# Scan these first because they already matched your laptop/phone situation
PREFERRED_THIRDS = [24, 33]


def generate_ips():
    seen = set()

    # Preferred ranges first
    for third in PREFERRED_THIRDS:
        for fourth in range(10, 255):  # two or three digits
            ip = f"10.0.{third}.{fourth}"
            seen.add(ip)
            yield ip

    # Then the rest: 10.0.10.x to 10.0.99.x
    for third in range(10, 100):       # two digits
        if third in PREFERRED_THIRDS:
            continue

        for fourth in range(10, 255):  # two or three digits
            ip = f"10.0.{third}.{fourth}"
            if ip not in seen:
                yield ip


def check_ip(ip):
    try:
        with socket.create_connection((ip, PORT), timeout=TIMEOUT):
            return f"http://{ip}:{PORT}/"
    except OSError:
        return None


def main():
    print("Scanning pattern: 10.0.[10-99].[10-254]:1234")
    print("Stopping at the first match...\n")

    ip_iterator = iter(generate_ips())

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        pending = set()

        # Start first batch
        for _ in range(MAX_WORKERS):
            try:
                ip = next(ip_iterator)
            except StopIteration:
                break
            pending.add(executor.submit(check_ip, ip))

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)

            for future in done:
                result = future.result()

                if result:
                    print("Found:", result)
                    webbrowser.open(result)

                    # Stop immediately
                    executor.shutdown(wait=False, cancel_futures=True)
                    return

                # Add one new IP for each completed failed check
                try:
                    ip = next(ip_iterator)
                    pending.add(executor.submit(check_ip, ip))
                except StopIteration:
                    pass

    print("No server found.")


if __name__ == "__main__":
    main()