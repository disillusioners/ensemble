#!/usr/bin/env python3
"""
End-to-end test to diagnose thinking extraction.

This script tests both streaming and non-streaming modes to identify where 
thinking data lives in the LangChain/OpenAI response structure.
"""

import os
import sys
import json
import traceback
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage
from openai import OpenAI

# Load config
from daemon.config import load_config


def test_non_streaming():
    """Test non-streaming mode with full inspection."""
    print("\n" + "="*70)
    print("NON-STREAMING TEST (invoke)")
    print("="*70)
    
    config = load_config()
    print(f"\n[CONFIG] base_url: {config.llm.base_url}")
    print(f"[CONFIG] model: {config.llm.model}")
    
    llm = ChatOpenAI(
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
        model=config.llm.model,
        temperature=config.llm.temperature,
    )
    
    try:
        response = llm.invoke([HumanMessage(content="What is 2+2? Answer briefly.")])
        
        print(f"\n[RESULT] Response type: {type(response).__name__}")
        print(f"[RESULT] Content: {response.content}")
        
        print(f"\n[DETAILS] response.additional_kwargs:")
        print(f"  {json.dumps(response.additional_kwargs, indent=2, default=str)}")
        
        print(f"\n[DETAILS] response.response_metadata:")
        print(f"  {json.dumps(response.response_metadata, indent=2, default=str)}")
        
        # Check ALL attributes
        print(f"\n[DETAILS] All public attributes:")
        for attr in sorted(dir(response)):
            if not attr.startswith('_'):
                try:
                    val = getattr(response, attr)
                    if not callable(val):
                        # Truncate long values for readability
                        val_str = str(val)
                        if len(val_str) > 200:
                            val_str = val_str[:200] + "..."
                        print(f"  {attr}: {val_str}")
                except Exception as e:
                    print(f"  {attr}: <error: {e}>")
        
        # Check if there's a way to access raw response
        print(f"\n[DETAILS] Checking for raw response access:")
        if hasattr(response, 'response'):
            print(f"  response.response: {response.response}")
        if hasattr(response, '_response'):
            print(f"  response._response: {response._response}")
        
    except Exception as e:
        print(f"\n[ERROR] {e}")
        traceback.print_exc()


def test_streaming():
    """Test streaming mode to see all chunks."""
    print("\n" + "="*70)
    print("STREAMING TEST (stream)")
    print("="*70)
    
    config = load_config()
    
    llm = ChatOpenAI(
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
        model=config.llm.model,
        temperature=config.llm.temperature,
    )
    
    try:
        full_content = ""
        all_chunks = []
        
        print("\n[STREAM] Receiving chunks:")
        for i, chunk in enumerate(llm.stream([HumanMessage(content="What is 2+2? Answer briefly.")])):
            print(f"\n--- Chunk {i} ---")
            print(f"Type: {type(chunk).__name__}")
            print(f"Content: {chunk.content!r}")
            
            # Collect all content
            if hasattr(chunk, 'content') and chunk.content:
                full_content += chunk.content
            
            # Check additional_kwargs on chunk
            if hasattr(chunk, 'additional_kwargs'):
                print(f"additional_kwargs: {chunk.additional_kwargs}")
                if chunk.additional_kwargs.get('thinking'):
                    print(f"  *** FOUND THINKING in chunk {i}: {chunk.additional_kwargs['thinking'][:200]}...")
                if chunk.additional_kwargs.get('reasoning_content'):
                    print(f"  *** FOUND reasoning_content in chunk {i}: {chunk.additional_kwargs['reasoning_content'][:200]}...")
            
            # Check response_metadata on chunk
            if hasattr(chunk, 'response_metadata'):
                print(f"response_metadata: {chunk.response_metadata}")
            
            # Check for tool calls
            if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                print(f"tool_calls: {chunk.tool_calls}")
            
            # Check for other attributes
            other_attrs = {}
            for attr in dir(chunk):
                if not attr.startswith('_') and attr not in ['content', 'additional_kwargs', 'response_metadata', 'tool_calls', 'type', 'id', 'name']:
                    try:
                        val = getattr(chunk, attr)
                        if not callable(val) and val:
                            other_attrs[attr] = str(val)[:100]
                    except:
                        pass
            if other_attrs:
                print(f"Other attrs: {other_attrs}")
            
            all_chunks.append(chunk)
        
        print(f"\n[STREAM] Full aggregated content: {full_content}")
        print(f"[STREAM] Total chunks: {len(all_chunks)}")
        
    except Exception as e:
        print(f"\n[ERROR] {e}")
        traceback.print_exc()


