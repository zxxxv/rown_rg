import { ChevronLeft, ChevronRight, KeyRound, Lock, LockOpen, Wallet } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { UserRoleType } from "@/api/types";
import {
  type AdminUser,
  useResetUserPassword,
  useSetUserQuota,
  useUnlockUser,
  useUpdateUser,
  useUsersList,
} from "@/api/users";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuth } from "@/hooks/useAuth";

const PAGE_SIZE = 20;

const ROLE_RANK: Record<UserRoleType, number> = {
  viewer: 1,
  worker: 2,
  admin: 3,
  super_admin: 4,
};

const ROLE_LABEL: Record<UserRoleType, string> = {
  super_admin: "최고관리자",
  admin: "관리자",
  worker: "작성자",
  viewer: "뷰어",
};

const ROLE_ORDER: UserRoleType[] = ["super_admin", "admin", "worker", "viewer"];

const PASSWORD_POLICY_HINT =
  "12자 이상, 대문자·소문자·숫자·특수문자를 각각 1자 이상 포함해야 합니다.";

function fmtDateTime(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function isLocked(user: AdminUser): boolean {
  return Boolean(user.locked_until && new Date(user.locked_until).getTime() > Date.now());
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function AdminUsersPage() {
  const { user, logout } = useAuth();
  const [offset, setOffset] = useState(0);
  const usersQuery = useUsersList({ limit: PAGE_SIZE, offset });

  const myRole: UserRoleType = user?.role ?? "viewer";

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <div className="flex flex-col gap-6">
        <header>
          <h1 className="text-3xl font-semibold text-fg">사용자 관리</h1>
          <p className="text-sm text-fg-secondary">
            역할 변경·활성 토글·잠금 해제·비밀번호 리셋·월 한도 조정을 처리합니다.
          </p>
        </header>

        {usersQuery.isLoading ? (
          <LoadingSkeleton variant="card" count={4} />
        ) : usersQuery.isError || !usersQuery.data ? (
          <EmptyState
            title="사용자 목록을 불러오지 못했습니다"
            description="잠시 후 다시 시도해 주세요."
            action={
              <Button variant="outline" onClick={() => void usersQuery.refetch()}>
                다시 시도
              </Button>
            }
          />
        ) : (
          <>
            <UsersTable users={usersQuery.data} myRole={myRole} myId={user?.id ?? ""} />
            <footer className="flex items-center justify-between">
              <span className="font-mono text-xs text-fg-tertiary">
                {offset + 1}–{offset + usersQuery.data.length}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={offset === 0 || usersQuery.isFetching}
                  onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
                >
                  <ChevronLeft className="mr-1 h-4 w-4" />
                  이전
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={usersQuery.data.length < PAGE_SIZE || usersQuery.isFetching}
                  onClick={() => setOffset(offset + PAGE_SIZE)}
                >
                  다음
                  <ChevronRight className="ml-1 h-4 w-4" />
                </Button>
              </div>
            </footer>
          </>
        )}
      </div>
    </AppShell>
  );
}

function UsersTable({
  users,
  myRole,
  myId,
}: {
  users: AdminUser[];
  myRole: UserRoleType;
  myId: string;
}) {
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
  const [quotaTarget, setQuotaTarget] = useState<AdminUser | null>(null);

  return (
    <>
      <div className="overflow-x-auto rounded border border-border bg-bg">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>사용자</TableHead>
              <TableHead>역할</TableHead>
              <TableHead>활성</TableHead>
              <TableHead>마지막 로그인</TableHead>
              <TableHead>잠금 상태</TableHead>
              <TableHead className="text-right">액션</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.map((row) => (
              <UserRow
                key={row.id}
                row={row}
                myRole={myRole}
                isSelf={row.id === myId}
                onResetPassword={() => setResetTarget(row)}
                onAdjustQuota={() => setQuotaTarget(row)}
              />
            ))}
          </TableBody>
        </Table>
      </div>

      <ResetPasswordDialog target={resetTarget} onClose={() => setResetTarget(null)} />
      <QuotaDialog target={quotaTarget} onClose={() => setQuotaTarget(null)} />
    </>
  );
}

