# OpenAI API GPT 모델 가격 및 토큰 비용 정리
> 단위: 별도 표기가 없으면 **USD / 1M tokens**

## 공식 출처

- [OpenAI API Pricing](https://developers.openai.com/api/docs/pricing)
- [OpenAI API Models](https://developers.openai.com/api/docs/models)
- [GPT-5.5 model](https://developers.openai.com/api/docs/models/gpt-5.5)
- [GPT-5.4 model](https://developers.openai.com/api/docs/models/gpt-5.4)
- [GPT-5.4 mini model](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
- [GPT-5.4 nano model](https://developers.openai.com/api/docs/models/gpt-5.4-nano)
- [Batch API](https://developers.openai.com/api/docs/guides/batch)
- [Flex processing](https://developers.openai.com/api/docs/guides/flex-processing)
- [Reasoning models](https://developers.openai.com/api/docs/guides/reasoning)

---

### 포함 모델

- `gpt-5.5`
- `gpt-5.5-pro`
- `gpt-5.4`
- `gpt-5.4-pro`
- `gpt-5.4-mini`
- `gpt-5.4-nano`

### 제외 대상

- Codex 전용 모델
- `o` 계열 및 Deep Research 모델
- Realtime / 음성 / 이미지 / 영상 생성 모델
- Embedding 모델
- Fine-tuning 비용
- Web search, File search, Code Interpreter 등의 도구 호출 비용

도구를 사용하면 모델 토큰 비용 외에 별도 도구 비용이 추가될 수 있다.

---

## 1. 핵심 요약

OpenAI API 가격을 볼 때는 **Usage Tier**와 **Processing Tier**를 구분해야 한다.

| 구분 | 의미 | 토큰 단가에 직접 영향 |
|---|---|---:|
| Usage Tier: Free / Tier 1~5 | RPM, TPM, Batch queue 등 사용 한도 | 없음 |
| Processing Tier: Standard / Batch / Flex / Priority | 요청을 처리하는 방식과 우선순위 | 있음 |

즉, 계정의 Usage Tier가 올라가도 같은 처리 방식과 모델을 사용하면 기본 토큰 단가가 자동으로 낮아지는 구조는 아니다.

또한 Responses API와 Chat Completions API 자체에 별도 기본 이용료가 붙는 것은 아니다. 실제 비용은 선택한 모델의 입력·캐시 입력·출력 토큰과 별도 도구 사용량을 기준으로 계산한다.

---

## 2. Processing Tier별 가격 구조

| 처리 방식 | 의미 | 가격 특성 | 적합한 용도 |
|---|---|---|---|
| Standard | 일반적인 실시간 API 호출 | 기준 가격 | 챗봇, 일반 서비스 응답 |
| Batch | 요청을 모아 비동기로 처리 | Standard 대비 약 50% 저렴 | 평가, 대량 분류, 문서 일괄 처리 |
| Flex | 느린 응답과 일시적 자원 부족을 허용하는 저비용 처리 | Batch와 같은 토큰 단가 | 비프로덕션, 데이터 보강, 저우선순위 작업 |
| Priority | 우선 처리 | Standard보다 비쌈 | 지연시간과 처리 안정성이 중요한 프로덕션 작업 |

### 주의

- Batch는 일반적으로 24시간 이내 처리되는 비동기 작업이다.
- Flex는 베타 기능이며 지원 모델이 제한된다.
- Priority도 모든 모델이 지원되는 것은 아니다.
- 지원 여부와 단가는 공식 가격표의 현재 목록을 기준으로 확인해야 한다.

---

# 3. Standard 가격

## 3-1. Short context

| 모델 | Input | Cached input | Output |
|---|---:|---:|---:|
| `gpt-5.5` | $5.00 | $0.50 | $30.00 |
| `gpt-5.5-pro` | $30.00 | 할인 없음 | $180.00 |
| `gpt-5.4` | $2.50 | $0.25 | $15.00 |
| `gpt-5.4-pro` | $30.00 | 할인 없음 | $180.00 |
| `gpt-5.4-mini` | $0.75 | $0.075 | $4.50 |
| `gpt-5.4-nano` | $0.20 | $0.02 | $1.25 |

## 3-2. Long context

| 모델 | Input | Cached input | Output |
|---|---:|---:|---:|
| `gpt-5.5` | $10.00 | $1.00 | $45.00 |
| `gpt-5.5-pro` | $60.00 | 할인 없음 | $270.00 |
| `gpt-5.4` | $5.00 | $0.50 | $22.50 |
| `gpt-5.4-pro` | $60.00 | 할인 없음 | $270.00 |
| `gpt-5.4-mini` | 별도 long-context 단가 없음 | - | - |
| `gpt-5.4-nano` | 별도 long-context 단가 없음 | - | - |

### Long context 적용 기준

- 공식 모델 문서에서 `gpt-5.5`, `gpt-5.4`, `gpt-5.4-pro`는 **입력 프롬프트가 272K tokens를 초과하면** 세션 전체에 long-context 가격이 적용된다고 안내한다.
- 이 경우 입력은 short-context의 2배, 출력은 1.5배 수준이다.
- 긴 컨텍스트 사용 시 일부 토큰만이 아니라 해당 세션 전체가 long-context 단가로 계산된다는 점에 주의해야 한다.

---

# 4. Batch 가격

Batch API는 일반 동기 API 대비 약 50% 낮은 가격과 별도 rate limit pool을 제공한다.

## 4-1. Short context

| 모델 | Input | Cached input | Output |
|---|---:|---:|---:|
| `gpt-5.5` | $2.50 | $0.25 | $15.00 |
| `gpt-5.5-pro` | $15.00 | 할인 없음 | $90.00 |
| `gpt-5.4` | $1.25 | $0.13 | $7.50 |
| `gpt-5.4-pro` | $15.00 | 할인 없음 | $90.00 |
| `gpt-5.4-mini` | $0.375 | $0.0375 | $2.25 |
| `gpt-5.4-nano` | $0.10 | $0.01 | $0.625 |

## 4-2. Long context

| 모델 | Input | Cached input | Output |
|---|---:|---:|---:|
| `gpt-5.5` | $5.00 | $0.50 | $22.50 |
| `gpt-5.5-pro` | 공식 가격표에 별도 단가 미표기 | - | - |
| `gpt-5.4` | $2.50 | $0.25 | $11.25 |
| `gpt-5.4-pro` | $30.00 | 할인 없음 | $135.00 |
| `gpt-5.4-mini` | 별도 long-context 단가 없음 | - | - |
| `gpt-5.4-nano` | 별도 long-context 단가 없음 | - | - |

---

# 5. Flex 가격

Flex의 토큰 가격은 현재 공식 문서상 Batch와 같다.

## 5-1. Short context

| 모델 | Input | Cached input | Output |
|---|---:|---:|---:|
| `gpt-5.5` | $2.50 | $0.25 | $15.00 |
| `gpt-5.5-pro` | $15.00 | 할인 없음 | $90.00 |
| `gpt-5.4` | $1.25 | $0.13 | $7.50 |
| `gpt-5.4-pro` | $15.00 | 할인 없음 | $90.00 |
| `gpt-5.4-mini` | $0.375 | $0.0375 | $2.25 |
| `gpt-5.4-nano` | $0.10 | $0.01 | $0.625 |

## 5-2. Long context

| 모델 | Input | Cached input | Output |
|---|---:|---:|---:|
| `gpt-5.5` | $5.00 | $0.50 | $22.50 |
| `gpt-5.5-pro` | 공식 가격표에 별도 단가 미표기 | - | - |
| `gpt-5.4` | $2.50 | $0.25 | $11.25 |
| `gpt-5.4-pro` | $30.00 | 할인 없음 | $135.00 |
| `gpt-5.4-mini` | 별도 long-context 단가 없음 | - | - |
| `gpt-5.4-nano` | 별도 long-context 단가 없음 | - | - |

### 특징

- Batch처럼 저렴하지만 동기 Responses 또는 Chat Completions 요청으로 사용할 수 있다.
- 처리 속도가 느릴 수 있고 자원이 부족하면 `429 Resource Unavailable`이 발생할 수 있다.
- 일반 실시간 프로덕션 응답보다는 평가, 데이터 보강, 저우선순위 작업에 적합하다.

---

# 6. Priority 가격

공식 가격표에 현재 표시된 모델만 정리한다.

| 모델 | Input | Cached input | Output |
|---|---:|---:|---:|
| `gpt-5.5` | $12.50 | $1.25 | $75.00 |
| `gpt-5.4` | $5.00 | $0.50 | $30.00 |
| `gpt-5.4-mini` | $1.50 | $0.15 | $9.00 |
| `gpt-5.5-pro` | 공식 가격표에 미표기 | - | - |
| `gpt-5.4-pro` | 공식 가격표에 미표기 | - | - |
| `gpt-5.4-nano` | 공식 가격표에 미표기 | - | - |

가격표에 없는 조합은 지원된다고 가정하지 않는다.

---

# 7. 모델별 특징

## 8-1. `gpt-5.5`

- 복잡한 추론, 코딩, 전문 업무를 위한 최신 플래그십 모델
- 1.05M context window
- 고성능이지만 입력과 출력 단가가 높은 편
- 272K input 초과 시 long-context 단가에 주의

## 7-2. `gpt-5.5-pro`

- `gpt-5.5`보다 더 많은 계산을 사용해 정밀한 결과를 목표로 하는 Pro 모델
- 매우 높은 입력·출력 비용
- Cached input 할인 없음
- 난도가 매우 높은 작업에 제한적으로 사용하는 것이 적합

## 7-3. `gpt-5.4`

- 고난도 코딩 및 전문 업무를 위한 고성능 모델
- `gpt-5.5`보다 저렴한 고성능 선택지
- 1.05M context window
- 272K input 초과 시 long-context 단가 적용

## 7-4. `gpt-5.4-pro`

- `gpt-5.4`보다 더 많은 추론 계산을 사용하는 Pro 모델
- Responses API 중심의 고난도 작업용 모델
- Cached input 할인 없음
- 비용이 높으므로 일반 요청의 기본 모델로는 부적합

## 7-5. `gpt-5.4-mini`

- 고볼륨 코딩, 도구 사용, sub-agent 작업을 위한 균형형 모델
- Standard 기준 Input $0.75 / Output $4.50
- 성능과 비용의 균형이 필요한 서비스 기본 모델 후보

## 7-6. `gpt-5.4-nano`

- 단순 분류, 데이터 추출, 랭킹, sub-agent 등 비용 민감 작업용
- Standard 기준 Input $0.20 / Output $1.25
- 이번 프로젝트의 저비용 기본 모델 후보
- 최신 5.4 계열 안에서는 가장 저렴하다

---

# 8. 이전 세대 최저가 모델과 비교

`gpt-5-nano`는 이전 GPT-5 계열 모델이며 Standard 기준 가격은 다음과 같다.

| 모델 | Input | Cached input | Output |
|---|---:|---:|---:|
| `gpt-5-nano` | $0.05 | $0.005 | $0.40 |
| `gpt-5.4-nano` | $0.20 | $0.02 | $1.25 |

따라서 **절대적인 토큰 단가만 비교하면 `gpt-5-nano`가 더 저렴하다.**  
다만 OpenAI는 새로운 속도·비용 민감 워크로드에는 `gpt-5.4-nano`부터 검토할 것을 안내한다.

프로젝트 정책은 다음 중 하나로 명확히 정해야 한다.

| 정책 | 기본 모델 후보 |
|---|---|
| 절대 최저 비용 우선 | `gpt-5-nano` |
| 현재 권장 저비용 계열 우선 | `gpt-5.4-nano` |
| 비용과 성능 균형 | `gpt-5.4-mini` |

현재 구현이 `gpt-5.4-nano`를 기본값으로 사용한다면 이는 **절대 최저가 정책이 아니라 최신 권장 저비용 모델 정책**이다.

---

# 9. 토큰 비용 계산식

OpenAI 응답의 `usage.input_tokens`에는 cached input tokens가 포함될 수 있으므로, 캐시 토큰을 중복 계산하지 않도록 일반 입력 토큰과 분리해야 한다.

```text
non_cached_input_tokens = input_tokens - cached_input_tokens

총 비용 =
(non_cached_input_tokens / 1,000,000 × input 단가)
+ (cached_input_tokens / 1,000,000 × cached input 단가)
+ (output_tokens / 1,000,000 × output 단가)
```

## 예시: `gpt-5.4-nano` Standard

사용량:

```text
input_tokens = 10,000
cached_input_tokens = 2,000
output_tokens = 2,000
```

계산:

```text
non-cached input = 8,000 tokens

일반 input 비용 = 8,000 / 1,000,000 × $0.20 = $0.0016
cached input 비용 = 2,000 / 1,000,000 × $0.02 = $0.00004
output 비용 = 2,000 / 1,000,000 × $1.25 = $0.0025

총 비용 = $0.00414
```

---

# 10. Reasoning token 과금

GPT-5 계열의 reasoning tokens는 사용자에게 그대로 표시되지 않을 수 있지만, **output tokens로 과금**된다.

Responses API의 사용량 객체는 일반적으로 다음 정보를 제공한다.

```text
usage.input_tokens
usage.input_tokens_details.cached_tokens
usage.output_tokens
usage.output_tokens_details.reasoning_tokens
```

`usage.output_tokens`에는 reasoning tokens가 이미 포함되므로 비용 계산 시 다음처럼 다시 더하면 안 된다.

```text
잘못된 계산:
output_tokens + reasoning_tokens
```

올바른 계산은 `usage.output_tokens`에 출력 단가를 한 번만 적용하는 것이다.

---

# 11. 프로젝트 적용 기준

현재 OpenAI 어댑터가 지원하도록 작성한 모델:

```text
gpt-5.4-nano
gpt-5.4-mini
```

추천 정책:

| 작업 | 추천 모델 |
|---|---|
| 분류, 추출, 단순 요약, 고볼륨 작업 | `gpt-5.4-nano` |
| 일반 챗봇, 코딩 보조, tool use | `gpt-5.4-mini` |
| 복잡한 코딩, 전문 분석 | `gpt-5.4` 또는 `gpt-5.5` |
| 최고 정밀도가 필요하고 비용 제약이 작은 작업 | Pro 계열 |

초기 API 연결 테스트와 일반 비용 민감 워크로드에는 `gpt-5.4-nano`가 적절하다.

---

# 12. 최종 정리

- Usage Tier는 rate limit과 사용 한도를 결정하며 토큰 단가 자체를 할인하지 않는다.
- Standard가 기본 가격이며 Batch와 Flex는 대체로 Standard의 50% 수준이다.
- Priority는 더 비싸지만 우선 처리 목적이다.
- 최신 5.4 계열 최저가는 `gpt-5.4-nano`다.
- 절대 최저가만 보면 이전 세대 `gpt-5-nano`가 더 저렴하다.
- `gpt-5.4-mini`는 비용과 성능의 균형형 후보다.
- 272K input을 넘는 1.05M-context 모델은 long-context 과금에 주의해야 한다.
- Cached input은 일반 input과 분리해 계산해야 한다.
- Reasoning tokens는 `output_tokens`에 포함되므로 중복 합산하면 안 된다.
- Web search, File search, Code Interpreter 등 built-in tool 비용은 모델 토큰 비용과 별도로 추가될 수 있다.
