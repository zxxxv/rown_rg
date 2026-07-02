import { getLibraryFilesForProject } from "@/api/mock/fixtures/library";
import type { LibraryNode, Source, SourceKind } from "@/api/types";

type SourceTemplate = Omit<Source, "id" | "project_id">;

const POOL: SourceTemplate[] = [
  // 웹 검색으로 수집한 후보 출처(WebResearchService의 CollectedSource를 Source로 매핑한 형태).
  // 페이지 수(pages)는 웹 문서라 없음. reliability는 high/medium/low를 0~1로 환산.
  {
    title: "2026년 전기차 구매보조금 업무처리지침",
    source: "ev.or.kr",
    source_kind: "web_search",
    url: "https://ev.or.kr/portal/subsidy/2026",
    published_at: "2026-01-08",
    reliability: 0.92,
    summary:
      "환경부 무공해차 통합누리집. 2026년 국비 보조금 단가·차종별 상한·신청 절차 안내. 웹 검색으로 수집.",
    is_included: null,
    quotes: ["승용 전기차 국비 보조금 상한은 차량가 5,300만원 미만 기준 최대 650만원."],
  },
  {
    title: "전기차 보조금 2026 — 차종별 지원금 총정리",
    source: "news.example.co.kr",
    source_kind: "web_search",
    url: "https://news.example.co.kr/ev-subsidy-2026",
    published_at: "2026-01-15",
    reliability: 0.66,
    summary:
      "주요 전기 승용·화물 모델별 예상 보조금 비교 기사. 일부 수치는 잠정치로 공식 공고와 차이 가능. 웹 검색으로 수집.",
    is_included: null,
    quotes: ["경형 전기화물차는 국비 1,100만원에 지자체 보조가 추가된다."],
  },
  {
    title: "지자체별 전기차 추가 보조금 한눈에 보기",
    source: "blog.example.com",
    source_kind: "web_search",
    url: "https://blog.example.com/ev-local-subsidy",
    published_at: "2025-12-20",
    reliability: 0.48,
    summary:
      "개인 블로그가 정리한 지자체 추가 보조금 표. 신뢰도 낮고 갱신 누락 가능 — 교차검증 권장. 웹 검색으로 수집.",
    is_included: null,
    quotes: ["서울시 추가 보조금은 예산 소진 시 조기 마감될 수 있다."],
  },
  {
    title: "2026년도 환경부 예산안 — 무공해차 보급",
    source: "me.go.kr",
    source_kind: "web_search",
    url: "https://me.go.kr/budget/2026",
    published_at: "2025-12-03",
    reliability: 0.9,
    summary: "환경부 2026 예산안 중 전기·수소차 보급 예산 규모와 전년 대비 증감. 웹 검색으로 수집.",
    is_included: null,
    quotes: ["2026년 무공해차 보급 예산은 전년 대비 4.2% 증액 편성됐다."],
  },
  {
    title: "2024 인구주택총조사 표본 집계 결과",
    source: "통계청",
    source_kind: "gov",
    url: "https://kostat.go.kr/",
    published_at: "2024-09-15",
    pages: 156,
    reliability: 0.97,
    summary:
      "전국 시·군 단위 인구·가구·주택 통계. 65세 이상 인구 비중이 19.2%로 사상 최고. 수도권 집중도 50.7%.",
    is_included: null,
    quotes: [
      "65세 이상 고령 인구 비율은 19.2%로 전년 대비 1.0%p 상승했다.",
      "수도권에 거주하는 인구는 전체의 50.7%로 집계됐다.",
      "1인 가구는 전체 가구의 35.5%로 가장 비중이 높았다.",
    ],
  },
  {
    title: "광역교통망 계획 5개년 추진 현황",
    source: "국토교통부",
    source_kind: "gov",
    url: "https://molit.go.kr/",
    published_at: "2024-11-20",
    pages: 88,
    reliability: 0.94,
    summary:
      "GTX·BRT·도시철도 노선의 추진 현황 정리. 24개 노선 중 18개 진행, 6개 지연. 사업비 누적 32조원.",
    is_included: null,
    quotes: [
      "GTX-A 운정~서울역 구간은 2024년 12월 개통 예정.",
      "예비타당성조사 단계의 노선 6개가 평균 14개월 지연.",
    ],
  },
  {
    title: "공공투자 예비타당성조사 운용 지침",
    source: "기획재정부",
    source_kind: "gov",
    url: "https://moef.go.kr/",
    published_at: "2024-02-08",
    pages: 42,
    reliability: 0.96,
    summary:
      "예타조사의 절차·평가 항목·통과 기준. 비용·편익 분석, 정책성, 지역균형 가중치 0.5·0.25·0.25.",
    is_included: null,
    quotes: ["B/C 1.0 이상이 권고 조건이며, AHP 종합점수 0.5 이상이어야 한다."],
  },
  {
    title: "고령화 진전이 노동시장에 미치는 영향",
    source: "한국개발연구원(KDI)",
    source_kind: "academic",
    url: "https://kdi.re.kr/",
    published_at: "2023-12-04",
    pages: 64,
    reliability: 0.92,
    summary:
      "생산가능인구의 감소가 잠재성장률·임금·복지 지출에 미치는 거시 영향 추정. 2030년 잠재성장률 1.5% 전망.",
    is_included: null,
    quotes: ["2030년 잠재성장률은 1.5%로 둔화될 전망이다."],
  },
  {
    title: "수도권 광역교통 수요 분석",
    source: "한국교통연구원",
    source_kind: "academic",
    published_at: "2024-05-22",
    pages: 112,
    reliability: 0.89,
    summary: "GTX 개통 후 환승 수요 시뮬레이션. 수도권 외곽~중심 통근 시간 평균 18분 단축 예측.",
    is_included: null,
    quotes: ["통근 시간이 평균 18분 단축될 것으로 추정된다."],
  },
  {
    title: "지방소멸위험지수 2024년 평가",
    source: "한국고용정보원",
    source_kind: "academic",
    published_at: "2024-06-30",
    pages: 36,
    reliability: 0.88,
    summary:
      "전국 228개 시·군·구 중 113곳이 소멸위험지역. 전남·경북·강원 집중. 청년 인구 유출이 핵심 원인.",
    is_included: null,
    quotes: ["전체 시·군·구의 49.6%가 소멸위험지역으로 분류됐다."],
  },
  {
    title: "GTX 사업 효과 평가 (감사원)",
    source: "감사원",
    source_kind: "gov",
    published_at: "2024-03-11",
    pages: 78,
    reliability: 0.93,
    summary: "GTX-A·B·C 노선의 비용편익·사업관리·재원조달 적정성 감사. 비용 초과 1.8조원 지적.",
    is_included: null,
    quotes: ["전체 사업비 대비 비용 초과는 1.8조원에 달했다."],
  },
  {
    title: "공공기관 지방이전 경제 효과 분석",
    source: "한국행정연구원",
    source_kind: "academic",
    published_at: "2023-08-19",
    pages: 92,
    reliability: 0.87,
    summary:
      "153개 공공기관 지방이전 후 지역내총생산·고용·인구 변화 추적. 단기 효과 큼, 장기 효과 제한.",
    is_included: null,
    quotes: ["이전 후 5년 시점 GRDP 평균 3.2% 증가에 그쳤다."],
  },
  {
    title: "한국은행 경제전망보고서 2024-IV",
    source: "한국은행",
    source_kind: "gov",
    url: "https://bok.or.kr/",
    published_at: "2024-11-28",
    pages: 138,
    reliability: 0.95,
    summary:
      "2025년 경제성장률 2.1% 전망. 수출 회복·내수 둔화·물가 안정. 기준금리 점진적 인하 시사.",
    is_included: null,
    quotes: ["2025년 한국 경제는 2.1% 성장할 것으로 전망된다."],
  },
  {
    title: "정부 R&D 투자 성과 분석",
    source: "과학기술정책연구원",
    source_kind: "academic",
    published_at: "2023-04-12",
    pages: 84,
    reliability: 0.86,
    summary: "공공 R&D 예산의 사업화 비율·논문·특허 성과. 사업화율 정체, 논문 수 증가.",
    is_included: null,
    quotes: ["공공 R&D 사업화율은 5년간 7%대에 정체됐다."],
  },
  {
    title: "예비타당성조사 보고서 양식 (회사 내부)",
    source: "로운인사이트 라이브러리",
    source_kind: "library",
    published_at: "2023-06-01",
    pages: 28,
    reliability: 0.91,
    summary:
      "회사 표준 예타조사 보고서 템플릿. 1·2·3·4 단원 구성, 함초롬바탕 11pt, 회사 양식 변수.",
    is_included: null,
    quotes: ["본 양식은 2023년 6월 개정판이며 모든 예타 보고서에 적용한다."],
  },
  {
    title: "이전 예타 보고서 — 지방 공항 활성화",
    source: "로운인사이트 라이브러리",
    source_kind: "library",
    published_at: "2023-11-15",
    pages: 198,
    reliability: 0.84,
    summary: "유사 주제 회사 보고서. 이용률 낮은 5개 공항의 활성화 방안과 노선 재편 분석.",
    is_included: null,
    quotes: ["이용률이 30% 미만인 공항이 3곳으로 확인됐다."],
  },
  {
    title: "수도권 광역교통 사업 보도자료 모음",
    source: "조선일보",
    source_kind: "media",
    url: "https://chosun.com/",
    published_at: "2024-10-05",
    pages: 12,
    reliability: 0.72,
    summary: "GTX·BRT 관련 보도 12건 요약. 사업 지연·예산 증액·주민 반발 관련 기사 위주.",
    is_included: null,
    quotes: ["GTX-B 사업비가 1조원 추가될 가능성이 제기됐다."],
  },
  {
    title: "재정 지출 효율성 평가 보고서",
    source: "국회예산정책처",
    source_kind: "gov",
    published_at: "2024-07-08",
    pages: 156,
    reliability: 0.9,
    summary: "주요 SOC·복지·교육 사업의 비용 대비 산출 효과 평가. 비효율 사업 14개 지적.",
    is_included: null,
    quotes: ["B/C가 0.8 미만인 사업이 14건으로 분류됐다."],
  },
  {
    title: "스마트시티 정책 동향 — 한겨레",
    source: "한겨레",
    source_kind: "media",
    published_at: "2024-08-22",
    pages: 6,
    reliability: 0.68,
    summary: "주요 도시의 스마트시티 사업 동향 기획기사. 통합 플랫폼 미흡, 시민 참여 부족 지적.",
    is_included: null,
    quotes: ["10개 도시 중 통합 플랫폼이 가동 중인 곳은 3개에 그친다."],
  },
  {
    title: "고령화 대응 정책 백서 2024",
    source: "보건복지부",
    source_kind: "gov",
    published_at: "2024-04-18",
    pages: 224,
    reliability: 0.93,
    summary:
      "초고령사회 진입 대비 종합 정책. 노인일자리·장기요양·기초연금 항목별 추진 현황과 과제.",
    is_included: null,
    quotes: ["2025년 노인일자리 사업 예산은 2.1조원으로 확정됐다."],
  },
  {
    title: "재무 분석 자료 — 사업 후보지 5곳 비교",
    source: "사용자 업로드",
    source_kind: "upload",
    published_at: "2025-01-12",
    pages: 18,
    reliability: 0.78,
    summary: "사업 후보지의 부지 매입비·운영비·예상 수입 비교 엑셀. NPV·IRR 계산 포함.",
    is_included: null,
    quotes: ["3개 후보지의 NPV가 양(+)으로 산출됐다."],
  },
  {
    title: "재생에너지 확대 정책 토론회 자료집",
    source: "에너지경제연구원",
    source_kind: "academic",
    published_at: "2023-09-25",
    pages: 102,
    reliability: 0.85,
    summary: "2030년 재생에너지 비중 30% 달성 시나리오. 정책 수단·재원·기술 한계 토론.",
    is_included: null,
    quotes: ["2030년 신재생에너지 비중 30% 목표는 추가 정책 강도 30% 상향이 필요하다."],
  },
  {
    title: "디지털 격차 실태조사 2024",
    source: "한국지능정보사회진흥원",
    source_kind: "gov",
    published_at: "2024-12-02",
    pages: 174,
    reliability: 0.89,
    summary: "고령자·장애인·저소득층의 디지털 접근성·역량·활용 수준 조사. 격차 점수 78.5점.",
    is_included: null,
    quotes: ["고령자의 디지털 정보화 수준은 일반인 대비 78.5%로 측정됐다."],
  },
  {
    title: "이전 보고서 — 재생에너지 사업 검토",
    source: "로운인사이트 라이브러리",
    source_kind: "library",
    published_at: "2024-02-28",
    pages: 244,
    reliability: 0.82,
    summary:
      "회사 내 이전 작업물. 호남·영남 권역 태양광·풍력 발전소 사업타당성. 자료 일부는 갱신 필요.",
    is_included: null,
    quotes: ["호남권 태양광 후보지 12곳 중 8곳이 사업성을 갖춘 것으로 분석됐다."],
  },
  {
    title: "지역 산업 분석 자료 (개인 블로그)",
    source: "example.blog.com",
    source_kind: "media",
    url: "https://example.blog.com/",
    published_at: "2025-03-10",
    pages: 4,
    reliability: 0.62,
    summary: "개인 블로그의 지역 산업 분석. 출처 신뢰도 낮음, 통계 근거 빈약. 참고만 권장.",
    is_included: null,
    quotes: ["지역 산업 다각화가 시급하다는 주장은 추가 근거가 필요하다."],
  },
  {
    title: "도시재생 뉴딜 사업 평가 (KDI)",
    source: "한국개발연구원(KDI)",
    source_kind: "academic",
    published_at: "2024-01-20",
    pages: 188,
    reliability: 0.9,
    summary:
      "1차 도시재생 뉴딜 250개 사업의 성과 평가. 정주여건 개선 성과 있음, 일자리 창출은 미흡.",
    is_included: null,
    quotes: ["일자리 창출 효과는 사업당 평균 12명에 그쳤다."],
  },
  {
    title: "공공의료 인력 수급 정책 자료",
    source: "보건사회연구원",
    source_kind: "academic",
    published_at: "2024-10-30",
    pages: 96,
    reliability: 0.88,
    summary: "공공의료 의사·간호사·약사 수급 현황과 양성 정책. 농어촌·도서 지역 부족 심각.",
    is_included: null,
    quotes: ["농어촌 공공의료 의사 부족률은 41.2%로 추정됐다."],
  },
  {
    title: "신규 보고서용 자료 — PDF 업로드",
    source: "사용자 업로드",
    source_kind: "upload",
    published_at: "2025-02-04",
    pages: 36,
    reliability: 0.75,
    summary: "사용자가 직접 업로드한 자료. 사업 후보지의 지반·환경 영향 평가 요약.",
    is_included: null,
    quotes: ["환경 영향 평가 결과 후보지 5곳 중 1곳이 부적합 판정됐다."],
  },
  {
    title: "수자원 재이용 인프라 동향 — 환경부",
    source: "환경부",
    source_kind: "gov",
    published_at: "2024-07-19",
    pages: 64,
    reliability: 0.86,
    summary: "물 부족 대응을 위한 재이용수 공급망 구축의 정책 동향과 투자 계획.",
    is_included: null,
    quotes: ["2030년까지 재이용수 공급 비율 8%를 목표로 한다."],
  },
];

