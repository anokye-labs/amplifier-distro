"""Tests for self-signed TLS certificate generation.

Validates:
1. generate_self_signed_cert creates cert and key files
2. Files end with .pem
3. Cert is loadable by ssl.SSLContext.load_cert_chain
4. Reuses existing cert (same mtime)
5. Creates cert_dir if missing
"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

from amplifier_distro.server.tls import generate_self_signed_cert


class TestGenerateSelfSignedCert:
    def test_creates_cert_and_key_files(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        assert cert_path.exists()
        assert key_path.exists()

    def test_files_end_with_pem(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        assert cert_path.suffix == ".pem"
        assert key_path.suffix == ".pem"

    def test_cert_loadable_by_ssl_context(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))

    def test_reuses_existing_cert(self, tmp_path: Path) -> None:
        cert_path_1, key_path_1 = generate_self_signed_cert(tmp_path)
        mtime_cert = cert_path_1.stat().st_mtime
        mtime_key = key_path_1.stat().st_mtime

        cert_path_2, key_path_2 = generate_self_signed_cert(tmp_path)
        assert cert_path_2.stat().st_mtime == mtime_cert
        assert key_path_2.stat().st_mtime == mtime_key

    def test_creates_cert_dir_if_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "certs"
        assert not nested.exists()
        cert_path, key_path = generate_self_signed_cert(nested)
        assert nested.is_dir()
        assert cert_path.exists()
        assert key_path.exists()

    def test_key_file_permissions(self, tmp_path: Path) -> None:
        _cert_path, key_path = generate_self_signed_cert(tmp_path)
        mode = os.stat(key_path).st_mode & 0o777
        assert mode == 0o600

    def test_cert_file_names(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        assert cert_path.name == "self-signed.pem"
        assert key_path.name == "self-signed-key.pem"

    def test_returns_paths_in_cert_dir(self, tmp_path: Path) -> None:
        cert_path, key_path = generate_self_signed_cert(tmp_path)
        assert cert_path.parent == tmp_path
        assert key_path.parent == tmp_path
