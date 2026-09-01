import base64
import re

from fastapi import Request
from onelogin.saml2.auth import OneLogin_Saml2_Auth
from onelogin.saml2.response import OneLogin_Saml2_Response

from src.core import app_settings
from src.core.config import settings


def get_saml_settings(base_url: str) -> dict:
    return {
        "strict": True,
        "debug": False,
        "sp": {
            # 네이버웍스 Developer Console/SSO/WORKS as IdP에서 SP Issuer에 등록
            "entityId": f"{base_url}/api/v1/auth/saml/metadata",
            "assertionConsumerService": {
                "url": f"{base_url}/api/v1/auth/saml/acs",  # ACS URL에 등록
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
        },
        "idp": {
            # 관리자 설정(app_settings) → .env → 코드 기본값 순으로 읽는다.
            # 하드코딩이던 시절엔 IdP를 바꿀 때마다 재배포가 필요했다(2026-08-10).
            "entityId": app_settings.get_str("saml_idp_entity_id"),
            "singleSignOnService": {
                "url": app_settings.get_str("saml_idp_sso_url"),
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": app_settings.get_str("saml_idp_x509cert"),
        },
        "security": {
            # 네이버웍스(LINE WORKS)는 Response 전체에만 서명(어설션 개별서명 안 함)하고
            # AttributeStatement 없이 NameID만 보낸다. Response 서명은 계속 검증하되
            # 어설션 개별서명·속성문 요구는 끈다(안 그러면 정상 응답을 거부).
            "wantMessagesSigned": True,
            "wantAssertionsSigned": False,
            "wantAttributeStatement": False,
        },
    }


async def prepare_fastapi_saml_request(request: Request) -> dict:
    headers = request.headers

    prod_base_url = settings.saml_base_url
    if prod_base_url:
        host = prod_base_url.split("://")[-1]
        proto = "https"
    else:
        host = headers.get("x-forwarded-host", headers.get("host", "localhost:8000"))
        proto = headers.get("x-forwarded-proto", "http")

    port = "443" if proto == "https" else "80"
    if ":" in host:
        host, port = host.split(":")

    post_data = {}
    if request.method == "POST":
        form_data = await request.form()
        post_data = {k: v for k, v in form_data.items()}

    return {
        "https": "on" if proto == "https" else "off",
        "http_host": host,
        "script_name": request.url.path,
        "server_port": port,
        "get_data": dict(request.query_params),
        "post_data": post_data,
    }


async def init_saml_auth(request: Request, base_url: str) -> OneLogin_Saml2_Auth:
    req_data = await prepare_fastapi_saml_request(request)
    is_local = settings.is_local
    saml_settings = get_saml_settings(base_url)

    if is_local:
        if req_data.get("post_data") and req_data["post_data"].get("SAMLResponse"):
            try:
                saml_b64 = req_data["post_data"]["SAMLResponse"]
                decoded_xml = base64.b64decode(saml_b64).decode("utf-8")
                cleaned_xml = re.sub(
                    r"<Signature[^>]*>.*?</Signature>",
                    "",
                    decoded_xml,
                    flags=re.DOTALL,
                )
                req_data["post_data"]["SAMLResponse"] = base64.b64encode(
                    cleaned_xml.encode("utf-8")
                ).decode("utf-8")
            except Exception:
                pass

        saml_settings["strict"] = False
        saml_settings["debug"] = True
        saml_settings["security"] = {
            "wantMessagesSigned": False,
            "wantAssertionsSigned": False,
        }

    return OneLogin_Saml2_Auth(req_data, saml_settings)


# --- NaverWorks(LINE WORKS) SAML 호환 패치 ------------------------------------
# 네이버웍스는 SAML Response 전체에 enveloped 서명을 걸고 Reference URI="" 를 쓴다.
# python3-saml의 _query_assertion은 '메시지 서명 참조 URI'로 어설션 위치를 찾는데,
# URI="" 이면 tagid="" 가 되어 /samlp:Response[@ID=''] 가 아무 노드도 매칭하지 못하고
# 어설션을 못 찾아 "The Assertion must include a Conditions element" 로 오판한다.
# 빈 URI(=문서 전체 서명)일 때는 ID 필터 없이 위치로 어설션을 찾도록 교정한다.
# 전체 Response 가 서명돼 있으므로 signature-wrapping 위험은 없다.
def _nw_query_assertion(self, xpath_expr):
    assertion_expr = "/saml:Assertion"
    signature_expr = "/ds:Signature/ds:SignedInfo/ds:Reference"
    signed_assertion_query = "/samlp:Response" + assertion_expr + signature_expr
    assertion_reference_nodes = self._query(signed_assertion_query)
    tagid = None
    if not assertion_reference_nodes:
        signed_message_query = "/samlp:Response" + signature_expr
        message_reference_nodes = self._query(signed_message_query)
        if message_reference_nodes:
            message_id = message_reference_nodes[0].get("URI")
            if message_id:
                final_query = "/samlp:Response[@ID=$tagid]/"
                tagid = message_id[1:]
            else:
                final_query = "/samlp:Response"
        else:
            final_query = "/samlp:Response"
        final_query += assertion_expr
    else:
        assertion_id = assertion_reference_nodes[0].get("URI")
        final_query = "/samlp:Response" + assertion_expr + "[@ID=$tagid]"
        tagid = assertion_id[1:]
    final_query += xpath_expr
    return self._query(final_query, tagid)


OneLogin_Saml2_Response._query_assertion = _nw_query_assertion
