"""웹 리서치 출처 병합·신뢰도 판정 단위 테스트.

신뢰도는 LLM 루브릭 판정(매니페스트)이 기본이지만, 신뢰 도메인
(go.kr·ac.kr·re.kr·gov·edu)은 결정적으로 high로 오버라이드된다.
"""

from src.clients.llm.base import WebSource
from src.services.research.web_research import _domain_reliability, _merge_sources


class TestDomainReliability:
    def test_trusted_korean_domains_are_high(self):
        assert _domain_reliability("https://kostat.go.kr/board/stat") == "high"
        assert _domain_reliability("https://www.snu.ac.kr/research") == "high"
        assert _domain_reliability("https://www.kdi.re.kr/report/1") == "high"

    def test_foreign_gov_edu_are_high(self):
        assert _domain_reliability("https://www.census.gov/data") == "high"
        assert _domain_reliability("https://web.mit.edu/paper") == "high"

    def test_other_domains_keep_llm_judgement(self):
        assert _domain_reliability("https://news.example.co.kr/article") is None
        assert _domain_reliability("https://blog.naver.com/post/1") is None
        # or.kr은 사설 단체도 쓸 수 있어 오버라이드하지 않는다
        assert _domain_reliability("https://someassoc.or.kr/notice") is None

    def test_suffix_must_match_domain_boundary(self):
        # 'go.kr'로 끝나는 척하는 도메인(예: fakego.kr)은 걸리지 않는다
        assert _domain_reliability("https://fakego.kr/page") is None
        assert _domain_reliability("not a url") is None


class TestMergeSourcesReliability:
    def test_trusted_domain_overrides_llm_low(self):
        """LLM이 low로 판정해도 정부 도메인이면 high로 강제된다."""
        ws = WebSource(url="https://data.go.kr/dataset/1", title="공공데이터", content_md="본문")
        manifest = {
            "sources": [
                {"url": "https://data.go.kr/dataset/1", "title": "공공데이터", "reliability": "low"}
            ]
        }
        merged = _merge_sources([ws], manifest, outline=[])
        assert merged[0].reliability == "high"

    def test_untrusted_domain_keeps_manifest_value(self):
        ws = WebSource(url="https://media.example.com/a", title="기사", content_md="본문")
        manifest = {
            "sources": [
                {"url": "https://media.example.com/a", "title": "기사", "reliability": "medium"}
            ]
        }
        merged = _merge_sources([ws], manifest, outline=[])
        assert merged[0].reliability == "medium"

    def test_fetched_only_source_gets_domain_reliability(self):
        """매니페스트에 없지만 본문을 회수한 출처도 신뢰 도메인이면 high가 붙는다."""
        ws = WebSource(url="https://www.moel.go.kr/policy", title="정책", content_md="본문")
        merged = _merge_sources([ws], {"sources": []}, outline=[])
        assert merged[0].reliability == "high"
