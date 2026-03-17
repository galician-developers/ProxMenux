"""OpenRouter provider implementation.

OpenRouter is an aggregator that provides access to 100+ AI models
using a single API key. Maximum flexibility for choosing models.
Uses OpenAI-compatible API format.
"""
from typing import Optional
from .base import AIProvider, AIProviderError


class OpenRouterProvider(AIProvider):
    """OpenRouter provider for multi-model access."""
    
    NAME = "openrouter"
    DEFAULT_MODEL = "meta-llama/llama-3.3-70b-instruct"
    REQUIRES_API_KEY = True
    API_URL = "https://openrouter.ai/api/v1/chat/completions"
    
    def generate(self, system_prompt: str, user_message: str,
                 max_tokens: int = 200) -> Optional[str]:
        """Generate a response using OpenRouter's API.
        
        OpenRouter uses OpenAI-compatible format with additional
        headers for app identification.
        
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
            raise AIProviderError("API key required for OpenRouter")
        
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
            'HTTP-Referer': 'https://github.com/MacRimi/ProxMenux',
            'X-Title': 'ProxMenux Monitor',
        }
        
        result = self._make_request(self.API_URL, payload, headers)
        
        try:
            return result['choices'][0]['message']['content'].strip()
        except (KeyError, IndexError) as e:
            raise AIProviderError(f"Unexpected response format: {e}")
