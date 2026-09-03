from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.clock import now
from src.core.types import ProjectStage
from src.db.models.project import Project
from src.db.models.quota_setting import QuotaSettings
from src.db.models.token_usage import TokenUsage
from src.db.models.user import User
from tests.conftest import auth_headers as _auth

pytestmark = pytest.mark.integration


# 한도 캐시 격리는 conftest의 autouse(_reset_quota_settings_cache)가 전담한다.


async def _seed_setting(session: AsyncSession, key: str, value: str) -> QuotaSettings:
    row = QuotaSettings(key=key, value=value)
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _add_usage(
    session: AsyncSession,
    user: User,
    cost_usd: Decimal,
    created_at: datetime | None = None,
) -> None:
    usage = TokenUsage(
        user_id=user.id,
        model="claude-opus-4-7",
        operation="test_op",
        input_tokens=100,
        output_tokens=100,
        cost_usd=cost_usd,
        mode="replay",
    )
    if created_at is not None:
        usage.created_at = created_at
    session.add(usage)
    await session.commit()


async def _add_project(
    session: AsyncSession,
    owner: User,
    status: str,
    completed_at: datetime | None = None,
    finalized_at: datetime | None = None,
) -> Project:
    project = Project(
        title="테스트 리포트",
        topic="대시보드 completed_reports 검증",
        owner_id=owner.id,
        status=status,
    )
    session.add(project)
    await session.commit()
    if completed_at is not None or finalized_at is not None:
        # 상태 전이 시점의 자동 기록(now) 대신 테스트가 원하는 경계값으로 덮어쓴다.
        # status는 건드리지 않으므로 completed_at 동기화 훅은 재실행되지 않는다.
        if completed_at is not None:
            project.completed_at = completed_at
        if finalized_at is not None:
            project.finalized_at = finalized_at
        await session.commit()
    await session.refresh(project)
    return project


def _this_month_start(today: datetime) -> datetime:
    return datetime(today.year, today.month, 1, tzinfo=today.tzinfo)


def _last_month_start(today: datetime) -> datetime:
    if today.month == 1:
        return datetime(today.year - 1, 12, 1, tzinfo=today.tzinfo)
    return datetime(today.year, today.month - 1, 1, tzinfo=today.tzinfo)


