"""사용자 인증 모듈.

bcrypt 기반 비밀번호 해싱 및 검증을 제공합니다.
"""
import bcrypt


def hash_password(plain: str) -> str:
    """평문 비밀번호를 bcrypt 해시로 변환합니다."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """평문 비밀번호가 bcrypt 해시와 일치하는지 확인합니다."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False
