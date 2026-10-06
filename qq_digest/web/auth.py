from __future__ import annotations

import hashlib
import hmac
import secrets
import time


def _pbkdf2(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()


class PasswordHasher:
    algorithm = "pbkdf2_sha256"
    iterations = 120_000

    @classmethod
    def hash(cls, password: str, salt: str | None = None) -> str:
        salt = salt or secrets.token_hex(8)
        digest = _pbkdf2(password, salt, cls.iterations)
        return f"{cls.algorithm}${cls.iterations}${salt}${digest}"

    @classmethod
    def verify(cls, password: str, stored: str) -> bool:
        try:
            algorithm, iterations, salt, digest = stored.split("$", 3)
        except ValueError:
            return False
        if algorithm != cls.algorithm:
            return False
        try:
            actual = _pbkdf2(password, salt, int(iterations))
        except ValueError:
            return False
        return hmac.compare_digest(actual, digest)


class SessionCookie:
    def __init__(self, secret: str, ttl_seconds: int):
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def issue(self) -> str:
        expires = str(int(time.time()) + self.ttl_seconds)
        payload = f"{expires}.{secrets.token_hex(16)}"
        signature = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def verify(self, value: str | None) -> bool:
        if not value:
            return False
        parts = value.split(".")
        if len(parts) != 3:
            return False
        expires, nonce, signature = parts
        if (not expires.isascii() or not expires.isdecimal() or len(expires) > 12
                or len(nonce) != 32 or len(signature) != 64 or not signature.isascii()):
            return False
        payload = f"{expires}.{nonce}"
        expected = hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and int(expires) >= int(time.time())
