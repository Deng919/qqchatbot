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
        signature = hmac.new(self.secret, expires.encode(), hashlib.sha256).hexdigest()
        return f"{expires}.{signature}"

    def verify(self, value: str | None) -> bool:
        if not value or "." not in value:
            return False
        expires, signature = value.split(".", 1)
        expected = hmac.new(self.secret, expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected) and int(expires) >= int(time.time())