function UserRow({
  row,
  myRole,
  isSelf,
  onResetPassword,
  onAdjustQuota,
}: {
  row: AdminUser;
  myRole: UserRoleType;
  isSelf: boolean;
  onResetPassword: () => void;
  onAdjustQuota: () => void;
}) {
  const updateUser = useUpdateUser();
  const unlock = useUnlockUser();

  // 자기보다 상위 역할 사용자는 관리 불가 (백엔드 assert_can_manage_user와 동일 규칙)
  const canManage = ROLE_RANK[row.role] <= ROLE_RANK[myRole];
  // 역할 부여는 자기 이하 역할만
  const assignableRoles = ROLE_ORDER.filter((r) => ROLE_RANK[r] <= ROLE_RANK[myRole]);
  const locked = isLocked(row);

  const changeRole = (role: UserRoleType) => {
    if (role === row.role) return;
    updateUser.mutate(
      { userId: row.id, role },
      {
        onSuccess: () =>
          toast.success(`${row.name}의 역할을 ${ROLE_LABEL[role]}(으)로 변경했습니다.`),
        onError: (err) =>
          toast.error("역할 변경 실패", { description: errMsg(err, "처리 중 오류") }),
      },
    );
  };

  const toggleActive = (next: boolean) => {
    updateUser.mutate(
      { userId: row.id, is_active: next },
      {
        onSuccess: () =>
          toast.success(
            next ? `${row.name} 계정을 활성화했습니다.` : `${row.name} 계정을 비활성화했습니다.`,
          ),
        onError: (err) =>
          toast.error("활성 상태 변경 실패", { description: errMsg(err, "처리 중 오류") }),
      },
    );
  };

  const doUnlock = () => {
    unlock.mutate(row.id, {
      onSuccess: (res) => toast.success(res.detail),
      onError: (err) => toast.error("잠금 해제 실패", { description: errMsg(err, "처리 중 오류") }),
    });
  };

  return (
    <TableRow>
      <TableCell>
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-fg">
            {row.name}
            {isSelf ? <span className="ml-1 text-xs text-fg-tertiary">(나)</span> : null}
          </span>
          <span className="font-mono text-[11px] text-fg-tertiary">{row.email}</span>
        </div>
      </TableCell>
      <TableCell>
        <Select
          value={row.role}
          onValueChange={(v) => changeRole(v as UserRoleType)}
          disabled={!canManage || updateUser.isPending}
        >
          <SelectTrigger className="h-8 w-32">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {ROLE_ORDER.map((r) => (
              <SelectItem key={r} value={r} disabled={!assignableRoles.includes(r)}>
                {ROLE_LABEL[r]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </TableCell>
      <TableCell>
        <Switch
          checked={row.is_active}
          onCheckedChange={toggleActive}
          disabled={!canManage || updateUser.isPending}
          aria-label={`${row.name} 활성 여부`}
        />
      </TableCell>
      <TableCell className="font-mono text-xs text-fg-tertiary">
        {fmtDateTime(row.last_login_at)}
      </TableCell>
      <TableCell>
        {locked ? (
          <Badge variant="destructive" className="gap-1">
            <Lock className="h-3 w-3" aria-hidden />
            잠김 · {fmtDateTime(row.locked_until)}까지
          </Badge>
        ) : (
          <span className="text-xs text-fg-tertiary">정상</span>
        )}
      </TableCell>
      <TableCell className="text-right">
        <div className="flex items-center justify-end gap-1">
          <Button
            variant="ghost"
            size="sm"
            disabled={!canManage || !locked || unlock.isPending}
            onClick={doUnlock}
            title="잠금 해제"
          >
            <LockOpen className="mr-1 h-3.5 w-3.5" aria-hidden />
            잠금 해제
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={!canManage}
            onClick={onResetPassword}
            title="비밀번호 리셋"
          >
            <KeyRound className="mr-1 h-3.5 w-3.5" aria-hidden />
            비번 리셋
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={!canManage}
            onClick={onAdjustQuota}
            title="월 한도 조정"
          >
            <Wallet className="mr-1 h-3.5 w-3.5" aria-hidden />
            한도 조정
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

function ResetPasswordDialog({
  target,
  onClose,
}: {
  target: AdminUser | null;
  onClose: () => void;
}) {
  const reset = useResetUserPassword();
  const [password, setPassword] = useState("");

  const close = () => {
    setPassword("");
    onClose();
  };

  const submit = () => {
    if (!target) return;
    reset.mutate(
      { userId: target.id, new_password: password },
      {
        onSuccess: (res) => {
          toast.success(res.detail);
          close();
        },
        onError: (err) =>
          toast.error("비밀번호 재설정 실패", {
            description: errMsg(err, "정책을 확인해 주세요."),
          }),
      },
    );
  };

  return (
    <Dialog open={target !== null} onOpenChange={(open) => (!open ? close() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>비밀번호 리셋 — {target?.name}</DialogTitle>
          <DialogDescription>
            새 비밀번호를 설정하면 기존 세션이 전부 종료되고 잠금도 함께 해제됩니다.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="admin-reset-password">새 비밀번호</Label>
          <Input
            id="admin-reset-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p className="text-xs text-fg-tertiary">{PASSWORD_POLICY_HINT}</p>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={close} disabled={reset.isPending}>
            취소
          </Button>
          <Button onClick={submit} disabled={password.length === 0 || reset.isPending}>
            {reset.isPending ? "처리 중…" : "재설정"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function QuotaDialog({ target, onClose }: { target: AdminUser | null; onClose: () => void }) {
  const setQuota = useSetUserQuota();
  const [amount, setAmount] = useState("");

  const close = () => {
    setAmount("");
    onClose();
  };

  const parsed = Number(amount);
  const valid = amount.trim() !== "" && Number.isFinite(parsed) && parsed >= 0;

  const submit = () => {
    if (!target || !valid) return;
    setQuota.mutate(
      { userId: target.id, monthly_limit_usd: parsed },
      {
        onSuccess: (res) => {
          toast.success(`${target.name}의 월 한도를 $${res.monthly_limit_usd}로 설정했습니다.`);
          close();
        },
        onError: (err) =>
          toast.error("한도 조정 실패", { description: errMsg(err, "처리 중 오류") }),
      },
    );
  };

  return (
    <Dialog open={target !== null} onOpenChange={(open) => (!open ? close() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>월 한도 조정 — {target?.name}</DialogTitle>
          <DialogDescription>이번 달부터 적용되는 개인 월 사용 한도(USD)입니다.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="admin-quota-amount">월 한도 (USD)</Label>
          <Input
            id="admin-quota-amount"
            type="number"
            min={0}
            step={10}
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder="예: 200"
          />
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={close} disabled={setQuota.isPending}>
            취소
          </Button>
          <Button onClick={submit} disabled={!valid || setQuota.isPending}>
            {setQuota.isPending ? "처리 중…" : "저장"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
