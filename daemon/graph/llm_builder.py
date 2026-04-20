"""LLM instance builder with retry support."""
import logging
from tenacity import Retrying, stop_after_attempt, wait_exponential_jitter
from langchain_core.runnables import RunnableLambda

from ..llm_error_classifier import (
    classify_llm_errors,
    _make_llm_retry_strategy,
)
from .thinking_llm import ThinkingChatOpenAI

logger = logging.getLogger(__name__)


def build_instance_llms(
    llm_config_with_headers: dict,
    model_standard: str,
    model_vision: str | None,
    tools: list,
    retry_config: dict | None = None,
):
    """Create LLM instances for agent execution.

    This function handles the logic for creating:
    - llm_with_tools: Primary LLM bound to tools (vision if configured, else standard)
    - llm_standard: Standard LLM (always bound to tools for tool-calling)

    Returns:
        Tuple of (llm_with_tools, llm_standard)
    """
    llm_standard = None
    llm_with_tools = None

    if model_vision:
        logger.info(f"[Graph] Vision model configured: {model_vision}, will use for FIRST call only per DEC-003")
        # Filter model_vision from config to avoid passing it to the API
        vision_config = {k: v for k, v in llm_config_with_headers.items() if k != "model_vision"}
        vision_config["model"] = model_vision
        llm_with_tools = ThinkingChatOpenAI(**vision_config).bind_tools(tools)
    else:
        logger.info("[Graph] No vision model configured, using standard model for all calls")

    # Create standard LLM (always needed, even if vision is configured)
    # Filter model_vision from config to avoid noisy LangChain warnings
    standard_config = {k: v for k, v in llm_config_with_headers.items() if k != "model_vision"}
    standard_config["model"] = model_standard
    llm_standard = ThinkingChatOpenAI(**standard_config)

    # Always bind tools to llm_standard, regardless of vision configuration
    if llm_with_tools is None:
        llm_with_tools = llm_standard.bind_tools(tools)
    llm_standard = llm_standard.bind_tools(tools)

    # Wrap with error classification and retry if config provided
    if retry_config:
        # CRITICAL: classify errors BEFORE retry so they can be caught
        llm_with_tools = classify_llm_errors(llm_with_tools)
        if llm_standard is not llm_with_tools:
            llm_standard = classify_llm_errors(llm_standard)

        transient_attempts = retry_config.get("transient_attempts", 8)
        timeout_attempts = retry_config.get("timeout_attempts", 3)

        retry_predicate = _make_llm_retry_strategy(
            transient_max=transient_attempts,
            timeout_max=timeout_attempts,
        )

        # Use max() as hard safety ceiling; the predicate controls per-category limits
        max_attempts = max(transient_attempts, timeout_attempts)

        # Use tenacity directly since LangChain's with_retry() no longer supports
        # custom retry predicates (the 'retry=' parameter was removed)
        retrying = Retrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(),
            retry=retry_predicate,
            reraise=True,
        )

        # Capture the classified LLMs for retry wrapper
        classified_llm = llm_with_tools

        def _run_with_retry(input_value):
            return retrying(classified_llm.invoke, input_value)

        llm_with_tools = RunnableLambda(_run_with_retry)

        # Also wrap standard LLM with Retrying if it's different from llm_with_tools.
        # This handles the dual-LLM architecture case where both vision and standard
        # models need their own retry wrappers.
        if llm_standard is not llm_with_tools:
            classified_standard = llm_standard
            def _run_standard_with_retry(input_value):
                return retrying(classified_standard.invoke, input_value)
            llm_standard = RunnableLambda(_run_standard_with_retry)

        logger.debug(
            f"LLM configured with {transient_attempts} transient retries, "
            f"{timeout_attempts} timeout retries"
        )

    return llm_with_tools, llm_standard
