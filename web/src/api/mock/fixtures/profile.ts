import type { MyTokenUsage } from "@/api/types";

// 이번 달(2026-06) 기준 데모 데이터. 1일~29일 일별 사용량.
const DAILY_COST = [
  0, 1.2, 0.8, 0, 2.4, 3.1, 1.7, 0, 0, 2.9, 4.2, 3.6, 1.1, 0, 0.6, 5.3, 4.8, 2.2, 0, 0, 1.9, 6.1,
  5.4, 3.3, 0.7, 0, 0, 4.5, 2.8,
];

// input:output 토큰 비율은 대략 8:2 로 가정, 비용 1달러당 ~120K 토큰
const daily = DAILY_COST.map((cost, i) => {
  const day = String(i + 1).padStart(2, "0");
  const totalTokens = Math.round(cost * 120_000);
  return {
    date: `2026-06-${day}`,
    input_tokens: Math.round(totalTokens * 0.8),
    output_tokens: Math.round(totalTokens * 0.2),
    cost_usd: Number(cost.toFixed(2)),
  };
});

const totalInput = daily.reduce((s, d) => s + d.input_tokens, 0);
const totalOutput = daily.reduce((s, d) => s + d.output_tokens, 0);
const totalCost = Number(daily.reduce((s, d) => s + d.cost_usd, 0).toFixed(2));

export const MY_TOKEN_USAGE: MyTokenUsage = {
  period_start: "2026-06-01",
  period_end: "2026-06-29",
  total_input_tokens: totalInput,
  total_output_tokens: totalOutput,
  total_cost_usd: totalCost,
  cost_limit_usd: 50,
  request_count: 142,
  daily,
  by_model: [
    {
      model: "claude-opus-4",
      input_tokens: Math.round(totalInput * 0.55),
      output_tokens: Math.round(totalOutput * 0.55),
      cost_usd: Number((totalCost * 0.62).toFixed(2)),
      request_count: 58,
    },
    {
      model: "claude-sonnet-4",
      input_tokens: Math.round(totalInput * 0.3),
      output_tokens: Math.round(totalOutput * 0.3),
      cost_usd: Number((totalCost * 0.24).toFixed(2)),
      request_count: 61,
    },
    {
      model: "web-research",
      input_tokens: Math.round(totalInput * 0.15),
      output_tokens: Math.round(totalOutput * 0.15),
      cost_usd: Number((totalCost * 0.14).toFixed(2)),
      request_count: 23,
    },
  ],
};
