# Progressive Streaming Tests

This directory contains comprehensive tests for the progressive streaming feature.

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run specific test categories
pytest tests/test_progressive_streaming.py -v
pytest tests/test_message_processing.py -v
pytest tests/test_sse_integration.py -v
pytest tests/test_error_scenarios.py -v
pytest tests/test_performance.py -v

# Run with coverage report
pytest tests/ --cov-report=html --cov-report=term-missing
```