function hashCode(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

const sourcesByProject = new Map<string, Source[]>();

function firstFileIdByKind(files: LibraryNode[], kind: SourceKind): string | undefined {
  for (const n of files) {
    if (n.type === "file" && n.file_meta.source_kind === kind) return n.id;
  }
  return undefined;
}

export function getSourcesForProject(projectId: string): Source[] {
  let existing = sourcesByProject.get(projectId);
  if (!existing) {
    const count = 15 + (hashCode(projectId) % 11);
    const libFiles = getLibraryFilesForProject(projectId);
    const assignedByKind: Record<string, boolean> = {};
    existing = POOL.slice(0, count).map((tpl, i) => {
      const src: Source = {
        ...tpl,
        id: `src_${projectId}_${String(i + 1).padStart(3, "0")}`,
        project_id: projectId,
      };
      if (!assignedByKind[tpl.source_kind]) {
        const fid = firstFileIdByKind(libFiles, tpl.source_kind);
        if (fid) {
          src.library_file_id = fid;
          assignedByKind[tpl.source_kind] = true;
        }
      }
      return src;
    });
    sourcesByProject.set(projectId, existing);
  }
  return existing;
}

export function patchSourceInStore(
  projectId: string,
  sourceId: string,
  changes: Partial<Pick<Source, "is_included">>,
): Source | undefined {
  const list = getSourcesForProject(projectId);
  const idx = list.findIndex((s) => s.id === sourceId);
  if (idx < 0) return undefined;
  list[idx] = { ...list[idx], ...changes };
  return list[idx];
}

export function pushSourceToStore(projectId: string, source: Source): void {
  const list = getSourcesForProject(projectId);
  list.unshift(source);
}

export function createUploadedSource(
  projectId: string,
  fileName: string,
  fileSize: number,
): Source {
  const ext = fileName.split(".").pop()?.toLowerCase() ?? "";
  return {
    id: `src_${projectId}_upload_${Date.now()}_${Math.floor(Math.random() * 1000)}`,
    project_id: projectId,
    title: fileName,
    source: "사용자 업로드",
    source_kind: "upload",
    published_at: new Date().toISOString().slice(0, 10),
    pages: Math.max(1, Math.floor(fileSize / 25_000)),
    reliability: 0.7,
    summary: `${ext.toUpperCase()} 파일 업로드. 자동 요약은 인덱싱 단계에서 갱신됩니다.`,
    is_included: null,
  };
}
