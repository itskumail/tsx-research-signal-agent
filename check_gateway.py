"""
check_gateway.py
Fast connection test for IB Gateway.
Exits 0 if IB Gateway is reachable on port 7497, non-zero otherwise.
Used by run_session.ps1 as a gate before invoking Claude Code.
"""
import socket
import sys

HOST = "127.0.0.1"
PORT = 7497  # IB Gateway paper trading port
TIMEOUT = 3  # seconds

def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((HOST, PORT))
        s.close()
        print(f"OK: IB Gateway reachable at {HOST}:{PORT}")
        sys.exit(0)
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        print(f"DOWN: IB Gateway not reachable at {HOST}:{PORT} ({e})")
        sys.exit(1)

if __name__ == "__main__":
    main()
