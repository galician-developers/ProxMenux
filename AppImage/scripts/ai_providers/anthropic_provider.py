"""Anthropic (Claude) provider implementation.

Anthropic's Claude models are excellent for text generation and translation.
Claude 3.5 Haiku is fast and affordable for notification enhancement.
"""
from typing import Optional
from .base import AIProvider, AIProviderError


class AnthropicProvider(AIProvider):
    """Anthropic provider using their Messages API."""
    
    NAME = "anthropic"
    DEFAULT_MODEL = "claude-3-5-haiku-latest"
    REQUIRES_API_KEY = True
    API_URL = "https://api.anthropic.com/v1/messages"
    API_VERSION = "2023-06-01"
    
    def generate(self, system_prompt: str, user_message: str,
                 max_tokens: int = 200) -> Optional[str]:
        """Generate a response using Anthropic's API.
        
        Note: Anthropic uses a different API format than OpenAI.
        The system prompt goes in a separate field, not in messages.
        
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
            raise AIProviderError("API key required for Anthropic")
        
        # Anthropic uses a different format - system is a top-level field
        payload = {
            'model': self.model,
            'system': system_prompt,
            'messages': [
                {'role': 'user', 'content': user_message},
            ],
            'max_tokens': max_tokens,
        }
        
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': self.api_key,
            'anthropic-version': self.API_VERSION,
        }
        
        result = self._make_request(self.API_URL, payload, headers)
        
        try:
            # Anthropic returns content as array of content blocks
            content = result['content']
            if isinstance(content, list) and len(content) > 0:
                return content[0].get('text', '').strip()
            return str(content).strip()
        except (KeyError, IndexError) as e:
            raise AIProviderError(f"Unexpected response format: {e}")
