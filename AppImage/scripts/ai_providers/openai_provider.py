"""OpenAI provider implementation.

OpenAI is the industry standard for AI APIs. gpt-4o-mini provides
excellent quality at a reasonable price point.
"""
from typing import Optional
from .base import AIProvider, AIProviderError


class OpenAIProvider(AIProvider):
    """OpenAI provider using their Chat Completions API.
    
    Also compatible with OpenAI-compatible APIs like:
    - BytePlus/ByteDance (Kimi K2.5)
    - LocalAI
    - LM Studio
    - vLLM
    - Together AI
    - Any OpenAI-compatible endpoint
    """
    
    NAME = "openai"
    DEFAULT_MODEL = "gpt-4o-mini"
    REQUIRES_API_KEY = True
    DEFAULT_API_URL = "https://api.openai.com/v1/chat/completions"
    
    def _get_api_url(self) -> str:
        """Get the API URL, using custom base_url if provided."""
        if self.base_url:
            # Ensure the URL ends with the correct path
            base = self.base_url.rstrip('/')
            if not base.endswith('/chat/completions'):
                if not base.endswith('/v1'):
                    base = f"{base}/v1"
                base = f"{base}/chat/completions"
            return base
        return self.DEFAULT_API_URL
    
    def generate(self, system_prompt: str, user_message: str,
                 max_tokens: int = 200) -> Optional[str]:
        """Generate a response using OpenAI's API or compatible endpoint.
        
        Args:
            system_prompt: System instructions
            user_message: User message to process
            max_tokens: Maximum response length
            
        Returns:
            Generated text or None if failed
            
        Raises:
            AIProviderError: If API key is missing or request fails
        """
        if not self.api_key:
            raise AIProviderError("API key required for OpenAI")
        
        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message},
            ],
            'max_tokens': max_tokens,
            'temperature': 0.3,
        }
        
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_key}',
        }
        
        api_url = self._get_api_url()
        result = self._make_request(api_url, payload, headers)
        
        try:
            return result['choices'][0]['message']['content'].strip()
        except (KeyError, IndexError) as e:
            raise AIProviderError(f"Unexpected response format: {e}")
