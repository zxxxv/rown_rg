"""시크릿 대칭 암호화 — app_settings의 비밀 값(API키·네이버웍스 private key 등) 저장용.

마스터 키는 settings.jwt_secret_key에서 파생한다(운영은 32자+ 강제라 안정적).
⚠️ jwt_secret_key를 바꾸면 기존 암호문은 복호화 불가 — 시크릿을 다시 입력해야 한다.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet

from src.core.config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.jwt_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
