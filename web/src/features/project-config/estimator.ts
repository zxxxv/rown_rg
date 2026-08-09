import type { DepthMode, ProjectConfig } from "@/api/types";

export interface EstimateResult {
  estimatedHours: number;
  estimatedTokens: number;
  estimatedCostUsd: number;
}

const WRITING_BASE_FULL: EstimateResult = {
  estimatedHours: 7,
  estimatedTokens: 2_000_000,
  estimatedCostUsd: 100,
};

export const DEPTH_MULTIPLIER: Record<DepthMode, number> = {
  outline_only: 0.15,
  standard: 0.4,
  full_report: 1.0,
  deep_dive: 1.8,
};

const PER_ANALYZER: EstimateResult = {
  estimatedHours: 0.5,
  estimatedTokens: 200_000,
  estimatedCostUsd: 15,
};

// 웹 검색(자료 수집)은 파이프라인 기본 실행 - 항상 가산한다.
const WEB_SEARCH_COST: EstimateResult = {
  estimatedHours: 1.0,
  estimatedTokens: 200_000,
  estimatedCostUsd: 15,
};

function add(a: EstimateResult, b: EstimateResult): EstimateResult {
  return {
    estimatedHours: a.estimatedHours + b.estimatedHours,
    estimatedTokens: a.estimatedTokens + b.estimatedTokens,
    estimatedCostUsd: a.estimatedCostUsd + b.estimatedCostUsd,
  };
}

function scale(v: EstimateResult, k: number): EstimateResult {
  return {
    estimatedHours: v.estimatedHours * k,
    estimatedTokens: v.estimatedTokens * k,
    estimatedCostUsd: v.estimatedCostUsd * k,
  };
}

export function estimate(config: ProjectConfig): EstimateResult {
  let subtotal: EstimateResult = WRITING_BASE_FULL;

  if (config.enabled_analyzers.length > 0) {
    subtotal = add(subtotal, scale(PER_ANALYZER, config.enabled_analyzers.length));
  }

  subtotal = add(subtotal, WEB_SEARCH_COST);

  const total = scale(subtotal, DEPTH_MULTIPLIER[config.depth_mode]);

  return {
    estimatedHours: Math.round(total.estimatedHours * 10) / 10,
    estimatedTokens: Math.round(total.estimatedTokens / 10_000) * 10_000,
    estimatedCostUsd: Math.round(total.estimatedCostUsd),
  };
}
