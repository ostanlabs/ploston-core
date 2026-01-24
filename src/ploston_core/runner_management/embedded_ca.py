"""Embedded CA for TLS Certificate Management.

Implements S-183: Embedded CA & TLS
- T-524: CA generation on CP first start
- T-525: Runner cert provisioning
- T-526: CA cert download endpoint
- T-527: TLS handshake for WebSocket
- T-528: Cert rotation mechanism
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Optional cryptography import
try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False
    x509 = None  # type: ignore
    hashes = None  # type: ignore
    serialization = None  # type: ignore
    rsa = None  # type: ignore
    NameOID = None  # type: ignore


@dataclass
class CertificateInfo:
    """Certificate information."""
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    serial_number: int
    is_ca: bool


class EmbeddedCA:
    """Embedded Certificate Authority for runner TLS.
    
    Generates and manages:
    - CA keypair and self-signed certificate
    - Runner certificates signed by the CA
    """
    
    def __init__(
        self,
        ca_dir: str | Path | None = None,
        ca_validity_days: int = 3650,  # 10 years
        cert_validity_days: int = 365,  # 1 year
    ) -> None:
        """Initialize the CA.
        
        Args:
            ca_dir: Directory to store CA files (default: ~/.ploston/ca/)
            ca_validity_days: CA certificate validity in days
            cert_validity_days: Runner certificate validity in days
        """
        if not HAS_CRYPTOGRAPHY:
            raise ImportError(
                "cryptography package required for TLS: pip install cryptography"
            )
        
        self._ca_dir = Path(ca_dir) if ca_dir else Path.home() / ".ploston" / "ca"
        self._ca_validity_days = ca_validity_days
        self._cert_validity_days = cert_validity_days
        
        self._ca_key: Any = None
        self._ca_cert: Any = None
    
    @property
    def ca_key_path(self) -> Path:
        """Path to CA private key."""
        return self._ca_dir / "ca.key"
    
    @property
    def ca_cert_path(self) -> Path:
        """Path to CA certificate."""
        return self._ca_dir / "ca.crt"
    
    def initialize(self) -> None:
        """Initialize the CA, generating keys if needed."""
        self._ca_dir.mkdir(parents=True, exist_ok=True)
        
        if self.ca_key_path.exists() and self.ca_cert_path.exists():
            self._load_ca()
        else:
            self._generate_ca()
    
    def _generate_ca(self) -> None:
        """Generate CA keypair and self-signed certificate."""
        logger.info("Generating new CA keypair...")
        
        # Generate private key
        self._ca_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096,
        )
        
        # Generate self-signed certificate
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ploston"),
            x509.NameAttribute(NameOID.COMMON_NAME, "Ploston Runner CA"),
        ])
        
        now = datetime.now(timezone.utc)
        self._ca_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(self._ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=self._ca_validity_days))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=0),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_cert_sign=True,
                    crl_sign=True,
                    key_encipherment=False,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(self._ca_key, hashes.SHA256())
        )
        
        # Save to files
        self._save_ca()
        logger.info(f"CA generated and saved to {self._ca_dir}")
    
    def _save_ca(self) -> None:
        """Save CA key and certificate to files."""
        # Save private key (restricted permissions)
        key_pem = self._ca_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        self.ca_key_path.write_bytes(key_pem)
        os.chmod(self.ca_key_path, 0o600)
        
        # Save certificate
        cert_pem = self._ca_cert.public_bytes(serialization.Encoding.PEM)
        self.ca_cert_path.write_bytes(cert_pem)
    
    def _load_ca(self) -> None:
        """Load CA key and certificate from files."""
        logger.info(f"Loading CA from {self._ca_dir}")
        
        key_pem = self.ca_key_path.read_bytes()
        self._ca_key = serialization.load_pem_private_key(key_pem, password=None)
        
        cert_pem = self.ca_cert_path.read_bytes()
        self._ca_cert = x509.load_pem_x509_certificate(cert_pem)
    
    def get_ca_cert_pem(self) -> bytes:
        """Get CA certificate in PEM format.
        
        This is what runners download to validate CP connections.
        """
        if not self._ca_cert:
            raise RuntimeError("CA not initialized")
        return self._ca_cert.public_bytes(serialization.Encoding.PEM)
    
    def get_ca_cert_info(self) -> CertificateInfo:
        """Get CA certificate information."""
        if not self._ca_cert:
            raise RuntimeError("CA not initialized")
        
        return CertificateInfo(
            subject=self._ca_cert.subject.rfc4514_string(),
            issuer=self._ca_cert.issuer.rfc4514_string(),
            not_before=self._ca_cert.not_valid_before_utc,
            not_after=self._ca_cert.not_valid_after_utc,
            serial_number=self._ca_cert.serial_number,
            is_ca=True,
        )
    
    def generate_runner_cert(
        self,
        runner_name: str,
        runner_id: str,
    ) -> tuple[bytes, bytes]:
        """Generate a certificate for a runner.
        
        Args:
            runner_name: Human-readable runner name
            runner_id: Runner ID
            
        Returns:
            Tuple of (private_key_pem, certificate_pem)
        """
        if not self._ca_key or not self._ca_cert:
            raise RuntimeError("CA not initialized")
        
        # Generate runner private key
        runner_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        
        # Generate certificate
        subject = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ploston"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"runner-{runner_name}"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, runner_id),
        ])
        
        now = datetime.now(timezone.utc)
        runner_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self._ca_cert.subject)
            .public_key(runner_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=self._cert_validity_days))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([
                    x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
                ]),
                critical=False,
            )
            .sign(self._ca_key, hashes.SHA256())
        )
        
        # Serialize
        key_pem = runner_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cert_pem = runner_cert.public_bytes(serialization.Encoding.PEM)
        
        return key_pem, cert_pem
    
    def generate_server_cert(
        self,
        hostname: str,
        alt_names: list[str] | None = None,
    ) -> tuple[bytes, bytes]:
        """Generate a server certificate for the CP.
        
        Args:
            hostname: Primary hostname
            alt_names: Additional hostnames/IPs
            
        Returns:
            Tuple of (private_key_pem, certificate_pem)
        """
        if not self._ca_key or not self._ca_cert:
            raise RuntimeError("CA not initialized")
        
        # Generate server private key
        server_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        
        # Build SAN list
        san_list = [x509.DNSName(hostname)]
        for name in (alt_names or []):
            if name.replace(".", "").isdigit():
                # IP address
                import ipaddress
                san_list.append(x509.IPAddress(ipaddress.ip_address(name)))
            else:
                san_list.append(x509.DNSName(name))
        
        # Generate certificate
        subject = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Ploston"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ])
        
        now = datetime.now(timezone.utc)
        server_cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self._ca_cert.subject)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=self._cert_validity_days))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.SubjectAlternativeName(san_list),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    key_encipherment=True,
                    key_cert_sign=False,
                    crl_sign=False,
                    content_commitment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([
                    x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                ]),
                critical=False,
            )
            .sign(self._ca_key, hashes.SHA256())
        )
        
        # Serialize
        key_pem = server_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cert_pem = server_cert.public_bytes(serialization.Encoding.PEM)
        
        return key_pem, cert_pem
    
    def needs_renewal(self, days_before_expiry: int = 30) -> bool:
        """Check if CA certificate needs renewal.
        
        Args:
            days_before_expiry: Days before expiry to trigger renewal
            
        Returns:
            True if renewal is needed
        """
        if not self._ca_cert:
            return True
        
        expiry = self._ca_cert.not_valid_after_utc
        threshold = datetime.now(timezone.utc) + timedelta(days=days_before_expiry)
        return expiry < threshold
