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

const DIFFERENTIATOR_COST: Record<
  keyof Pick<
    ProjectConfig,
    | "enable_pre_reconciliation"
    | "enable_consistency_graph"
    | "enable_dual_track_search"
    | "enable_source_tagging"
    | "enable_critic_agent"
    | "enable_glossary"
  >,
  EstimateResult
> = {
  enable_pre_reconciliation: {
    estimatedHours: 0.08,
    estimatedTokens: 30_000,
    estimatedCostUsd: 2,
  },
  enable_consistency_graph: {
    estimatedHours: 0.5,
    estimatedTokens: 100_000,
    estimatedCostUsd: 10,
  },
  enable_dual_track_search: {
    estimatedHours: 0.3,
    estimatedTokens: 80_000,
    estimatedCostUsd: 5,
  },
  enable_source_tagging: {
    estimatedHours: 0.2,
    estimatedTokens: 50_000,
    estimatedCostUsd: 3,
  },
  enable_critic_agent: {
    estimatedHours: 0.17,
    estimatedTokens: 100_000,
    estimatedCostUsd: 5,
  },
  enable_glossary: {
    estimatedHours: 0.5,
    estimatedTokens: 100_000,
    estimatedCostUsd: 5,
  },
};

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

  for (const key of Object.keys(DIFFERENTIATOR_COST) as Array<keyof typeof DIFFERENTIATOR_COST>) {
    if (config[key]) subtotal = add(subtotal, DIFFERENTIATOR_COST[key]);
  }

  if (config.sources.use_web_search) subtotal = add(subtotal, WEB_SEARCH_COST);

  const total = scale(subtotal, DEPTH_MULTIPLIER[config.depth_mode]);

  return {
    estimatedHours: Math.round(total.estimatedHours * 10) / 10,
    estimatedTokens: Math.round(total.estimatedTokens / 10_000) * 10_000,
    estimatedCostUsd: Math.round(total.estimatedCostUsd),
  };
}