def test_direct_openai_client():
    """Test using OpenAI client directly to see raw API response."""
    print("\n" + "="*70)
    print("DIRECT OPENAI CLIENT TEST")
    print("="*70)
    
    config = load_config()
    
    try:
        client = OpenAI(
            base_url=config.llm.base_url,
            api_key=config.llm.api_key,
        )
        
        print(f"\n[CLIENT] Using base_url: {config.llm.base_url}")
        print(f"[CLIENT] Model: {config.llm.model}")
        
        # Test non-streaming
        print("\n--- Non-streaming ---")
        response = client.chat.completions.create(
            model=config.llm.model,
            messages=[{"role": "user", "content": "What is 2+2? Answer briefly."}],
            temperature=config.llm.temperature,
        )
        
        print(f"\n[RAW] Response object: {response}")
        print(f"[RAW] Model: {response.model}")
        print(f"[RAW] Choices: {len(response.choices)}")
        
        if response.choices:
            choice = response.choices[0]
            print(f"[RAW] Message role: {choice.message.role}")
            print(f"[RAW] Message content: {choice.message.content}")
            print(f"[RAW] Message reasoning_content: {getattr(choice.message, 'reasoning_content', 'NOT FOUND')}")
            print(f"[RAW] Finish reason: {choice.finish_reason}")
        
        print(f"[RAW] Usage: {response.usage}")
        print(f"[RAW] Response model dict: {response.model_dump()}")
        
        # Test streaming
        print("\n--- Streaming ---")
        stream = client.chat.completions.create(
            model=config.llm.model,
            messages=[{"role": "user", "content": "What is 2+2? Answer briefly."}],
            temperature=config.llm.temperature,
            stream=True,
        )
        
        print("\n[STREAM] Receiving chunks:")
        for i, chunk in enumerate(stream):
            print(f"\n--- Chunk {i} ---")
            print(f"Chunk: {chunk}")
            print(f"Chunk dict: {chunk.model_dump()}")
            
            if chunk.choices and chunk.choices[0].delta:
                delta = chunk.choices[0].delta
                print(f"Delta content: {delta.content}")
                print(f"Delta reasoning_content: {getattr(delta, 'reasoning_content', 'NOT FOUND')}")
            
            if i > 10:  # Limit output
                print("\n[STREAM] ... (limiting to first 10 chunks)")
                break
                
    except Exception as e:
        print(f"\n[ERROR] {e}")
        traceback.print_exc()


def test_with_extra_headers():
    """Test with extra headers to see if there's a thinking header."""
    print("\n" + "="*70)
    print("TEST WITH EXTRA HEADERS")
    print("="*70)
    
    config = load_config()
    
    # Try to get raw response with different settings
    llm = ChatOpenAI(
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
        model=config.llm.model,
        temperature=config.llm.temperature,
        extra_body={
            "extra_body": {
                "thinking": {
                    "type": "enabled"
                }
            }
        } if hasattr(ChatOpenAI, 'extra_body') else {}
    )
    
    # Alternative: Use the client directly with extra params
    client = OpenAI(
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
    )
    
    # Check what params the API might support
    print("\n[INFO] Testing with extended thinking params...")
    
    try:
        response = client.chat.completions.create(
            model=config.llm.model,
            messages=[{"role": "user", "content": "What is 2+2? Answer briefly."}],
            # Some providers use these to enable thinking
            # thinking={"type": "enabled"},
            # reasoning_effort="high",
        )
        
        print(f"[RESULT] reasoning_content: {getattr(response.choices[0].message, 'reasoning_content', 'NOT FOUND')}")
        print(f"[RESULT] content: {response.choices[0].message.content}")
        
    except Exception as e:
        print(f"[ERROR] {e}")


def main():
    """Run all tests."""
    print("="*70)
    print("THINKING EXTRACTION DIAGNOSTIC TEST")
    print("="*70)
    
    # Check environment
    print(f"\n[ENV] OPENAI_BASE_URL: {os.environ.get('OPENAI_BASE_URL', 'not set')}")
    print(f"[ENV] OPENAI_MODEL: {os.environ.get('OPENAI_MODEL', 'not set')}")
    print(f"[ENV] OPENAI_API_KEY: {'set' if os.environ.get('OPENAI_API_KEY') else 'not set'}")
    
    # Run tests
    test_non_streaming()
    test_streaming()
    test_direct_openai_client()
    test_with_extra_headers()
    
    print("\n" + "="*70)
    print("DIAGNOSTIC COMPLETE")
    print("="*70)


if __name__ == "__main__":
    main()
