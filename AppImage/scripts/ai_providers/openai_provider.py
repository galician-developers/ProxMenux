"""OpenAI provider implementation.

OpenAI is the industry standard for AI APIs. gpt-4o-mini provides
excellent quality at a reasonable price point.
"""
from typing import Optional
from .base import AIProvider, AIProviderError


class OpenAIProvider(AIProvider):
    """OpenAI provider using their Chat Completions API."""
    
    NAME = "openai"
    DEFAULT_MODEL = "gpt-4o-mini"
    REQUIRES_API_KEY = True
    API_URL = "https://api.openai.com/v1/chat/completions"
    
    def generate(self, system_prompt: str, user_message: str,
                 max_tokens: int = 200) -> Optional[str]:
        """Generate a response using OpenAI's API.
        
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
        
        result = self._make_request(self.API_URL, payload, headers)
        
        try:
            return result['choices'][0]['message']['content'].strip()
        except (KeyError, IndexError) as e:
            raise AIProviderError(f"Unexpected response format: {e}")
