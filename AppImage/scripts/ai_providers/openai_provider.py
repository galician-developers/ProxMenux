"""OpenAI provider implementation.

OpenAI is the industry standard for AI APIs.
Models are loaded dynamically from the API.
"""
from typing import Optional, List
import json
import urllib.request
import urllib.error
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
    REQUIRES_API_KEY = True
    DEFAULT_API_URL = "https://api.openai.com/v1/chat/completions"
    DEFAULT_MODELS_URL = "https://api.openai.com/v1/models"
    
    def list_models(self) -> List[str]:
        """List available OpenAI models.
        
        Returns:
            List of model IDs available for chat completions.
        """
        if not self.api_key:
            return []
        
        try:
            # Determine models URL from base_url if set
            if self.base_url:
                base = self.base_url.rstrip('/')
                if not base.endswith('/v1'):
                    base = f"{base}/v1"
                models_url = f"{base}/models"
            else:
                models_url = self.DEFAULT_MODELS_URL
            
            req = urllib.request.Request(
                models_url,
                headers={'Authorization': f'Bearer {self.api_key}'},
                method='GET'
            )
            
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            
            models = []
            for model in data.get('data', []):
                model_id = model.get('id', '')
                # Filter to chat models only (skip embeddings, etc.)
                if model_id and ('gpt' in model_id.lower() or 'turbo' in model_id.lower()):
                    models.append(model_id)
            
            return models
        except Exception as e:
            print(f"[OpenAIProvider] Failed to list models: {e}")
            return []
    
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
