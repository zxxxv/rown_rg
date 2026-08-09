"""시크릿 대칭 암호화 — app_settings의 비밀 값(API키·네이버웍스 private key 등) 저장용.

마스터 키는 전용 키(settings.secrets_encryption_key)를 쓴다 — JWT 서명 키와 분리해,
JWT 키를 로테이션해도 저장된 시크릿이 안 깨지고 두 신뢰 도메인이 결합되지 않는다.

전용 키가 비어 있으면 jwt_secret_key에서 파생하는 종전 방식으로 폴백한다(하위 호환).
⚠️ 폴백 모드에선 jwt_secret_key를 바꾸면 기존 암호문을 복호화할 수 없다 — 운영은
   `Fernet.generate_key()`로 뽑은 전용 키를 SECRETS_ENCRYPTION_KEY에 넣을 것.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet

from src.core.config import settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    dedicated = settings.secrets_encryption_key
    if dedicated:
        return Fernet(dedicated.encode("ascii"))
    # 폴백: 전용 키 미설정 시 jwt_secret_key에서 파생(하위 호환).
    digest = hashlib.sha256(settings.jwt_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
