import asyncio

from src.clients.base import Message
from src.clients.llm_factory import create_llm_client
from src.clients.openai_models import OpenAICompletionRequest


async def main() -> None:
    client = create_llm_client(mode="live")

    request = OpenAICompletionRequest(
        messages=[
            Message(
                role="user",
                content="OpenAI 기본 모델 연결 테스트입니다. 짧게 응답해 주세요.",
            )
        ],
        max_tokens=256,
        temperature=0.2,
    )

    print("선택된 모델:", request.model)

    response = await client.complete(request)

    print("\n=== OpenAIAdapter 응답 ===")
    print(response.content)

    print("\n=== Token usage ===")
    print("model:", response.model)
    print("input_tokens:", response.input_tokens)
    print("output_tokens:", response.output_tokens)
    print("cached_input_tokens:", response.cached_input_tokens)
    print("stop_reason:", response.stop_reason)


if __name__ == "__main__":
    asyncio.run(main())
