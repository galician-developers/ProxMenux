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
    
    # Models to exclude (not suitable for chat/text generation)
    EXCLUDED_PATTERNS = [
        'embedding', 'whisper', 'tts', 'dall-e', 'image',
        'instruct', 'realtime', 'audio', 'moderation',
        'search', 'code-search', 'text-similarity', 'babbage', 'davinci',
        'curie', 'ada', 'transcribe'
    ]
    
    # Recommended models for chat (in priority order)
    RECOMMENDED_PREFIXES = ['gpt-4o-mini', 'gpt-4o', 'gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo']
    
    def list_models(self) -> List[str]:
        """List available OpenAI models for chat completions.
        
        Filters to only chat-capable models, excluding:
        - Embedding models
        - Audio/speech models (whisper, tts)
        - Image models (dall-e)
        - Instruct models (different API)
        - Legacy models (babbage, davinci, etc.)
        
        Returns:
            List of model IDs suitable for chat completions.
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
                if not model_id:
                    continue
                
                model_lower = model_id.lower()
                
                # Must be a GPT model
                if 'gpt' not in model_lower:
                    continue
                
                # Exclude non-chat models
                if any(pattern in model_lower for pattern in self.EXCLUDED_PATTERNS):
                    continue
                
                models.append(model_id)
            
            # Sort with recommended models first
            def sort_key(m):
                m_lower = m.lower()
                for i, prefix in enumerate(self.RECOMMENDED_PREFIXES):
                    if m_lower.startswith(prefix):
                        return (i, m)
                return (len(self.RECOMMENDED_PREFIXES), m)
            
            return sorted(models, key=sort_key)
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
