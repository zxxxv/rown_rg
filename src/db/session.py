from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.core.config import settings

async_engine = create_async_engine(settings.database_url, echo=not settings.is_production)
async_session_maker = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


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
