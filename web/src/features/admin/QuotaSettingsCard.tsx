import { Coins, Save } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { apiErrorMessage } from "@/api/client";
import {
  QUOTA_META,
  type QuotaSetting,
  useQuotaSettings,
  useUpdateQuotaSettings,
} from "@/api/quota-settings";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/useAuth";

type Meta = (typeof QUOTA_META)[number];

// 조직 상한 + 역할별 기본 한도 편집 카드. 개인별 한도(대시보드)와 다른 '밑값'을 다룬다.
// 조회·수정 모두 admin+ (백엔드 require_role(*ADMINS)와 일치, 2026-08-26 결정).
// 역할 기본값은 조직 한도를 넘길 수 없다 - 넘기면 서버가 422로 막는다.
export function QuotaSettingsCard() {
  const query = useQuotaSettings();
  const { user } = useAuth();
  const canEdit = user?.role === "super_admin" || user?.role === "admin";

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Coins className="h-4 w-4 text-fg-secondary" aria-hidden />
          조직 한도 (USD)
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {!canEdit ? (
          <p className="text-xs text-fg-tertiary">
            수정은 관리자 이상만 할 수 있습니다 - 현재 읽기 전용입니다.
          </p>
        ) : (
          <p className="text-xs text-fg-tertiary">
            역할 기본 한도는 조직 한도를 넘길 수 없습니다. 변경 기록은 남습니다.
          </p>
        )}
        {query.isLoading ? (
          <p className="text-sm text-fg-tertiary">불러오는 중…</p>
        ) : query.isError || !query.data ? (
          <p className="text-sm text-fg-secondary">한도를 불러오지 못했습니다.</p>
        ) : (
          QUOTA_META.map((meta) => (
            <QuotaRow
              key={meta.key}
              meta={meta}
              row={query.data.find((r) => r.key === meta.key)}
              canEdit={canEdit}
            />
          ))
        )}
      </CardContent>
    </Card>
  );
}

function QuotaRow({
  meta,
  row,
  canEdit,
}: {
  meta: Meta;
  row: QuotaSetting | undefined;
  canEdit: boolean;
}) {
  const update = useUpdateQuotaSettings();
  const [value, setValue] = useState(row?.value ?? "");

  const parsed = Number(value);
  const valid =
    value.trim() !== "" && Number.isInteger(parsed) && parsed >= meta.min && parsed <= meta.max;
  const dirty = value !== (row?.value ?? "");

  const save = async () => {
    if (!valid) return;
    try {
      await update.mutateAsync({ [meta.key]: String(parsed) });
      toast.success(`${meta.label} 저장됨`);
    } catch (err) {
      toast.error("저장 실패", { description: apiErrorMessage(err, "값을 확인해 주세요.") });
    }
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <Label htmlFor={`quota-${meta.key}`} className="text-sm">
          {meta.label}
        </Label>
        {row?.updated_at ? (
          <Badge variant="secondary" className="text-[10px]">
            변경 {row.updated_at.slice(0, 10)}
          </Badge>
        ) : null}
        <span className="ml-auto font-mono text-[10px] text-fg-tertiary">{meta.key}</span>
      </div>
      <p className="text-xs text-fg-tertiary">{meta.hint}</p>
      <div className="flex items-center gap-2">
        <Input
          id={`quota-${meta.key}`}
          type="number"
          inputMode="numeric"
          min={meta.min}
          max={meta.max}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          disabled={!canEdit || update.isPending}
          className="font-mono"
        />
        <span className="text-sm text-fg-secondary">USD</span>
        <Button
          size="sm"
          onClick={() => void save()}
          disabled={!canEdit || !valid || !dirty || update.isPending}
        >
          <Save className="mr-1 h-3.5 w-3.5" aria-hidden />
          저장
        </Button>
      </div>
      {value.trim() !== "" && !valid ? (
        <p className="text-xs text-fg-tertiary">
          {meta.min.toLocaleString()}~{meta.max.toLocaleString()} 사이 정수여야 합니다.
        </p>
      ) : null}
    </div>
  );
}
