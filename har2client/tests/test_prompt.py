from har2client.solve import prompt


def test_system_prompt_does_not_hard_code_the_constructor():
    text = prompt.build_system_prompt()
    assert "api_key" not in text
    assert "constructor" in text and "goal" in text


def test_system_prompt_keeps_the_other_rules():
    text = prompt.build_system_prompt()
    for needle in ("class Client", "httpx", "Never hard-code", "single ```python fenced block", "unrelated traffic"):
        assert needle in text


def test_user_prompt_points_at_the_goal_constructor_not_a_fixed_one():
    text = prompt.build_user_prompt("GOAL TEXT: Client(base_url, username, password)", "{HAR}")
    assert "GOAL TEXT" in text and "{HAR}" in text
    assert "api_key" not in text
    assert "constructor" in text.lower() and "goal" in text.lower()
