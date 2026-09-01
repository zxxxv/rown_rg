import { Globe, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  type IpWhitelistEntry,
  useCreateIpEntry,
  useDeleteIpEntry,
  useIpWhitelist,
  useUpdateIpEntry,
} from "@/api/ip-whitelist";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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

function fmtDateTime(iso?: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

const IPV4_RE = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
const IPV6_RE =
  /^(([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:(:[0-9a-fA-F]{1,4}){1,6}|:((:[0-9a-fA-F]{1,4}){1,7}|:))$/;

function isIpv4(s: string): boolean {
  const m = IPV4_RE.exec(s);
  return m ? m.slice(1).every((o) => Number(o) <= 255) : false;
}

/** IPv4/IPv6 단일 IP 또는 CIDR 검증. 백엔드 ipaddress.ip_network(strict=False)와 정합. */
function isValidIpOrCidr(value: string): boolean {
  const parts = value.trim().split("/");
  if (parts.length > 2) return false;
  const [addr, prefix] = parts;
  const v4 = isIpv4(addr);
  const v6 = !v4 && IPV6_RE.test(addr);
  if (!v4 && !v6) return false;
  if (prefix !== undefined) {
    if (!/^\d+$/.test(prefix)) return false;
    const p = Number(prefix);
    return v4 ? p >= 0 && p <= 32 : p >= 0 && p <= 128;
  }
  return true;
}

function isExpired(entry: IpWhitelistEntry): boolean {
  return Boolean(entry.expires_at && new Date(entry.expires_at).getTime() <= Date.now());
}

export default function AdminIpPage() {
  const { user, logout } = useAuth();
  const listQuery = useIpWhitelist();

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <div className="flex flex-col gap-6">
        <header>
          <h1 className="flex items-center gap-2 text-3xl font-semibold text-fg">
            <Globe className="h-7 w-7 text-fg-secondary" aria-hidden />
            IP 화이트리스트
          </h1>
          <p className="text-sm text-fg-secondary">
            허용된 IP/CIDR에서만 접속할 수 있습니다. 만료 시각을 지정하면 임시 허용으로 동작합니다.
            (최고관리자 전용)
          </p>
        </header>

        <AddEntryCard />

        {listQuery.isLoading ? (
          <LoadingSkeleton variant="card" count={3} />
        ) : listQuery.isError || !listQuery.data ? (
          <EmptyState
            title="목록을 불러오지 못했습니다"
            description="잠시 후 다시 시도해 주세요."
            action={
              <Button variant="outline" onClick={() => void listQuery.refetch()}>
                다시 시도
              </Button>
            }
          />
        ) : listQuery.data.length === 0 ? (
          <EmptyState
            title="등록된 항목이 없습니다"
            description="위 폼에서 허용할 IP 또는 CIDR을 추가하세요."
          />
        ) : (
          <EntriesTable entries={listQuery.data} />
        )}
      </div>
    </AppShell>
  );
}

function AddEntryCard() {
  const create = useCreateIpEntry();
  const [ipCidr, setIpCidr] = useState("");
  const [description, setDescription] = useState("");
  const [expiresAt, setExpiresAt] = useState(""); // datetime-local 값

  const trimmed = ipCidr.trim();
  const cidrInvalid = trimmed !== "" && !isValidIpOrCidr(trimmed);

  const submit = () => {
    if (!trimmed || cidrInvalid) return;
    create.mutate(
      {
        ip_cidr: trimmed,
        ...(description.trim() ? { description: description.trim() } : {}),
        // datetime-local(로컬 시각) → ISO(UTC) 변환해 전송
        ...(expiresAt ? { expires_at: new Date(expiresAt).toISOString() } : {}),
      },
      {
        onSuccess: (entry) => {
          toast.success(`${entry.ip_cidr} 등록됨`);
          setIpCidr("");
          setDescription("");
          setExpiresAt("");
        },
        onError: (err) =>
          toast.error("등록 실패", { description: errMsg(err, "IP/CIDR 형식을 확인해 주세요.") }),
      },
    );
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">허용 IP 추가</CardTitle>
        <CardDescription>
          단일 IP(예: 1.2.3.4) 또는 CIDR(예: 10.0.0.0/24)을 입력하세요. 만료 시각은 선택입니다.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-end gap-3">
        <div className="flex w-44 flex-col gap-1.5">
          <Label htmlFor="ip-cidr">IP / CIDR</Label>
          <Input
            id="ip-cidr"
            value={ipCidr}
            onChange={(e) => setIpCidr(e.target.value)}
            placeholder="10.0.0.0/24"
            className="font-mono"
            aria-invalid={cidrInvalid ? "true" : undefined}
            aria-describedby={cidrInvalid ? "ip-cidr-error" : undefined}
          />
          {cidrInvalid ? (
            <p id="ip-cidr-error" className="text-xs text-fg-danger">
              잘못된 IP/CIDR 형식입니다 (예: 1.2.3.4, 10.0.0.0/24)
            </p>
          ) : null}
        </div>
        <div className="flex w-52 flex-col gap-1.5">
          <Label htmlFor="ip-description">설명</Label>
          <Input
            id="ip-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="본사 사무실"
            maxLength={255}
          />
        </div>
        <div className="flex w-52 flex-col gap-1.5">
          <Label htmlFor="ip-expires">만료 시각 (선택)</Label>
          <Input
            id="ip-expires"
            type="datetime-local"
            value={expiresAt}
            onChange={(e) => setExpiresAt(e.target.value)}
          />
        </div>
        <Button onClick={submit} disabled={!trimmed || cidrInvalid || create.isPending}>
          <Plus className="mr-1 h-4 w-4" aria-hidden />
          {create.isPending ? "등록 중…" : "추가"}
        </Button>
      </CardContent>
    </Card>
  );
}

function EntriesTable({ entries }: { entries: IpWhitelistEntry[] }) {
  const [deleteTarget, setDeleteTarget] = useState<IpWhitelistEntry | null>(null);

  return (
    <>
      <div className="overflow-x-auto rounded border border-border bg-bg">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>CIDR</TableHead>
              <TableHead>설명</TableHead>
              <TableHead>활성</TableHead>
              <TableHead>만료</TableHead>
              <TableHead>등록자</TableHead>
              <TableHead>등록일</TableHead>
              <TableHead className="text-right">액션</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((entry) => (
              <EntryRow key={entry.id} entry={entry} onDelete={() => setDeleteTarget(entry)} />
            ))}
          </TableBody>
        </Table>
      </div>

      <DeleteConfirmDialog target={deleteTarget} onClose={() => setDeleteTarget(null)} />
    </>
  );
}

function EntryRow({ entry, onDelete }: { entry: IpWhitelistEntry; onDelete: () => void }) {
  const update = useUpdateIpEntry();
  const expired = isExpired(entry);

  const toggleActive = (next: boolean) => {
    update.mutate(
      { entryId: entry.id, is_active: next },
      {
        onSuccess: () =>
          toast.success(next ? `${entry.ip_cidr} 활성화됨` : `${entry.ip_cidr} 비활성화됨`),
        onError: (err) => toast.error("변경 실패", { description: errMsg(err, "처리 중 오류") }),
      },
    );
  };

  return (
    <TableRow>
      <TableCell className="font-mono text-sm text-fg">{entry.ip_cidr}</TableCell>
      <TableCell className="max-w-64 truncate text-sm text-fg-secondary">
        {entry.description ?? "-"}
      </TableCell>
      <TableCell>
        <Switch
          checked={entry.is_active}
          onCheckedChange={toggleActive}
          disabled={update.isPending}
          aria-label={`${entry.ip_cidr} 활성 여부`}
        />
      </TableCell>
      <TableCell>
        {entry.expires_at ? (
          <span className="flex items-center gap-1.5 whitespace-nowrap font-mono text-xs text-fg-tertiary">
            {fmtDateTime(entry.expires_at)}
            {expired ? <Badge variant="destructive">만료됨</Badge> : null}
          </span>
        ) : (
          <span className="text-xs text-fg-tertiary">영구</span>
        )}
      </TableCell>
      <TableCell className="font-mono text-xs text-fg-tertiary">
        {entry.created_by ?? "-"}
      </TableCell>
      <TableCell className="font-mono text-xs text-fg-tertiary">
        {fmtDateTime(entry.created_at)}
      </TableCell>
      <TableCell className="text-right">
        <Button variant="ghost" size="sm" onClick={onDelete} title="삭제">
          <Trash2 className="h-3.5 w-3.5 text-fg-danger" aria-hidden />
          <span className="sr-only">삭제</span>
        </Button>
      </TableCell>
    </TableRow>
  );
}

function DeleteConfirmDialog({
  target,
  onClose,
}: {
  target: IpWhitelistEntry | null;
  onClose: () => void;
}) {
  const del = useDeleteIpEntry();

  const submit = () => {
    if (!target) return;
    del.mutate(target.id, {
      onSuccess: (res) => {
        toast.success(res.detail || "삭제되었습니다");
        onClose();
      },
      onError: (err) => toast.error("삭제 실패", { description: errMsg(err, "처리 중 오류") }),
    });
  };

  return (
    <Dialog open={target !== null} onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>항목 삭제</DialogTitle>
          <DialogDescription>
            <span className="font-mono">{target?.ip_cidr}</span> 항목을 삭제합니다. 일시 차단이
            목적이라면 삭제 대신 비활성화를 권장합니다.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={del.isPending}>
            취소
          </Button>
          <Button variant="destructive" onClick={submit} disabled={del.isPending}>
            {del.isPending ? "삭제 중…" : "삭제"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
