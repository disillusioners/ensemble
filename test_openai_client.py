#!/usr/bin/env python3
"""Quick test script to verify OpenAI client configuration."""

import os
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables from .env
load_dotenv()

def test_openai_connection():
    """Test OpenAI API connection with current config."""
    
    # Get config from environment
    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")
    model = os.getenv("OPENAI_MODEL", "gpt-4")
    
    print("=" * 50)
    print("OpenAI Client Configuration Test")
    print("=" * 50)
    print(f"API Key: {api_key[:20]}..." if api_key else "API Key: NOT SET")
    print(f"Base URL: {base_url}")
    print(f"Model: {model}")
    print("=" * 50)
    
    if not api_key:
        print("\n❌ ERROR: OPENAI_API_KEY not found in environment")
        return False
    
    try:
        # Initialize client
        client = OpenAI(
            api_key=api_key,
            base_url=base_url
        )
        
        print("\n🔄 Testing connection with a simple completion...")
        
        # Make a simple test request
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": "Say 'Hello, environment is working!' and nothing else."}
            ],
            max_tokens=50,
            temperature=0
        )
        
        # Extract response
        message = response.choices[0].message.content
        
        print("\n✅ SUCCESS! Connection working!")
        print(f"\n📝 Model response: {message}")
        print(f"\n📊 Usage: {response.usage.total_tokens} tokens")
        
        return True
        
    except Exception as e:
        print(f"\n❌ ERROR: {type(e).__name__}: {e}")
        return False

if __name__ == "__main__":
    success = test_openai_connection()
    exit(0 if success else 1)
