# pipeline/llm_provider.py
# Provider abstraction layer. Switch LLM backend via config LLM_PROVIDER.
# Supported: "openai" | "anthropic" | "ollama"

from core.config import LLM_PROVIDER, OPENAI_API_KEY, OPENAI_LLM_MODEL


def chat_completion(system_prompt: str, user_message: str, temperature: float = 0) -> str:
    """
    Single-call LLM completion routed to the configured provider.
    Returns the response content as a string.
    """
    if LLM_PROVIDER == "openai":
        return _openai_chat(system_prompt, user_message, temperature)
    elif LLM_PROVIDER == "anthropic":
        return _anthropic_chat(system_prompt, user_message, temperature)
    elif LLM_PROVIDER == "ollama":
        return _ollama_chat(system_prompt, user_message, temperature)
    else:
        raise ValueError(f"Unsupported LLM_PROVIDER: '{LLM_PROVIDER}'. Choose openai, anthropic, or ollama.")


def _openai_chat(system_prompt: str, user_message: str, temperature: float) -> str:
    from openai import OpenAI
    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=OPENAI_LLM_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=temperature,
    )
    return response.choices[0].message.content.strip()


def _anthropic_chat(system_prompt: str, user_message: str, temperature: float) -> str:
    import anthropic
    import os
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return message.content[0].text.strip()


def _ollama_chat(system_prompt: str, user_message: str, temperature: float) -> str:
    import requests
    import os
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3")
    response = requests.post(
        f"{base_url}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
        },
    )
    return response.json()["message"]["content"].strip()
