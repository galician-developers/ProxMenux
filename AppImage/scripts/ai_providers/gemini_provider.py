"""Google Gemini provider implementation.

Google's Gemini models offer a free tier and excellent quality/price ratio.
Models are loaded dynamically from the API - no hardcoded model names.
"""
from typing import Optional, List
import json
import urllib.request
import urllib.error
from .base import AIProvider, AIProviderError


class GeminiProvider(AIProvider):
    """Google Gemini provider using the Generative Language API."""
    
    NAME = "gemini"
    REQUIRES_API_KEY = True
    API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    
    # Patterns to exclude from model list (experimental, preview, specialized)
    EXCLUDED_PATTERNS = [
        'preview', 'exp', 'experimental', 'computer-use', 
        'deep-research', 'image', 'embedding', 'aqa', 'tts',
        'learnlm', 'imagen', 'veo'
    ]
    
    def list_models(self) -> List[str]:
        """List available Gemini models that support generateContent.
        
        Filters to only stable text generation models, excluding:
        - Preview/experimental models
        - Image generation models
        - Embedding models
        - Specialized models (computer-use, deep-research, etc.)
        
        Returns:
            List of model IDs available for text generation.
        """
        if not self.api_key:
            return []
        
        try:
            url = f"{self.API_BASE}?key={self.api_key}"
            req = urllib.request.Request(url, method='GET')
            
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            
            models = []
            for model in data.get('models', []):
                model_name = model.get('name', '')
                # Extract just the model ID (e.g., "models/gemini-pro" -> "gemini-pro")
                if model_name.startswith('models/'):
                    model_id = model_name[7:]
                else:
                    model_id = model_name
                
                # Only include models that support generateContent
                supported_methods = model.get('supportedGenerationMethods', [])
                if 'generateContent' not in supported_methods:
                    continue
                
                # Exclude experimental, preview, and specialized models
                model_lower = model_id.lower()
                if any(pattern in model_lower for pattern in self.EXCLUDED_PATTERNS):
                    continue
                
                models.append(model_id)
            
            # Sort with recommended models first (flash-lite, flash, pro)
            def sort_key(m):
                m_lower = m.lower()
                if 'flash-lite' in m_lower:
                    return (0, m)  # Best for notifications (fast, cheap)
                if 'flash' in m_lower:
                    return (1, m)
                if 'pro' in m_lower:
                    return (2, m)
                return (3, m)
            
            return sorted(models, key=sort_key)
        except Exception as e:
            print(f"[GeminiProvider] Failed to list models: {e}")
            return []
    
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
