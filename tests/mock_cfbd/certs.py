"""Host-side helper that generates a throwaway self-signed certificate for the mock DoH endpoint."""

import subprocess
from pathlib import Path

CERT_FILE = "doh-cert.pem"
KEY_FILE = "doh-key.pem"
VALID_DAYS = "2"


def generate_self_signed(directory: Path, ip_address: str = "127.0.0.1") -> tuple[Path, Path]:
    """Write a fresh EC certificate and key valid for `ip_address` into `directory` via openssl."""
    cert, key = directory / CERT_FILE, directory / KEY_FILE
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:prime256v1",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            VALID_DAYS,
            "-subj",
            "/CN=mock-doh",
            "-addext",
            f"subjectAltName=IP:{ip_address}",
        ],
        check=True,
        capture_output=True,
    )
    key.chmod(0o600)
    return cert, key
