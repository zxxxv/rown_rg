import { KeyRound, Save, Settings2, X } from "lucide-react";
import { useState } from "react";
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
import { useAuth } from "@/hooks/useAuth";

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
          groups.map((group) => (
            <Card key={group}>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">{group}</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                {grouped[group].map((item) => (
                  <SettingRow key={item.key} item={item} />
                ))}
              </CardContent>
            </Card>
          ))
        )}
      </div>
    </AppShell>
  );
}

function SettingRow({ item }: { item: SettingItem }) {
  const update = useUpdateSetting();
  // 비밀 아닌 값은 현재 값을 프리필, 시크릿은 항상 빈칸에서 시작(되읽기 불가).
  const [value, setValue] = useState(item.is_secret ? "" : (item.value ?? ""));

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
