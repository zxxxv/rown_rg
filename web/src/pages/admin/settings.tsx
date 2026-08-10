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
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useAuth } from "@/hooks/useAuth";

// 알림용 자격증명 그룹 - 로그인(SSO)엔 불필요라 기본 접어둔다.
const COLLAPSED_BY_DEFAULT = new Set(["네이버웍스"]);
const GROUP_HINT: Record<string, string> = {
  네이버웍스: "알림 봇 전용 - 로그인(SSO)엔 불필요합니다. 알림을 쓸 때만 채우세요.",
  SSO: "네이버웍스 로그인(SAML) - IdP 콘솔의 Identity Provider 정보에서 복사해 넣습니다. 위 '네이버웍스'(알림 봇)와 별개입니다.",
};
// 감출 설정 키. 절약 모드의 본문 작성이 gpt-5.4-mini라 OpenAI 키는 실제로 쓰인다 -
// 숨기면 키를 못 넣어 그 모드가 통째로 죽는다(2026-08-10 되돌림).
const HIDDEN_KEYS = new Set<string>();

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

const GROUP_ORDER = ["LLM", "SSO", "네이버웍스"];

export default function AdminSettingsPage() {
  const { user, logout } = useAuth();
  const query = useSettings();

  const grouped: Record<string, SettingItem[]> = {};
  for (const item of query.data ?? []) {
    if (HIDDEN_KEYS.has(item.key)) continue;
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
            API 키·네이버웍스 자격증명을 입력합니다. .env보다 우선 적용되며, 시크릿은 암호화
            저장되어 다시 조회할 수 없습니다. (최고관리자 전용) 조직 한도는 별도 '조직 한도' 페이지,
            알림은 프로젝트별 설정에서 관리합니다.
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
  // 입력칸은 항상 빈칸에서 시작한다 - 설정된 값은 위에 읽기 전용으로 보여주고,
  // 이 칸은 "새 값을 넣을 때만" 쓴다(시크릿과 같은 동선으로 통일, 2026-08-10).
  const [value, setValue] = useState("");
  // 서버 값이 바뀌면(저장·해제) 입력칸을 다시 비운다.
  // biome-ignore lint/correctness/useExhaustiveDependencies: item.value 변화가 트리거다
  useEffect(() => {
    setValue("");
  }, [item.value]);

  const save = async (next: string) => {
    try {
      await update.mutateAsync({ key: item.key, value: next });
      toast.success(`${item.label} 저장됨`);
      setValue("");
    } catch (err) {
      toast.error("저장 실패", { description: errMsg(err, "값을 확인해 주세요.") });
    }
  };

  const onClear = () => void save("");
  // 비밀값은 저장 후 칸이 비므로 '변경 있음'이 자명하다. 일반값은 값이 남아 있어
  // 눌러도 화면이 그대로라 저장 여부를 알 수 없었다(2026-08-10 지적) - 바뀐 게
  // 없으면 버튼을 잠가 "누를 게 없음"을 눈에 보이게 한다.
  // 읽기 전용으로 보여줄 현재 값(시크릿은 되읽기 불가라 없음). 긴 값은 줄여서.
  const current = item.is_secret || item.kind === "bool" ? null : (item.value ?? "");
  const currentShort =
    current && current.length > 88 ? `${current.slice(0, 60)}… (${current.length}자)` : current;

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
      {currentShort ? (
        <p className="break-all font-mono text-[11px] text-fg-tertiary">현재: {currentShort}</p>
      ) : null}
      <div className="flex items-center gap-2">
        {item.kind === "bool" ? (
          // 켜고 끄는 값 - 토글이 곧 의도라 선택 즉시 저장한다(드롭다운과 같은 규칙).
          <div className="flex items-center gap-2">
            <Switch
              id={`set-${item.key}`}
              checked={item.value === "true"}
              disabled={update.isPending}
              onCheckedChange={(on) => void save(on ? "true" : "false")}
            />
            <span className="text-sm text-fg-secondary">
              {item.value === "true" ? "사용" : "사용 안 함"}
            </span>
          </div>
        ) : item.kind === "enum" && item.options ? (
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
            {item.kind === "text" ? (
              // 인증서처럼 긴 값 - 한 줄 입력으론 확인도 수정도 안 된다.
              <Textarea
                id={`set-${item.key}`}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={
                  item.configured ? "변경하려면 새 값 입력(BEGIN/END 줄 제외)" : "값 입력"
                }
                className="min-h-[96px] font-mono text-xs"
                autoComplete="off"
              />
            ) : (
              <Input
                id={`set-${item.key}`}
                type={item.is_secret ? "password" : "text"}
                value={value}
                onChange={(e) => setValue(e.target.value)}
                placeholder={item.configured ? "변경하려면 새 값 입력" : "값 입력"}
                className="font-mono"
                autoComplete="off"
              />
            )}
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
        {item.source === "db" && item.kind !== "bool" ? (
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