class TestAdminDashboardPeriod:
    async def test_default_period_is_this_month(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        # 기본 기간은 이번 달(2026-08-20 변경) — 월 한도와 같은 창이라 월이 바뀌면
        # 0부터 다시 누적된다. 지난달 사용량은 제외된다.
        today = now()
        await _add_usage(test_session, admin_user, Decimal("50"), created_at=today)
        await _add_usage(
            test_session,
            admin_user,
            Decimal("999"),
            created_at=_this_month_start(today) - timedelta(seconds=1),
        )

        response = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))

        assert response.status_code == 200
        body = response.json()
        assert body["period"]["type"] == "this_month"
        assert body["kpis"]["total_cost_usd"] == 50.0
        # 월초~오늘, 하루 한 버킷
        assert len(body["daily_costs"]) == today.day

    async def test_this_month_still_available_via_param(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        await _add_usage(test_session, admin_user, Decimal("50"), created_at=today)
        await _add_usage(
            test_session,
            admin_user,
            Decimal("999"),
            created_at=_this_month_start(today) - timedelta(seconds=1),
        )

        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "this_month"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["period"]["type"] == "this_month"
        assert body["kpis"]["total_cost_usd"] == 50.0

    async def test_custom_range_uses_start_end_inclusive(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        tz = today.tzinfo
        start = (today - timedelta(days=5)).date()
        end = (today - timedelta(days=3)).date()
        # 구간 안(end 당일 포함) vs 구간 밖(end 다음날)
        in_range = datetime(end.year, end.month, end.day, 12, 0, tzinfo=tz)
        out_range = datetime(end.year, end.month, end.day, tzinfo=tz) + timedelta(days=1)
        await _add_usage(test_session, admin_user, Decimal("40"), created_at=in_range)
        await _add_usage(test_session, admin_user, Decimal("999"), created_at=out_range)

        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "custom", "start": str(start), "end": str(end)},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["period"]["type"] == "custom"
        assert body["kpis"]["total_cost_usd"] == 40.0
        assert body["period"]["label"] == f"{start} ~ {end}"
        # start~end(포함) = 3일
        assert len(body["daily_costs"]) == 3

    async def test_custom_range_missing_bounds_returns_422(
        self, test_client: AsyncClient, admin_token: str
    ) -> None:
        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "custom"},
            headers=_auth(admin_token),
        )
        assert response.status_code == 422

    async def test_user_daily_series_top_n_plus_other(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        await _add_usage(test_session, admin_user, Decimal("25"), created_at=today)

        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "last_30_days"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        body = response.json()
        assert "user_daily" in body
        keys = {u["key"] for u in body["user_daily"]["users"]}
        assert str(admin_user.id) in keys
        # 일별 포인트는 30일치, admin 기여가 오늘 날짜에 잡힌다
        assert len(body["user_daily"]["points"]) == 30
        assert any(
            p["costs"].get(str(admin_user.id), 0) == 25.0 for p in body["user_daily"]["points"]
        )

    async def test_last_month_excludes_this_month_includes_boundary(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        this_month_start = _this_month_start(today)
        last_month_start = _last_month_start(today)

        await _add_usage(test_session, admin_user, Decimal("999"), created_at=today)
        await _add_usage(test_session, admin_user, Decimal("30"), created_at=last_month_start)
        await _add_usage(
            test_session,
            admin_user,
            Decimal("999"),
            created_at=last_month_start - timedelta(seconds=1),
        )

        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "last_month"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["period"]["type"] == "last_month"
        assert body["kpis"]["total_cost_usd"] == 30.0
        last_day = this_month_start.date() - timedelta(days=1)
        assert body["period"]["label"] == f"{last_month_start.date()} ~ {last_day}"

    async def test_last_7_days_boundary(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        midnight = datetime(today.year, today.month, today.day, tzinfo=today.tzinfo)
        window_start = midnight - timedelta(days=6)

        await _add_usage(test_session, admin_user, Decimal("20"), created_at=window_start)
        await _add_usage(
            test_session,
            admin_user,
            Decimal("999"),
            created_at=window_start - timedelta(seconds=1),
        )

        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "last_7_days"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        body = response.json()
        assert body["kpis"]["total_cost_usd"] == 20.0
        assert len(body["daily_costs"]) == 7

    async def test_last_30_days_daily_costs_length(
        self,
        test_client: AsyncClient,
        admin_token: str,
    ) -> None:
        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "last_30_days"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        assert len(response.json()["daily_costs"]) == 30

    async def test_invalid_period_returns_422(
        self,
        test_client: AsyncClient,
        admin_token: str,
    ) -> None:
        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "foo"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 422


class TestAdminDashboardCompletedReports:
    async def test_completed_reports_counts_finalized_not_assembled(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        """'완료'는 **최종 확정**이다 - 조립만 끝난 건 아직 손보는 중이다(2026-08-27).

        completed_at은 조립이 끝난 시각이라, 그걸로 세면 검토 중인 보고서까지 완료로
        올라간다. 목록 필터('완료 = finalized_at IS NOT NULL')와 어긋나서 같은 프로젝트가
        두 화면에서 다르게 세어지던 것을 맞춘다.
        """
        today = now()
        this_month_start = _this_month_start(today)

        # 이번 달에 확정 -> 집계에 포함
        in_range = await _add_project(
            test_session,
            admin_user,
            ProjectStage.COMPLETED.value,
            completed_at=today,
            finalized_at=today,
        )
        # 지난달 확정 -> 이번 달 집계에서 제외
        await _add_project(
            test_session,
            admin_user,
            ProjectStage.COMPLETED.value,
            completed_at=this_month_start - timedelta(seconds=1),
            finalized_at=this_month_start - timedelta(seconds=1),
        )
        # 조립은 이번 달에 끝났지만 **확정 전** -> 완료가 아니다
        assembled_only = await _add_project(
            test_session, admin_user, ProjectStage.COMPLETED.value, completed_at=today
        )
        # 작성 중 -> 완료가 아니다
        await _add_project(test_session, admin_user, ProjectStage.WRITING.value)
        assert in_range.finalized_at is not None
        assert assembled_only.finalized_at is None

        # 경계값이 '이번 달' 기준이므로 명시적으로 this_month로 조회한다(기본은 최근 30일).
        response = await test_client.get(
            "/api/v1/admin/dashboard",
            params={"period": "this_month"},
            headers=_auth(admin_token),
        )

        assert response.status_code == 200
        assert response.json()["kpis"]["completed_reports"] == 1

    async def test_unfinalized_report_counts_as_active(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        """조립까지 끝났어도 확정 전이면 '진행 중'이다 - 아직 사람이 손보는 중이다.

        종전에는 status가 completed로 넘어가는 순간 진행 중에서도 완료에서도 빠져
        어느 칸에도 안 잡혔다.
        """
        await _add_project(
            test_session, admin_user, ProjectStage.COMPLETED.value, completed_at=now()
        )
        await _add_project(
            test_session,
            admin_user,
            ProjectStage.COMPLETED.value,
            completed_at=now(),
            finalized_at=now(),
        )
        await _add_project(test_session, admin_user, ProjectStage.WRITING.value)

        response = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))

        assert response.status_code == 200
        kpis = response.json()["kpis"]
        assert kpis["active_projects"] == 2, "미확정 보고서가 진행 중에서 빠졌다"

    async def test_reverting_completed_status_removes_it_from_count(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        project = await _add_project(
            test_session, admin_user, ProjectStage.COMPLETED.value, completed_at=today
        )

        project.status = ProjectStage.WRITING.value
        await test_session.commit()
        await test_session.refresh(project)
        assert project.completed_at is None

        response = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))

        assert response.status_code == 200
        assert response.json()["kpis"]["completed_reports"] == 0


class TestAdminDashboardQuotaSettingsOverride:
    async def test_cost_limit_usd_reflects_db_override(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "4321")

        response = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))

        assert response.status_code == 200
        assert response.json()["kpis"]["cost_limit_usd"] == 4321.0

    async def test_user_usage_limit_reflects_db_override_for_role(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_token: str,
        worker_user: User,
    ) -> None:
        await _seed_setting(test_session, "DEFAULT_LIMIT_WORKER_USD", "777")

        response = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))

        assert response.status_code == 200
        rows = {row["user_id"]: row for row in response.json()["user_usage"]}
        assert rows[str(worker_user.id)]["limit_usd"] == 777.0

    async def test_dashboard_patch_then_reread_reflects_new_value_without_restart(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_token: str,
        super_admin_token: str,
    ) -> None:
        await _seed_setting(test_session, "ORG_MONTHLY_COST_LIMIT_USD", "3000")

        first = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))
        assert first.status_code == 200
        assert first.json()["kpis"]["cost_limit_usd"] == 3000.0

        patch_response = await test_client.patch(
            "/api/v1/admin/quota-settings",
            json={"ORG_MONTHLY_COST_LIMIT_USD": "6000"},
            headers=_auth(super_admin_token),
        )
        assert patch_response.status_code == 200

        second = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))
        assert second.status_code == 200
        assert second.json()["kpis"]["cost_limit_usd"] == 6000.0


