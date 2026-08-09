"""gemini_adapter.py 경유 api호출 테스트"""

import asyncio

from src.clients.llm.base import CompletionRequest, Message
from src.clients.llm.factory import create_llm_client
from src.clients.llm.models import default_id


async def main() -> None:
    client = create_llm_client(mode="live")

    request = CompletionRequest(
        model=default_id("gemini"),  # 카탈로그 기본 모델 = gemini-2.5-flash-lite
        messages=[
            Message(
                role="user",
                content="Gemini 기본 모델 연결 테스트",
            )
        ],
        max_tokens=512,
        temperature=0.2,
    )

    print("선택된 모델:", request.model)

    response = await client.complete(request)

    print("=== GeminiAdapter 응답 ===")
    print(response.content)

    print("\n=== Token usage ===")
    print("model:", response.model)
    print("input_tokens:", response.input_tokens)
    print("output_tokens:", response.output_tokens)
    print("cached_input_tokens:", response.cached_input_tokens)
    print("stop_reason:", response.stop_reason)


if __name__ == "__main__":
    asyncio.run(main())
