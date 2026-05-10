import anthropic


def count(text: str) -> int:
    client = anthropic.Anthropic()
    result = client.messages.count_tokens(
        model="claude-sonnet-4-6",
        messages=[{"role": "user", "content": text}],
    )
    return result.input_tokens
