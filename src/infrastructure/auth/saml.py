import base64
import re

from fastapi import Request
from onelogin.saml2.auth import OneLogin_Saml2_Auth

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
            # NAVER WORKS Identity Provider 정보에서 Response Issuer 복사/붙여넣기
            "entityId": "https://auth.worksmobile.com/saml2/oseop.by-works.net",
            "singleSignOnService": {
                # NAVER WORKS Identity Provider 정보에서 SSO URL 복사/붙여넣기
                "url": "https://auth.worksmobile.com/saml2/idp/oseop.by-works.net",
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            # NAVER WORKS Identity Provider 정보에서 Certificate 다운로드 후
            # 내용을 한 줄로 복사/붙여넣기
            "x509cert": "MIIC6jCCAdKgAwIBAgIIX0B8jtiXKtYwDQYJKoZIhvcNAQELBQAwNTETMBEGA1UEAwwKTElORSBXT1JLUzERMA8GA1UEBwwIU0VPTkdOQU0xCzAJBgNVBAYTAktSMB4XDTI2MDUzMDEyMzc0N1oXDTMxMDUzMDEyMzc0N1owNTETMBEGA1UEAwwKTElORSBXT1JLUzERMA8GA1UEBwwIU0VPTkdOQU0xCzAJBgNVBAYTAktSMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAvnAOMLaXoeEto9PryxQVpqt0FrlWpTbIQDcCenCbI70F2HMkAh5twBCPY6Mv5NLNYFDJF4NselJcMYCFwtF2otUGLINzUXtAVrksRMsvIjHmh7ldQRvyK7k/WJLHdSX3qEyJre6sdvlWWshA+nX51vS2x5XR8r/KXYN6OTKgtTyBYaRvPO58hNUvXC8ZY0sss2zWdiFweuprkxI6wF8TQDSKWf02vi26nRNMsfcigK12QRNcni1sVPUEdiDbxfhBON0GgInXeVU+Oqd6cMC8bjnHaA7o6loVGlk17V+2l2cidZEhI7bkJAoY7yxKjDJERB1fZ45TipTyrtz6rhSbrwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQBljuK68BXDAg/SrP8cmgK0Rlwh0nYg21M/7pNj5T+bDDuyWZzsw9djWmIlXjzr8kALiz+miUtDRIBNoyANi68Ed1NlIDXa4yP++IoJDdMeAF9YScPsQgEX23+CY1sxHKhoTUuznvFdAqmkSJ/uhJMXkMliZtQdShRQcK3pVoZ9NYTIy2GXIlXN9W17rAd/EfR0DV4AFUYbvanrUPrRLcqn3LEn1414W6AQBk0atCL4Y3ZYZQfyeIwCf2oqBqHmfQxkkSwaGjbP2FuczIGrFvXb7BI311feFQyPN5BASWMtPjiTtL+Kgf8AjTho/Xrw9wRsrX5jD10+EmiFp16eBaED",  # noqa: E501
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
