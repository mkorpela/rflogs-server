from .logging_config import get_logger
import httpx
import jwt
from fastapi import HTTPException

from .models import Workspace
import secrets
from typing import Dict, Any, Tuple, cast

logger = get_logger(__name__)


async def get_oidc_config(provider_url: str) -> Dict[str, Any]:
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{provider_url}/.well-known/openid-configuration")
        if response.status_code != 200:
            raise HTTPException(
                status_code=500, detail="Failed to fetch OIDC configuration"
            )
        return cast(Dict[str, Any], response.json())


async def get_jwks(jwks_uri: str) -> Dict[str, Any]:
    async with httpx.AsyncClient() as client:
        response = await client.get(jwks_uri)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to fetch JWKS")
        return cast(Dict[str, Any], response.json())


def create_oidc_login_url(
    workspace: Workspace, redirect_uri: str
) -> Tuple[str, str, str]:
    if (
        not workspace.oidc_enabled
        or not workspace.oidc_provider_url
        or not workspace.oidc_client_id
    ):
        raise ValueError("OIDC is not properly configured for this workspace")

    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(16)

    params = {
        "response_type": "code",
        "client_id": workspace.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
    }

    if workspace.oidc_provider_url:
        oidc_config = httpx.get(
            f"{workspace.oidc_provider_url}/.well-known/openid-configuration"
        ).json()
        authorization_endpoint = oidc_config["authorization_endpoint"]
        return (
            f"{authorization_endpoint}?{'&'.join(f'{k}={v}' for k, v in params.items())}",
            state,
            nonce,
        )
    else:
        raise ValueError("OIDC provider URL is not set")


async def exchange_code_for_token(
    workspace: Workspace, code: str, redirect_uri: str
) -> Dict[str, Any]:
    if not workspace.oidc_provider_url:
        raise ValueError("OIDC provider URL is not set")

    logger.info(
        f"Fetching OIDC configuration from: {workspace.oidc_provider_url}/.well-known/openid-configuration"
    )
    async with httpx.AsyncClient() as client:
        config_response = await client.get(
            f"{workspace.oidc_provider_url}/.well-known/openid-configuration"
        )
        if config_response.status_code != 200:
            logger.error(
                f"Failed to fetch OIDC configuration. Status: {config_response.status_code}, Content: {config_response.text}"
            )
            raise HTTPException(
                status_code=500, detail="Failed to fetch OIDC configuration"
            )

        oidc_config = config_response.json()
        token_endpoint = oidc_config.get("token_endpoint")
        if not token_endpoint:
            logger.error("Token endpoint not found in OIDC configuration")
            raise HTTPException(
                status_code=500, detail="Token endpoint not found in OIDC configuration"
            )

        logger.info(f"Exchanging code for token at: {token_endpoint}")
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": workspace.oidc_client_id,
            "client_secret": workspace.oidc_client_secret,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
        }
        token_response = await client.post(token_endpoint, data=data, headers=headers)

        if token_response.status_code != 200:
            logger.error(
                f"Failed to exchange code for token. Status: {token_response.status_code}, Content: {token_response.text}"
            )
            raise HTTPException(
                status_code=400, detail="Failed to exchange code for token"
            )

        result: Dict[str, Any] = token_response.json()
        return result


async def verify_oidc_token(
    token: str, workspace: Workspace, expected_nonce: str
) -> Dict[str, Any]:
    if not workspace.oidc_enabled:
        raise HTTPException(
            status_code=400, detail="OIDC is not enabled for this workspace"
        )

    if not workspace.oidc_provider_url:
        raise ValueError("OIDC provider URL is not set")

    try:
        jwks_url = f"{workspace.oidc_provider_url}/.well-known/jwks.json"
        logger.info("Fetching JWKS", jwks_url=jwks_url)
        jwks_client = jwt.PyJWKClient(jwks_url)

        logger.info("Attempting to get signing key from JWT")
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        logger.info("Decoding and verifying the token")
        payload: Dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=workspace.oidc_client_id,
            issuer=workspace.oidc_issuer_url,
            options={"verify_aud": True, "verify_iss": True},
        )

        if payload.get("nonce") != expected_nonce:
            logger.info(
                "Nonce invalid", expected=expected_nonce, nonce=payload.get("nonce")
            )
            raise HTTPException(status_code=401, detail="Invalid nonce in OIDC token")

        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="OIDC token has expired")
    except jwt.InvalidIssuerError:
        raise HTTPException(status_code=401, detail="Invalid issuer in OIDC token")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Invalid OIDC token: {str(e)}")
    except Exception as e:
        logger.exception("Error verifying OIDC token")
        raise HTTPException(
            status_code=500, detail=f"Error verifying OIDC token: {str(e)}"
        )


async def fetch_oidc_issuer(oidc_provider_url: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{oidc_provider_url}/.well-known/openid-configuration"
        )
        if response.status_code != 200:
            raise HTTPException(
                status_code=500, detail="Failed to fetch OIDC configuration"
            )

        oidc_config = response.json()
        issuer_url: str = oidc_config.get("issuer")
        if not issuer_url:
            raise ValueError("Issuer URL not found in OIDC configuration")
        return issuer_url
