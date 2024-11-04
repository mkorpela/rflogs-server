import secrets
from typing import Dict, Optional

import httpx
import jwt
from fastapi import HTTPException

from ..logging_config import get_logger
from ..models import Workspace
from .providers import AuthProvider

logger = get_logger(__name__)


class OIDCConfig:
    """OIDC provider configuration."""

    def __init__(self, workspace: Workspace):
        if not workspace.oidc_enabled:
            raise ValueError("OIDC is not enabled for this workspace")
        if not workspace.oidc_provider_url:
            raise ValueError("OIDC provider URL is not set")
        if not workspace.oidc_client_id:
            raise ValueError("OIDC client ID is not set")
        if not workspace.oidc_client_secret:
            raise ValueError("OIDC client secret is not set")

        self.provider_url = workspace.oidc_provider_url
        self.client_id = workspace.oidc_client_id
        self.client_secret = workspace.oidc_client_secret
        self.issuer_url = workspace.oidc_issuer_url


class OIDCProvider(AuthProvider):
    def __init__(self, config: OIDCConfig):
        self.config = config
        self._well_known_config: Optional[Dict] = None

    async def _get_well_known_config(self) -> Dict:
        """Fetch and cache OIDC provider configuration."""
        if self._well_known_config is None:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.config.provider_url}/.well-known/openid-configuration"
                )
                if response.status_code != 200:
                    raise HTTPException(
                        status_code=500, detail="Failed to fetch OIDC configuration"
                    )
                self._well_known_config = response.json()
        return self._well_known_config

    async def _get_jwks(self) -> Dict:
        """Fetch JSON Web Key Set from provider."""
        config = await self._get_well_known_config()
        jwks_uri = config["jwks_uri"]

        async with httpx.AsyncClient() as client:
            response = await client.get(jwks_uri)
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to fetch JWKS")
            return response.json()

    async def authenticate(self, token: str) -> Optional[Dict[str, str]]:
        """Authenticate a user's JWT token."""
        try:
            jwks = await self._get_jwks()
            jwks_client = jwt.PyJWKClient(
                self.config.provider_url + "/.well-known/jwks.json"
            )
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.config.client_id,
                issuer=self.config.issuer_url,
            )

            return {
                "sub": payload["sub"],
                "email": payload.get("email"),
                "username": payload.get("preferred_username", payload.get("sub")),
            }
        except jwt.InvalidTokenError as e:
            logger.error(f"Token validation failed: {e}")
            return None

    async def get_login_url(self, redirect_uri: str, state: str) -> str:
        """Get the OIDC login URL with appropriate parameters."""
        config = await self._get_well_known_config()
        auth_endpoint = config["authorization_endpoint"]

        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "nonce": secrets.token_urlsafe(16),
        }

        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{auth_endpoint}?{query}"

    async def verify_callback(
        self, code: str, state: str, redirect_uri: str
    ) -> Dict[str, str]:
        """Handle OIDC callback and token exchange."""
        config = await self._get_well_known_config()
        token_endpoint = config["token_endpoint"]

        async with httpx.AsyncClient() as client:
            # Exchange code for tokens
            token_response = await client.post(
                token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if token_response.status_code != 200:
                logger.error(
                    "Token exchange failed",
                    status=token_response.status_code,
                    content=token_response.text,
                )
                raise HTTPException(
                    status_code=400, detail="Failed to exchange code for token"
                )

            tokens = token_response.json()

            # Verify ID token
            user_info = await self.authenticate(tokens["id_token"])
            if not user_info:
                raise HTTPException(status_code=401, detail="Invalid ID token")

            return user_info


def create_auth_provider(workspace: Workspace) -> AuthProvider:
    """Factory function to create appropriate auth provider."""
    if workspace.oidc_enabled:
        return OIDCProvider(OIDCConfig(workspace))
    raise ValueError("No supported auth provider configured")
