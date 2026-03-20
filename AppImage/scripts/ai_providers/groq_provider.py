"""Groq AI provider implementation.

Groq provides fast inference with a generous free tier (30 requests/minute).
Uses the OpenAI-compatible API format.
"""
from typing import Optional, List
import json
import urllib.request
import urllib.error
from .base import AIProvider, AIProviderError


class GroqProvider(AIProvider):
    """Groq AI provider using their OpenAI-compatible API."""
    
    NAME = "groq"
    REQUIRES_API_KEY = True
    API_URL = "https://api.groq.com/openai/v1/chat/completions"
    MODELS_URL = "https://api.groq.com/openai/v1/models"
    
    def list_models(self) -> List[str]:
        """List available Groq models.
        
        Returns:
            List of model IDs available for chat completions.
        """
        if not self.api_key:
            return []
        
        try:
            req = urllib.request.Request(
                self.MODELS_URL,
                headers={'Authorization': f'Bearer {self.api_key}'},
                method='GET'
            )
            
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            
            models = []
            for model in data.get('data', []):
                model_id = model.get('id', '')
                if model_id:
                    models.append(model_id)
            
            return models
        except Exception as e:
            print(f"[GroqProvider] Failed to list models: {e}")
            return []
    
    def generate(self, system_prompt: str, user_message: str,
                 max_tokens: int = 200) -> Optional[str]:
        """Generate a response using Groq's API.
        
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
            raise AIProviderError("API key required for Groq")
        
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
