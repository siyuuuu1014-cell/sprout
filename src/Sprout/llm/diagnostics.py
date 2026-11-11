"""Non-sensitive completion observations; never log prompts or credentials."""
import logging


def record_completion(provider, finish_reason, usage):
    info = {'finish_reason': finish_reason,
            'completion_tokens': usage.completion_tokens if usage is not None else None,
            'max_tokens': provider.max_output_tokens}
    provider.last_response_info = info
    logging.getLogger('Sprout.llm.response').info(
        'model_response provider=%s model=%s finish_reason=%s completion_tokens=%s max_tokens=%s',
        provider.name, provider.model, info['finish_reason'], info['completion_tokens'],
        info['max_tokens'])
    return info