class TestUserUsageDetail:
    async def test_detail_reports_counts_and_project_cost(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        today = now()
        completed = await _add_project(
            test_session, admin_user, ProjectStage.COMPLETED.value, completed_at=today
        )
        await _add_project(test_session, admin_user, ProjectStage.WRITING.value)
        usage = TokenUsage(
            user_id=admin_user.id,
            project_id=completed.id,
            model="claude-opus-4-7",
            operation="test_op",
            input_tokens=10,
            output_tokens=10,
            cost_usd=Decimal("15"),
            mode="replay",
        )
        test_session.add(usage)
        await test_session.commit()

        response = await test_client.get(
            f"/api/v1/admin/users/{admin_user.id}/usage", headers=_auth(admin_token)
        )

        assert response.status_code == 200
        body = response.json()
        assert body["reports_total"] == 2
        assert body["reports_completed"] == 1
        assert body["reports_in_progress"] == 1
        assert body["total_cost_usd"] == 15.0
        proj = next(p for p in body["projects"] if p["id"] == str(completed.id))
        assert proj["cost_usd"] == 15.0
        assert len(body["daily_costs"]) == 30

    async def test_detail_404_for_unknown_user(
        self, test_client: AsyncClient, admin_token: str
    ) -> None:
        from uuid import uuid4

        response = await test_client.get(
            f"/api/v1/admin/users/{uuid4()}/usage", headers=_auth(admin_token)
        )
        assert response.status_code == 404


class TestOnlineUsersKPI:
    async def test_counts_recent_heartbeats_excluding_stale_and_inactive(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
        worker_user: User,
        viewer_user: User,
        super_admin_user: User,
    ) -> None:
        # 접속중 = last_seen_at이 5분 이내 & is_active. 요청자(admin)는 이 요청의
        # 하트비트로 접속중에 포함된다.
        current = now()
        worker_user.last_seen_at = current - timedelta(minutes=2)  # 포함
        super_admin_user.last_seen_at = current - timedelta(minutes=30)  # 창 밖 → 제외
        viewer_user.last_seen_at = current - timedelta(minutes=1)  # 최근이지만 비활성 → 제외
        viewer_user.is_active = False
        await test_session.commit()

        response = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))

        assert response.status_code == 200
        kpis = response.json()["kpis"]
        assert kpis["online_users"] == 2  # admin(요청자) + worker
        # 활성 사용자(기간 내 로그인)와는 별개 지표다 — 로그인한 사람은 admin뿐.
        assert kpis["active_users"] == 1


class TestLastSeenHeartbeat:
    async def test_authenticated_request_stamps_and_throttles_last_seen(
        self,
        test_client: AsyncClient,
        test_session: AsyncSession,
        admin_user: User,
        admin_token: str,
    ) -> None:
        assert admin_user.last_seen_at is None

        first_resp = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))
        assert first_resp.status_code == 200
        await test_session.refresh(admin_user)
        first_seen = admin_user.last_seen_at
        assert first_seen is not None

        # 60초 스로틀 — 직후의 두 번째 요청은 last_seen_at을 갱신하지 않는다.
        second_resp = await test_client.get("/api/v1/admin/dashboard", headers=_auth(admin_token))
        assert second_resp.status_code == 200
        await test_session.refresh(admin_user)
        assert admin_user.last_seen_at == first_seen
