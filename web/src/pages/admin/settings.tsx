import { ChevronDown, ChevronRight, KeyRound, Save, Settings2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { type SettingItem, useSettings, useUpdateSetting } from "@/api/settings";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { QuotaSettingsCard } from "@/features/admin/QuotaSettingsCard";
import { useAuth } from "@/hooks/useAuth";

// 알림용 자격증명 그룹 — 로그인(SSO)엔 불필요라 기본 접어둔다.
const COLLAPSED_BY_DEFAULT = new Set(["네이버웍스"]);
const GROUP_HINT: Record<string, string> = {
  네이버웍스: "알림 봇 전용 — 로그인(SSO)엔 불필요합니다. 알림을 쓸 때만 채우세요.",
};

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

const GROUP_ORDER = ["LLM", "네이버웍스", "운영"];

export default function AdminSettingsPage() {
  const { user, logout } = useAuth();
  const query = useSettings();

  const grouped: Record<string, SettingItem[]> = {};
  for (const item of query.data ?? []) {
    if (!grouped[item.group]) grouped[item.group] = [];
    grouped[item.group].push(item);
  }
  const groups = Object.keys(grouped).sort(
    (a, b) => GROUP_ORDER.indexOf(a) - GROUP_ORDER.indexOf(b),
  );

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <header>
          <h1 className="flex items-center gap-2 text-3xl font-semibold text-fg">
            <Settings2 className="h-7 w-7 text-fg-secondary" aria-hidden />
            시스템 설정
          </h1>
          <p className="text-sm text-fg-secondary">
            API 키·네이버웍스 자격증명·운영 설정을 입력합니다. .env보다 우선 적용되며, 시크릿은
            암호화 저장되어 다시 조회할 수 없습니다. (최고관리자 전용)
          </p>
        </header>

        {query.isLoading ? (
          <LoadingSkeleton variant="card" count={3} />
        ) : query.isError || !query.data ? (
          <EmptyState
            title="설정을 불러오지 못했습니다"
            description="최고관리자 권한이 필요합니다."
            action={
              <Button variant="outline" onClick={() => void query.refetch()}>
                다시 시도
              </Button>
            }
          />
        ) : (
          groups.map((group) => <GroupCard key={group} group={group} items={grouped[group]} />)
        )}

        <QuotaSettingsCard />
      </div>
    </AppShell>
  );
}

function GroupCard({ group, items }: { group: string; items: SettingItem[] }) {
  const [collapsed, setCollapsed] = useState(COLLAPSED_BY_DEFAULT.has(group));
  const hint = GROUP_HINT[group];
  return (
    <Card>
      <CardHeader className="pb-2">
        <button
          type="button"
          onClick={() => setCollapsed((c) => !c)}
          className="flex w-full items-center gap-2 text-left"
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4 text-fg-tertiary" aria-hidden />
          ) : (
            <ChevronDown className="h-4 w-4 text-fg-tertiary" aria-hidden />
          )}
          <CardTitle className="text-base">{group}</CardTitle>
          <span className="ml-1 text-xs text-fg-tertiary">({items.length})</span>
        </button>
        {hint ? <p className="mt-1 pl-6 text-xs text-fg-tertiary">{hint}</p> : null}
      </CardHeader>
      {collapsed ? null : (
        <CardContent className="flex flex-col gap-4">
          {items.map((item) => (
            <SettingRow key={item.key} item={item} />
          ))}
        </CardContent>
      )}
    </Card>
  );
}

function SettingRow({ item }: { item: SettingItem }) {
  const update = useUpdateSetting();
  // 비밀 아닌 값은 현재 값을 프리필, 시크릿은 항상 빈칸에서 시작(되읽기 불가).
  const [value, setValue] = useState(item.is_secret ? "" : (item.value ?? ""));
  // 저장·해제 후 서버 값이 바뀌면 동기화(입력 중엔 refetch가 없어 방해 없음).
  useEffect(() => {
    if (!item.is_secret) setValue(item.value ?? "");
  }, [item.is_secret, item.value]);

  const save = async (next: string) => {
    try {
      await update.mutateAsync({ key: item.key, value: next });
      toast.success(`${item.label} 저장됨`);
      if (item.is_secret) setValue("");
    } catch (err) {
      toast.error("저장 실패", { description: errMsg(err, "값을 확인해 주세요.") });
    }
  };

  const onClear = () => void save("");

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <Label htmlFor={`set-${item.key}`} className="text-sm">
          {item.is_secret ? (
            <KeyRound className="mr-1 inline h-3.5 w-3.5 text-fg-tertiary" aria-hidden />
          ) : null}
          {item.label}
        </Label>
        {item.configured ? (
          <Badge variant={item.source === "db" ? "default" : "secondary"} className="text-[10px]">
            {item.source === "db" ? "설정됨" : "기본값(.env)"}
          </Badge>
        ) : (
          <Badge variant="outline" className="text-[10px]">
            미설정
          </Badge>
        )}
        <span className="ml-auto font-mono text-[10px] text-fg-tertiary">{item.key}</span>
      </div>
      <div className="flex items-center gap-2">
        {item.kind === "enum" && item.options ? (
          // 드롭다운은 선택 즉시 저장(선택=의도). 자유입력·오타 방지.
          <Select
            value={value || undefined}
            onValueChange={(v) => {
              setValue(v);
              void save(v);
            }}
          >
            <SelectTrigger className="font-mono" disabled={update.isPending}>
              <SelectValue placeholder="선택" />
            </SelectTrigger>
            <SelectContent>
              {item.options.map((o) => (
                <SelectItem key={o.value} value={o.value}>
                  {o.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <>
            <Input
              id={`set-${item.key}`}
              type={item.is_secret ? "password" : "text"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={
                item.is_secret ? (item.configured ? "변경하려면 새 값 입력" : "값 입력") : "값 입력"
              }
              className="font-mono"
              autoComplete="off"
            />
            <Button
              size="sm"
              onClick={() => void save(value)}
              disabled={update.isPending || value.trim() === ""}
            >
              <Save className="mr-1 h-3.5 w-3.5" />
              저장
            </Button>
          </>
        )}
        {item.source === "db" ? (
          <Button
            variant="ghost"
            size="sm"
            onClick={onClear}
            disabled={update.isPending}
            title="입력값 삭제(.env 기본값으로 복귀)"
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        ) : null}
      </div>
    </div>
  );
}
