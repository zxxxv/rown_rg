import base64
import re

from fastapi import Request
from onelogin.saml2.auth import OneLogin_Saml2_Auth

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
            "wantMessagesSigned": True,
            "wantAssertionsSigned": True,
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
