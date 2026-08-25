import sys
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.core.config import settings

_engine_kwargs: dict[str, Any] = {"echo": not settings.is_production}
if "pytest" in sys.modules:
    # 테스트에서는 커넥션 풀을 끈다. pytest-asyncio가 테스트마다 새 이벤트 루프를
    # 만드는데, 풀에 남은 asyncpg 커넥션은 처음 쓴 루프에 묶여 있어 다른 테스트가
    # 재사용하는 순간 죽는다(Windows proactor '_proactor=None' — 순서 의존 플레이크의
    # 실체, 2026-08-20 실측: 단독 통과·배치 실패가 이것). NullPool은 체크아웃마다
    # 새로 열어 루프 간 공유가 원천 차단된다. 운영 경로는 그대로 기본 풀.
    _engine_kwargs["poolclass"] = NullPool
async_engine = create_async_engine(settings.database_url, **_engine_kwargs)
async_session_maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


def open_session() -> AsyncSession:
    """요청 밖 경로(백그라운드 작업)의 세션 opener — 여는 쪽이 커밋한다.

    `from ... import async_session_maker`로 모듈 값을 붙들면 테스트가 세션메이커를
    갈아끼워도 그 참조는 옛 것을 가리켜, 백그라운드 작업이 **테스트 DB가 아니라
    개발 DB를 본다**(2026-08-26 실측: 묶음 재작성 통합 테스트가 프로젝트를 못 찾고
    조용히 빠져나갔다). 여기서 호출 시점에 모듈 전역을 찾으므로 패치가 먹는다.
    """
    return async_session_maker()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """요청 스코프 세션 + 트랜잭션 소유권.

    커밋 책임 단일 규칙(모든 계층 공통):
    세션을 '연' 쪽이 커밋한다. 요청 경로에서는 이 의존성이 유일한 opener이므로
    happy path의 커밋/롤백을 전적으로 소유한다. 라우터·의존성·서비스 콜리는
    주입받은 세션에 대해 절대 commit()/rollback()을 호출하지 않는다.
    (요청 밖 경로 — runner·token_tracker·indexing 서비스 — 는 각자 async_session_maker()로
    세션을 열므로, 그 블록이 곧 opener이자 커밋 주체가 된다.)

    유일한 예외는 '거부 직전 영속화'다 → persist_before_reject 참조.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def persist_before_reject(session: AsyncSession) -> None:
    """요청을 거부(raise)하기 직전에 보안 관련 쓰기를 확정한다.

    기본 규칙은 'opener(get_async_session)만 커밋'이지만, 로그인 실패 횟수 기록이나
    refresh 재사용 탐지에 따른 토큰 폐기처럼 '요청은 거부하되 그 쓰기는 반드시
    남겨야' 하는 경우가 있다. 그냥 raise하면 get_async_session의 except가 롤백하여
    이 쓰기까지 사라지므로, raise 직전에 이 함수로 명시적으로 커밋한다.

    happy path에서는 절대 호출하지 않는다. 이 함수는 요청 경로에서 opener 외에
    허용되는 유일한 커밋 지점이며, 이름으로 그 예외성을 드러낸다(grep 감사 지점).
    """
    await session.commit()
