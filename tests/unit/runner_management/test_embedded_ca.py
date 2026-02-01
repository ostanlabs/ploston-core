"""Unit tests for EmbeddedCA.

Tests for S-183: Embedded CA & TLS
- UT-083: test_ca_generation
- UT-084: test_runner_cert_provision
- UT-085: test_ca_download_endpoint
- UT-086: test_tls_handshake
"""

import ssl
from datetime import UTC, datetime

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from ploston_core.runner_management.embedded_ca import (
    CertificateInfo,
    EmbeddedCA,
)


class TestCAGeneration:
    """UT-083: CA keypair generated."""

    def test_ca_init_creates_directory(self, tmp_path):
        """Test CA initialization creates directory."""
        ca_dir = tmp_path / "ca"
        ca = EmbeddedCA(ca_dir=ca_dir)
        ca.initialize()

        assert ca_dir.exists()
        assert ca.ca_key_path.exists()
        assert ca.ca_cert_path.exists()

    def test_ca_generates_valid_keypair(self, tmp_path):
        """Test CA generates valid RSA keypair."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        # Load and verify key
        key_pem = ca.ca_key_path.read_bytes()
        key = serialization.load_pem_private_key(key_pem, password=None)
        assert key.key_size == 4096

    def test_ca_generates_self_signed_cert(self, tmp_path):
        """Test CA generates self-signed certificate."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        # Load and verify certificate
        cert_pem = ca.ca_cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)

        # Self-signed: subject == issuer
        assert cert.subject == cert.issuer
        assert "Ploston Runner CA" in cert.subject.rfc4514_string()

    def test_ca_cert_is_ca(self, tmp_path):
        """Test CA certificate has CA basic constraint."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        cert_pem = ca.ca_cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)

        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert bc.value.ca is True

    def test_ca_cert_validity_period(self, tmp_path):
        """Test CA certificate has correct validity period."""
        ca = EmbeddedCA(ca_dir=tmp_path, ca_validity_days=365)
        ca.initialize()

        cert_pem = ca.ca_cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_pem)

        now = datetime.now(UTC)
        assert cert.not_valid_before_utc <= now
        # Should be valid for ~365 days (with some tolerance)
        validity = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert 364 <= validity.days <= 366

    def test_ca_key_permissions(self, tmp_path):
        """Test CA private key has restricted permissions."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        # Check permissions (owner read/write only)
        mode = ca.ca_key_path.stat().st_mode & 0o777
        assert mode == 0o600

    def test_ca_loads_existing(self, tmp_path):
        """Test CA loads existing keys on reinitialize."""
        ca1 = EmbeddedCA(ca_dir=tmp_path)
        ca1.initialize()

        # Get original cert serial
        cert1 = x509.load_pem_x509_certificate(ca1.ca_cert_path.read_bytes())
        serial1 = cert1.serial_number

        # Create new CA instance and initialize (should load, not regenerate)
        ca2 = EmbeddedCA(ca_dir=tmp_path)
        ca2.initialize()

        cert2 = x509.load_pem_x509_certificate(ca2.ca_cert_path.read_bytes())
        serial2 = cert2.serial_number

        assert serial1 == serial2  # Same cert loaded


class TestRunnerCertProvision:
    """UT-084: Runner cert signed by CA."""

    def test_generate_runner_cert(self, tmp_path):
        """Test generating a runner certificate."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        key_pem, cert_pem = ca.generate_runner_cert(
            runner_name="test-runner",
            runner_id="runner_abc123",
        )

        assert key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
        assert cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")

    def test_runner_cert_signed_by_ca(self, tmp_path):
        """Test runner certificate is signed by CA."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        _, cert_pem = ca.generate_runner_cert(
            runner_name="test-runner",
            runner_id="runner_abc123",
        )

        runner_cert = x509.load_pem_x509_certificate(cert_pem)
        ca_cert = x509.load_pem_x509_certificate(ca.ca_cert_path.read_bytes())

        # Issuer should be CA subject
        assert runner_cert.issuer == ca_cert.subject

    def test_runner_cert_contains_runner_info(self, tmp_path):
        """Test runner certificate contains runner name and ID."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        _, cert_pem = ca.generate_runner_cert(
            runner_name="my-laptop",
            runner_id="runner_xyz789",
        )

        cert = x509.load_pem_x509_certificate(cert_pem)
        subject = cert.subject.rfc4514_string()

        # CN contains runner name, OU contains runner ID
        assert "runner-my-laptop" in subject
        assert "OU=runner_xyz789" in subject

    def test_runner_cert_not_ca(self, tmp_path):
        """Test runner certificate is not a CA."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        _, cert_pem = ca.generate_runner_cert(
            runner_name="test-runner",
            runner_id="runner_abc123",
        )

        cert = x509.load_pem_x509_certificate(cert_pem)
        bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
        assert bc.value.ca is False

    def test_runner_cert_client_auth(self, tmp_path):
        """Test runner certificate has client auth extended key usage."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        _, cert_pem = ca.generate_runner_cert(
            runner_name="test-runner",
            runner_id="runner_abc123",
        )

        cert = x509.load_pem_x509_certificate(cert_pem)
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        assert x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH in eku.value

    def test_runner_cert_validity_period(self, tmp_path):
        """Test runner certificate has correct validity period."""
        ca = EmbeddedCA(ca_dir=tmp_path, cert_validity_days=30)
        ca.initialize()

        _, cert_pem = ca.generate_runner_cert(
            runner_name="test-runner",
            runner_id="runner_abc123",
        )

        cert = x509.load_pem_x509_certificate(cert_pem)
        validity = cert.not_valid_after_utc - cert.not_valid_before_utc
        assert 29 <= validity.days <= 31

    def test_runner_cert_unique_serial(self, tmp_path):
        """Test each runner certificate has unique serial number."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        _, cert1_pem = ca.generate_runner_cert("runner1", "id1")
        _, cert2_pem = ca.generate_runner_cert("runner2", "id2")

        cert1 = x509.load_pem_x509_certificate(cert1_pem)
        cert2 = x509.load_pem_x509_certificate(cert2_pem)

        assert cert1.serial_number != cert2.serial_number

    def test_generate_runner_cert_without_init_fails(self, tmp_path):
        """Test generating cert without initialization fails."""
        ca = EmbeddedCA(ca_dir=tmp_path)

        with pytest.raises(RuntimeError, match="CA not initialized"):
            ca.generate_runner_cert("test", "id")


