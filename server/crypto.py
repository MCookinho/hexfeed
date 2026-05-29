"""
hexfeed - criptografia para arquivos de DM
Usa Fernet (AES-128-CBC + HMAC) para encriptar arquivos em repouso.
A chave é gerada automaticamente e salva em data/dm_encryption.key.
"""

from pathlib import Path
from cryptography.fernet import Fernet

KEY_DIR = Path(__file__).resolve().parent.parent / "data"
KEY_FILE = KEY_DIR / "dm_encryption.key"


def _get_key() -> bytes:
    """
    Carrega a chave do disco ou gera uma nova se não existir.
    A chave é persistente entre reinicializações do servidor.
    """
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    if KEY_FILE.exists():
        return KEY_FILE.read_bytes()
    key = Fernet.generate_key()
    KEY_FILE.write_bytes(key)
    return key


# Instância Fernet global (inicializada na importação do módulo)
_fernet = Fernet(_get_key())


def encrypt_file_bytes(data: bytes) -> bytes:
    """Criptografa bytes usando Fernet (AES-128-CBC + HMAC)."""
    return _fernet.encrypt(data)


def decrypt_file_bytes(data: bytes) -> bytes:
    """Descriptografa bytes previamente criptografados com encrypt_file_bytes."""
    return _fernet.decrypt(data)
