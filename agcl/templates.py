# agcl/templates.py

MODEL_TEMPLATES = {
    "gemma": {
        "format_prompt": lambda messages: _gemma_prompt(messages),
        "generation": {
            "max_tokens": 4,
            "temperature": 0.0,
            "top_p": 0.8,
            "top_k": 10,
            "repeat_penalty": 1.0,
            "stop": ["<end_of_turn>", "\n"],
        },
    },

    "smollm2": {
        "format_prompt": lambda messages: _smollm2_prompt(messages),
        "generation": {
            "max_tokens": 6,
            "temperature": 0.15,
            "top_p": 0.9,
            "top_k": 40,
            "repeat_penalty": 1.05,
            "stop": ["</s>", "<|im_end|>"],
        },
    },

    "qwen25": {
        "format_prompt": lambda messages: _qwen25_prompt(messages),
        "generation": {
            "max_tokens": 6,
            "temperature": 0.0,
            "top_p": 0.9,
            "top_k": 20,
            "repeat_penalty": 1.05,
            "stop": ["<|im_end|>", "<|endoftext|>", "\n", "####", "Q:", "A:"],
        },
    },

    "plain": {
        "format_prompt": lambda messages: _plain_prompt(messages),
        "generation": {
            "max_tokens": 8,
            "temperature": 0.3,
            "top_p": 0.9,
            "top_k": 40,
            "repeat_penalty": 1.1,
            "stop": ["\n\n"],
        },
    },
}


def _turns(messages):
    """Yield only user/assistant turns, in order, skipping system."""
    for m in messages:
        role = m.get("role")
        if role in ("user", "assistant"):
            yield role, m.get("content", "")


def _gemma_prompt(messages):
    parts = []
    for role, content in _turns(messages):
        tag = "user" if role == "user" else "model"
        parts.append(f"<start_of_turn>{tag}\n{content}<end_of_turn>\n")
    parts.append("<start_of_turn>model\n")
    return "".join(parts)


def _smollm2_prompt(messages):
    parts = []
    for role, content in _turns(messages):
        parts.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")
    parts.append("<|im_start|>assistant\n")
    return "".join(parts)


def _qwen25_prompt(messages):
    system_msgs = [m.get("content", "") for m in messages if m.get("role") == "system"]
    default_system = (
        "You are a helpful assistant. Begin your reply with a short, natural "
        "conversational opener (a few words at most) — for example 'Sure,', "
        "'Of course,', 'Yes,', 'Got it —'. Do not output lists, headings, "
        "code, examples, or labels like 'Q:' or 'A:'. Just start the sentence."
    )
    system = system_msgs[-1] if system_msgs else default_system

    parts = [f"<|im_start|>system\n{system}<|im_end|>\n"]
    for role, content in _turns(messages):
        parts.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")
    parts.append("<|im_start|>assistant\n")
    return "".join(parts)


def _plain_prompt(messages):
    lines = []
    for role, content in _turns(messages):
        tag = "Q" if role == "user" else "A"
        lines.append(f"{tag}: {content}")
    lines.append("A:")
    return "\n".join(lines)
