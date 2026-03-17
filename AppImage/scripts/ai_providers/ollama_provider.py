"""Ollama provider implementation.

Ollama enables 100% local AI execution with no costs and complete privacy.
No internet connection required - perfect for sensitive enterprise environments.
"""
from typing import Optional
from .base import AIProvider, AIProviderError


class OllamaProvider(AIProvider):
    """Ollama provider for local AI execution."""
    
    NAME = "ollama"
    DEFAULT_MODEL = "llama3.2"
    REQUIRES_API_KEY = False
    DEFAULT_URL = "http://localhost:11434"
    
    def __init__(self, api_key: str = "", model: str = "", base_url: str = ""):
        """Initialize Ollama provider.
        
        Args:
            api_key: Not used for Ollama (local execution)
            model: Model name (default: llama3.2)
            base_url: Ollama server URL (default: http://localhost:11434)
        """
        super().__init__(api_key, model, base_url)
        # Use default URL if not provided
        if not self.base_url:
            self.base_url = self.DEFAULT_URL
    
    def generate(self, system_prompt: str, user_message: str,
                 max_tokens: int = 200) -> Optional[str]:
        """Generate a response using local Ollama server.
        
        Args:
            system_prompt: System instructions
            user_message: User message to process
            max_tokens: Maximum response length (maps to num_predict)
            
        Returns:
            Generated text or None if failed
            
        Raises:
            AIProviderError: If Ollama server is unreachable
        """
        url = f"{self.base_url.rstrip('/')}/api/chat"
        
        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            'stream': False,
            'options': {
                'num_predict': max_tokens,
                'temperature': 0.3,
            }
        }
        
        headers = {
            'Content-Type': 'application/json',
        }
        
        try:
            result = self._make_request(url, payload, headers, timeout=30)
        except AIProviderError as e:
            if "Connection" in str(e) or "refused" in str(e).lower():
                raise AIProviderError(
                    f"Cannot connect to Ollama at {self.base_url}. "
                    "Make sure Ollama is running (ollama serve)"
                )
            raise
        
        try:
            message = result.get('message', {})
            return message.get('content', '').strip()
        except (KeyError, AttributeError) as e:
            raise AIProviderError(f"Unexpected response format: {e}")
    
    def test_connection(self):
        """Test connection to Ollama server.
        
        Also checks if the specified model is available.
        """
        import json
        import urllib.request
        import urllib.error
        
        # First check if server is running
        try:
            url = f"{self.base_url.rstrip('/')}/api/tags"
            req = urllib.request.Request(url, method='GET')
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                models = [m.get('name', '').split(':')[0] for m in data.get('models', [])]
                
                if self.model not in models and f"{self.model}:latest" not in [m.get('name', '') for m in data.get('models', [])]:
                    return {
                        'success': False,
                        'message': f"Model '{self.model}' not found. Available: {', '.join(models[:5])}...",
                        'model': self.model
                    }
        except urllib.error.URLError:
            return {
                'success': False,
                'message': f"Cannot connect to Ollama at {self.base_url}. Make sure Ollama is running.",
                'model': self.model
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Error checking Ollama: {str(e)}",
                'model': self.model
            }
        
        # If server is up and model exists, do the actual test
        return super().test_connection()
