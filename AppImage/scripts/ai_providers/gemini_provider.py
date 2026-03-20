"""Google Gemini provider implementation.

Google's Gemini models offer a free tier and excellent quality/price ratio.
Gemini 2.0 Flash is fast and cost-effective with improved capabilities.
"""
from typing import Optional
from .base import AIProvider, AIProviderError


class GeminiProvider(AIProvider):
    """Google Gemini provider using the Generative Language API."""
    
    NAME = "gemini"
    DEFAULT_MODEL = "gemini-2.0-flash"
    REQUIRES_API_KEY = True
    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    
    def generate(self, system_prompt: str, user_message: str,
                 max_tokens: int = 200) -> Optional[str]:
        """Generate a response using Google's Gemini API.
        
        Note: Gemini uses a different API format. System instructions
        go in a separate systemInstruction field.
        
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
            raise AIProviderError("API key required for Gemini")
        
        url = f"{self.API_BASE}/{self.model}:generateContent?key={self.api_key}"
        
        # Gemini uses a specific format with contents array
        payload = {
            'systemInstruction': {
                'parts': [{'text': system_prompt}]
            },
            'contents': [
                {
                    'role': 'user',
                    'parts': [{'text': user_message}]
                }
            ],
            'generationConfig': {
                'maxOutputTokens': max_tokens,
                'temperature': 0.3,
            }
        }
        
        headers = {
            'Content-Type': 'application/json',
        }
        
        result = self._make_request(url, payload, headers)
        
        try:
            # Gemini returns candidates array with content parts
            candidates = result.get('candidates', [])
            if candidates:
                content = candidates[0].get('content', {})
                parts = content.get('parts', [])
                if parts:
                    return parts[0].get('text', '').strip()
            raise AIProviderError("No content in response")
        except (KeyError, IndexError) as e:
            raise AIProviderError(f"Unexpected response format: {e}")
