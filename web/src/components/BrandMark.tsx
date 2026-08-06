import type { SVGProps } from "react";

// 로운 리포트 로고 마크 — 파비콘(public/favicon.svg)과 동일한 도형.
// 브랜드 블루 타일 위 보고서 문서 + 상승 막대. 자기 배경을 가져 라이트/다크 어디서나 또렷.
// className으로 크기 지정(예: h-6 w-6). 색은 브랜드 고정값.
export function BrandMark({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 32 32" className={className} role="img" aria-label="로운 리포트" {...props}>
      <rect width="32" height="32" rx="7" fill="#2C6CB8" />
      <path
        d="M9 7.5A2.5 2.5 0 0 1 11.5 5H18l5 5v14.5A2.5 2.5 0 0 1 20.5 27h-9A2.5 2.5 0 0 1 9 24.5Z"
        fill="#FFFFFF"
      />
      <path d="M18 5l5 5h-3.5A1.5 1.5 0 0 1 18 8.5Z" fill="#CFE0F4" />
      <rect x="12" y="11.6" width="8" height="1.7" rx="0.85" fill="#A9C4E7" />
      <rect x="12" y="14.8" width="5.6" height="1.7" rx="0.85" fill="#A9C4E7" />
      <rect x="12" y="20.4" width="2.3" height="3.1" rx="0.6" fill="#2C6CB8" />
      <rect x="15.15" y="18.4" width="2.3" height="5.1" rx="0.6" fill="#2C6CB8" />
      <rect x="18.3" y="16.4" width="2.3" height="7.1" rx="0.6" fill="#2C6CB8" />
    </svg>
  );
}
