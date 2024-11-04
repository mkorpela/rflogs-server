from abc import ABC, abstractmethod
from typing import Dict, Optional

class AuthProvider(ABC):
    """Abstract base class for authentication providers."""
    
    @abstractmethod
    async def authenticate(self, token: str) -> Optional[Dict[str, str]]:
        """Authenticate a user token and return user info if valid."""
        pass

    @abstractmethod
    async def get_login_url(self, redirect_uri: str, state: str) -> str:
        """Get the URL to redirect users for login."""
        pass

    @abstractmethod
    async def verify_callback(self, code: str, state: str, redirect_uri: str) -> Dict[str, str]:
        """Verify the callback from auth provider and return user info."""
        pass