class TestCADownloadEndpoint:
    """UT-085: /runner/ca.crt accessible."""

    def test_get_ca_cert_pem(self, tmp_path):
        """Test getting CA certificate in PEM format."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        pem = ca.get_ca_cert_pem()

        assert pem.startswith(b"-----BEGIN CERTIFICATE-----")
        assert pem.endswith(b"-----END CERTIFICATE-----\n")

    def test_get_ca_cert_pem_matches_file(self, tmp_path):
        """Test PEM output matches file content."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        pem = ca.get_ca_cert_pem()
        file_content = ca.ca_cert_path.read_bytes()

        assert pem == file_content

    def test_get_ca_cert_pem_without_init_fails(self, tmp_path):
        """Test getting PEM without initialization fails."""
        ca = EmbeddedCA(ca_dir=tmp_path)

        with pytest.raises(RuntimeError, match="CA not initialized"):
            ca.get_ca_cert_pem()

    def test_get_ca_cert_info(self, tmp_path):
        """Test getting CA certificate info."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        info = ca.get_ca_cert_info()

        assert isinstance(info, CertificateInfo)
        assert "Ploston Runner CA" in info.subject
        assert info.is_ca is True
        assert info.not_before <= datetime.now(UTC)
        assert info.not_after > datetime.now(UTC)

    def test_get_ca_cert_info_without_init_fails(self, tmp_path):
        """Test getting info without initialization fails."""
        ca = EmbeddedCA(ca_dir=tmp_path)

        with pytest.raises(RuntimeError, match="CA not initialized"):
            ca.get_ca_cert_info()


class TestTLSHandshake:
    """UT-086: WSS connection validates cert."""

    def test_generate_server_cert(self, tmp_path):
        """Test generating a server certificate."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        key_pem, cert_pem = ca.generate_server_cert(
            hostname="localhost",
        )

        assert key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
        assert cert_pem.startswith(b"-----BEGIN CERTIFICATE-----")

    def test_server_cert_signed_by_ca(self, tmp_path):
        """Test server certificate is signed by CA."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        _, cert_pem = ca.generate_server_cert(hostname="localhost")

        server_cert = x509.load_pem_x509_certificate(cert_pem)
        ca_cert = x509.load_pem_x509_certificate(ca.ca_cert_path.read_bytes())

        assert server_cert.issuer == ca_cert.subject

    def test_server_cert_has_san(self, tmp_path):
        """Test server certificate has Subject Alternative Name."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        _, cert_pem = ca.generate_server_cert(
            hostname="localhost",
            alt_names=["127.0.0.1", "ploston.local"],
        )

        cert = x509.load_pem_x509_certificate(cert_pem)
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)

        names = [str(n.value) for n in san.value]
        assert "localhost" in names
        assert "127.0.0.1" in names
        assert "ploston.local" in names

    def test_server_cert_server_auth(self, tmp_path):
        """Test server certificate has server auth extended key usage."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        _, cert_pem = ca.generate_server_cert(hostname="localhost")

        cert = x509.load_pem_x509_certificate(cert_pem)
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
        assert x509.oid.ExtendedKeyUsageOID.SERVER_AUTH in eku.value

    def test_ssl_context_with_ca_cert(self, tmp_path):
        """Test creating SSL context with CA certificate."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        # Create SSL context for client
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(cadata=ca.get_ca_cert_pem().decode())

        # Should not raise
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_ssl_context_with_server_cert(self, tmp_path):
        """Test creating SSL context with server certificate."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        ca.initialize()

        key_pem, cert_pem = ca.generate_server_cert(hostname="localhost")

        # Write to temp files for SSL context
        key_file = tmp_path / "server.key"
        cert_file = tmp_path / "server.crt"
        key_file.write_bytes(key_pem)
        cert_file.write_bytes(cert_pem)

        # Create SSL context for server
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))

        # Should not raise
        assert ctx is not None


class TestCertRenewal:
    """Test certificate renewal mechanism."""

    def test_needs_renewal_not_initialized(self, tmp_path):
        """Test needs_renewal returns True when not initialized."""
        ca = EmbeddedCA(ca_dir=tmp_path)
        assert ca.needs_renewal() is True

    def test_needs_renewal_fresh_cert(self, tmp_path):
        """Test needs_renewal returns False for fresh certificate."""
        ca = EmbeddedCA(ca_dir=tmp_path, ca_validity_days=365)
        ca.initialize()

        assert ca.needs_renewal(days_before_expiry=30) is False

    def test_needs_renewal_expiring_soon(self, tmp_path):
        """Test needs_renewal returns True for expiring certificate."""
        # Create CA with very short validity
        ca = EmbeddedCA(ca_dir=tmp_path, ca_validity_days=10)
        ca.initialize()

        # Should need renewal if checking 30 days before expiry
        assert ca.needs_renewal(days_before_expiry=30) is True
