import { Globe, HardDriveUpload, Library } from "lucide-react";

// 자료 출처 안내 — 토글이 아니라 정보 카드다(2026-08-04 정리).
// 웹 검색은 파이프라인이 항상 실행하는 기본 경로라 켜고 끌 대상이 아니고,
// 라이브러리·업로드는 설계 시 스위치가 아니라 자료 검토 단계에서 '추가하는 행위'로 붙는다.
const CHANNELS = [
  {
    icon: Globe,
    label: "AI 자동 인터넷 검색",
    description: "목차 기반으로 최신 통계·뉴스·연구자료를 수집합니다.",
    badge: "기본 실행",
    ready: true,
  },
  {
    icon: Library,
    label: "회사 자료 라이브러리",
    description: "사내 저장소의 검증된 자료를 자료 검토 단계에서 추가.",
    badge: "준비 중",
    ready: false,
  },
  {
    icon: HardDriveUpload,
    label: "자료 직접 업로드",
    description: "PDF·HWPX·DOCX 파일을 자료 검토 단계에서 추가.",
    badge: "준비 중",
    ready: false,
  },
] as const;

/** 자료가 어디서 오는지 알려주는 읽기 전용 안내 — 폼 값과 무관하다. */
export function SourceChannels() {
  return (
    <div className="flex flex-col gap-2">
      {CHANNELS.map((ch) => (
        <div
          key={ch.label}
          className="flex items-start gap-3 rounded border border-border bg-bg p-3"
        >
          <ch.icon className="mt-0.5 h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
          <div className="flex flex-1 flex-col gap-0.5">
            <p className="text-sm font-medium text-fg">{ch.label}</p>
            <p className="text-xs text-fg-tertiary">{ch.description}</p>
          </div>
          <span
            className={
              ch.ready
                ? "shrink-0 rounded-sm bg-bg-success px-1.5 py-0.5 text-[10px] font-medium text-fg-success"
                : "shrink-0 rounded-sm border border-border bg-bg-secondary px-1.5 py-0.5 text-[10px] text-fg-tertiary"
            }
          >
            {ch.badge}
          </span>
        </div>
      ))}
    </div>
  );
}
