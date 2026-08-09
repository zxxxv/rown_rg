"""보고서 생성 서비스 — LLM router로 섹션 후보를 만든다 (판정은 services/qa 몫).

- candidates: 후보 N개(상수) 생성, [번호] 인용 마커 → chunk_id 매핑.
"""

from src.services.generation.candidates import generate_section_candidates

__all__ = ["generate_section_candidates"]
