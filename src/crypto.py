"""Field-level encryption for resumes, letters and profile data (Fernet, with key rotation)."""
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from config import settings

PREFIX = "enc1:"


class CryptoError(RuntimeError):
    pass


def _fernet() -> MultiFernet | None:
    keys = [k.strip() for k in settings.ENCRYPTION_KEY.split(",") if k.strip()]
    if not keys:
        return None
    try:
        return MultiFernet([Fernet(k.encode()) for k in keys])
    except (ValueError, TypeError) as e:
        raise CryptoError("ENCRYPTION_KEY is not a valid Fernet key. Generate one with: "
                          "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"") from e


def require_key_in_production() -> None:
    if settings.APP_ENV == "production" and not settings.ENCRYPTION_KEY.strip():
        raise CryptoError("ENCRYPTION_KEY must be set in production so resumes are encrypted at rest.")
    _fernet()  # validate the format early


def encrypt(text: str) -> str:
    if not text:
        return text or ""
    f = _fernet()
    return PREFIX + f.encrypt(text.encode()).decode() if f else text


def decrypt(text: str) -> str:
    if not text or not text.startswith(PREFIX):
        return text or ""          # plaintext written before a key was configured
    f = _fernet()
    if not f:
        raise CryptoError("Stored data is encrypted but ENCRYPTION_KEY is not set.")
    try:
        return f.decrypt(text[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise CryptoError("Couldn't decrypt stored data. Is ENCRYPTION_KEY the one it was written with?") from e
