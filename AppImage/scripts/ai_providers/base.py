"""Base class for AI providers."""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class AIProviderError(Exception):
    """Exception for AI provider errors."""
    pass


class AIProvider(ABC):
    """Abstract base class for AI providers.
    
    All provider implementations must inherit from this class and implement
    the generate() method.
    """
    
    # Provider metadata (override in subclasses)
    NAME = "base"
    DEFAULT_MODEL = ""
    REQUIRES_API_KEY = True
    
    def __init__(self, api_key: str = "", model: str = "", base_url: str = ""):
        """Initialize the AI provider.
        
        Args:
            api_key: API key for authentication (not required for local providers)
            model: Model name to use (defaults to DEFAULT_MODEL if empty)
            base_url: Base URL for API calls (used by Ollama and custom endpoints)
        """
        self.api_key = api_key
        self.model = model or self.DEFAULT_MODEL
        self.base_url = base_url
    
    @abstractmethod
    def generate(self, system_prompt: str, user_message: str, 
                 max_tokens: int = 200) -> Optional[str]:
        """Generate a response from the AI model.
        
        Args:
            system_prompt: System instructions for the model
            user_message: User message/query to process
            max_tokens: Maximum tokens in the response
            
        Returns:
            Generated text or None if failed
            
        Raises:
            AIProviderError: If there's an error communicating with the provider
        """
        pass
    
    def test_connection(self) -> Dict[str, Any]:
        """Test the connection to the AI provider.
        
        Sends a simple test message to verify the provider is accessible
        and the API key is valid.
        
        Returns:
            Dictionary with:
                - success: bool indicating if connection succeeded
                - message: Human-readable status message
                - model: Model name being used
        """
        try:
            response = self.generate(
                system_prompt="You are a test assistant. Respond with exactly: CONNECTION_OK",
                user_message="Test connection",
                max_tokens=20
            )
            if response:
                # Check if response contains our expected text
                if "CONNECTION_OK" in response.upper() or "CONNECTION" in response.upper():
                    return {
                        'success': True,
                        'message': 'Connection successful',
                        'model': self.model
                    }
                # Even if different response, connection worked
                return {
                    'success': True,
                    'message': f'Connected (response received)',
                    'model': self.model
                }
            return {
                'success': False,
                'message': 'No response received from provider',
                'model': self.model
            }
        except AIProviderError as e:
            return {
                'success': False,
                'message': str(e),
                'model': self.model
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Unexpected error: {str(e)}',
                'model': self.model
            }
    
    def _make_request(self, url: str, payload: dict, headers: dict, 
                      timeout: int = 15) -> dict:
        """Make HTTP request to AI provider API.
        
        Args:
            url: API endpoint URL
            payload: JSON payload to send
            headers: HTTP headers
            timeout: Request timeout in seconds
            
        Returns:
            Parsed JSON response
            
        Raises:
            AIProviderError: If request fails
        """
        import json
        import urllib.request
        import urllib.error
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode('utf-8')
            except Exception:
                pass
            raise AIProviderError(f"HTTP {e.code}: {error_body or e.reason}")
        except urllib.error.URLError as e:
            raise AIProviderError(f"Connection error: {e.reason}")
        except json.JSONDecodeError as e:
            raise AIProviderError(f"Invalid JSON response: {e}")
        except Exception as e:
            raise AIProviderError(f"Request failed: {str(e)}")
