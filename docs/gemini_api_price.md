# Gemini API 2.x / 3.x 모델 가격 및 토큰 비용 정리



## 공식 출처

- [Gemini Developer API Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini API Models](https://ai.google.dev/gemini-api/docs/models)

---

## 1. 핵심 요약

Gemini API 가격을 볼 때는 두 가지 티어를 분리해서 이해해야 한다.

| 구분 | 의미 | 가격에 직접 영향 |
|---|---|---:|
| Usage Tier 1 / 2 / 3 | 프로젝트의 사용 한도, rate limit, billing cap을 정하는 등급 | 직접적인 토큰 단가 차이 없음 |
| 실행 티어: Standard / Batch / Flex / Priority | 같은 모델을 어떤 처리 방식으로 호출할지 정하는 가격 구조 | 있음 |

즉, **Tier 1/2/3가 올라간다고 1M 토큰 단가가 싸지는 구조가 아니다.**
실제 토큰 비용은 **모델별 가격 + 실행 티어(Standard, Batch, Flex, Priority)** 로 계산된다.

---

## 2. 실행 티어별 가격 구조

| 실행 티어 | 의미 | 가격 구조 | 적합한 용도 |
|---|---|---:|---|
| Standard | 기본 동기 호출 | 기준 가격 | 일반 챗봇, 서비스 기본 응답 |
| Batch | 비동기 대량 처리 | Standard 대비 약 50% | 대량 문서 요약, 데이터 라벨링, 야간 배치 작업 |
| Flex | 비용 최적화 동기 호출 | Standard 대비 약 50% | 즉시성은 조금 덜 중요하지만 비용을 줄이고 싶은 작업 |
| Priority | 우선 처리 | Standard보다 비쌈 | latency와 안정성이 중요한 프로덕션 작업 |

---

## 3. 이번 정리 대상 모델

### Gemini 3.x

| 모델 ID | 상태 | 용도 요약 |
|---|---|---|
| `gemini-3.5-flash` | Stable | 3.x 계열 기본 고성능 Flash 모델 |
| `gemini-3.1-flash-lite` | Stable | 3.x 계열 저비용/고효율 모델 |
| `gemini-3.1-pro-preview` | Preview | 고성능 추론, 코딩, 에이전트 작업 |

### Gemini 2.x

| 모델 ID | 상태 | 용도 요약 |
|---|---|---|
| `gemini-2.5-pro` | Stable | 복잡한 추론, 코딩, 장문 분석 |
| `gemini-2.5-flash` | Stable | 가격 대비 성능, 저지연, 대량 처리 |
| `gemini-2.5-flash-lite` | Stable | 최저비용/고속 처리 |

---

# 4. Gemini 3.x 가격표

단위는 **USD / 1M tokens**이다.
Output price에는 공식 문서 기준으로 **thinking tokens 포함**이다.

## 4-1. `gemini-3.5-flash`

| 실행 티어 | Input | Output |
|---|---:|---:|
| Standard | $1.50 | $9.00 |
| Batch | $0.75 | $4.50 |
| Flex | $0.75 | $4.50 |
| Priority | $2.70 | $16.20 |

### 특징

- 3.x 계열에서 일반 서비스 기본 모델로 쓰기 좋은 고성능 Flash 모델
- Standard 대비 Batch/Flex는 약 50% 수준
- Priority는 더 높은 단가

---

## 4-2. `gemini-3.1-flash-lite`

| 실행 티어 | Input | Output |
|---|---:|---:|
| Standard | $0.25 text/image/video<br>$0.50 audio | $1.50 |
| Batch | $0.125 text/image/video<br>$0.25 audio | $0.75 |
| Flex | $0.125 text/image/video<br>$0.25 audio | $0.75 |
| Priority | $0.45 text/image/video<br>$0.90 audio | $2.70 |

### 특징

- 3.x 계열의 저비용/고효율 모델
- 번역, 단순 데이터 처리, 고볼륨 agentic task에 적합
- `gemini-3.5-flash`보다 훨씬 저렴하지만, 고난도 추론은 Pro/Flash 고성능 모델이 유리할 수 있음

---

## 4-3. `gemini-3.1-pro-preview`

`gemini-3.1-pro-preview-customtools`도 같은 가격 구조다.

| 실행 티어 | Input | Output |
|---|---:|---:|
| Standard | $2.00 ≤200k tokens<br>$4.00 >200k tokens | $12.00 ≤200k tokens<br>$18.00 >200k tokens |
| Batch | $1.00 ≤200k tokens<br>$2.00 >200k tokens | $6.00 ≤200k tokens<br>$9.00 >200k tokens |
| Flex | $1.00 ≤200k tokens<br>$2.00 >200k tokens | $6.00 ≤200k tokens<br>$9.00 >200k tokens |
| Priority | $3.60 ≤200k tokens<br>$7.20 >200k tokens | $21.60 ≤200k tokens<br>$32.40 >200k tokens |

### 특징

- 3.x 계열의 Pro preview 모델
- 복잡한 추론, 코딩, 에이전트 워크플로우, tool use에 적합
- 프롬프트가 200k tokens 이하인지 초과인지에 따라 단가가 달라짐
- Preview 모델이므로 프로덕션 안정성 요구가 높다면 릴리즈 상태를 반드시 확인해야 함

---

## 4-4. `gemini-3-flash-preview`

| 실행 티어 | Input | Output |
|---|---:|---:|
| Standard | $0.50 text/image/video<br>$1.00 audio | $3.00 |
| Batch | $0.25 text/image/video<br>$0.50 audio | $1.50 |
| Flex | $0.25 text/image/video<br>$0.50 audio | $1.50 |
| Priority | $0.90 text/image/video<br>$1.80 audio | $5.40 |

### 특징

- Gemini 3 Flash preview 테스트용 모델
- `gemini-3.5-flash`보다 Standard 기준 input/output 단가가 낮음
- Preview 모델이므로 릴리즈 상태와 rate limit을 확인하고 사용하는 것이 좋음

---

# 5. Gemini 2.x 가격표

단위는 **USD / 1M tokens**이다.
Output price에는 공식 문서 기준으로 **thinking tokens 포함**이다.

## 5-1. `gemini-2.5-pro`

| 실행 티어 | Input | Output |
|---|---:|---:|
| Standard | $1.25 ≤200k tokens<br>$2.50 >200k tokens | $10.00 ≤200k tokens<br>$15.00 >200k tokens |
| Batch | $0.625 ≤200k tokens<br>$1.25 >200k tokens | $5.00 ≤200k tokens<br>$7.50 >200k tokens |
| Flex | $0.625 ≤200k tokens<br>$1.25 >200k tokens | $5.00 ≤200k tokens<br>$7.50 >200k tokens |
| Priority | $2.25 ≤200k tokens<br>$4.50 >200k tokens | $18.00 ≤200k tokens<br>$27.00 >200k tokens |

### 특징

- 2.x 계열의 고성능 Pro 모델
- 복잡한 reasoning, coding, 장문 분석에 적합
- 프롬프트가 200k tokens 이하인지 초과인지에 따라 단가가 달라짐

---

## 5-2. `gemini-2.5-flash`

| 실행 티어 | Input | Output |
|---|---:|---:|
| Standard | $0.30 text/image/video<br>$1.00 audio | $2.50 |
| Batch | $0.15 text/image/video<br>$0.50 audio | $1.25 |
| Flex | $0.15 text/image/video<br>$0.50 audio | $1.25 |
| Priority | $0.54 text/image/video<br>$1.80 audio | $4.50 |

### 특징

- 2.x 계열의 가격 대비 성능 모델
- 일반 챗봇, 저지연 API 응답, 대량 작업에 적합
- 비용과 성능의 균형이 좋아 기본값 후보로 보기 좋음

---

## 5-3. `gemini-2.5-flash-lite`

| 실행 티어 | Input | Output |
|---|---:|---:|
| Standard | $0.10 text/image/video<br>$0.30 audio | $0.40 |
| Batch | $0.05 text/image/video<br>$0.15 audio | $0.20 |
| Flex | $0.05 text/image/video<br>$0.15 audio | $0.20 |
| Priority | $0.18 text/image/video<br>$0.54 audio | $0.72 |

### 특징

- 2.5 계열에서 가장 저렴한 일반 텍스트 출력 모델
- 고볼륨 분류, 단순 추출, 간단한 요약, 비용 민감 작업에 적합
- 고난도 추론보다는 빠르고 저렴한 처리에 초점

---

# 6. 빠른 비교표

## 6-1. 용도별 추천

| 목적 | 추천 모델 |
|---|---|
| 가장 저렴한 처리 | `gemini-2.5-flash-lite` |
| 저렴하지만 3.x 최신 계열 사용 | `gemini-3.1-flash-lite` |
| 일반 챗봇/서비스 기본값 | `gemini-2.5-flash` 또는 `gemini-3.5-flash` |
| 최신 3.x 고성능 Flash | `gemini-3.5-flash` |
| 복잡한 추론/코딩/장문 분석 | `gemini-2.5-pro` |
| 최신 Pro preview 테스트 | `gemini-3.1-pro-preview` |
| 3.x preview 테스트 | `gemini-3-flash-preview` |

## 6-2. 계열별 포지셔닝

| 계열 | 저비용 | 균형형 | 고성능 |
|---|---|---|---|
| Gemini 3.x | `gemini-3.1-flash-lite` | `gemini-3.5-flash` / `gemini-3-flash-preview` | `gemini-3.1-pro-preview` |
| Gemini 2.x | `gemini-2.5-flash-lite` | `gemini-2.5-flash` | `gemini-2.5-pro` |

---

# 7. 비용 계산식

```text
총 비용 =
(input_tokens / 1,000,000 × input 단가)
+ (output_tokens / 1,000,000 × output 단가)
```

예시: `gemini-2.5-flash` Standard에서 input 10,000 tokens, output 2,000 tokens 사용 시

```text
input 비용  = 10,000 / 1,000,000 × $0.30 = $0.003
output 비용 =  2,000 / 1,000,000 × $2.50 = $0.005
총 비용 = $0.008
```

---

# 8. 정리

- **Usage Tier 1/2/3는 토큰 단가가 아니라 사용 한도/rate limit/billing cap의 차이**다.
- **실제 토큰 비용은 모델별 + 실행 티어별 가격으로 계산**한다.
- **Batch와 Flex는 대체로 Standard 대비 약 50% 비용 구조**라 비용 최적화에 유리하다.
- **Priority는 더 비싸지만 응답 안정성과 지연시간이 중요한 프로덕션 작업에 적합**하다.
- 비용 최우선이면 `gemini-2.5-flash-lite`, 최신 3.x 저비용 모델은 `gemini-3.1-flash-lite`, 일반 서비스 기본값은 `gemini-2.5-flash` 또는 `gemini-3.5-flash`, 고난도 작업은 `gemini-2.5-pro` 또는 `gemini-3.1-pro-preview`를 우선 검토하면 된다.
