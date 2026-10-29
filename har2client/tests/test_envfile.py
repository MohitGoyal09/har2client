from har2client.solve import envfile

SECRET = "sk-test-not-a-real-key-123"


def test_reads_key_from_dotenv(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"# comment\n\nexport REFLECTION_API_KEY='{SECRET}'\n", encoding="utf-8")
    environ = {}
    assert envfile.load_env_key(env, environ) is True
    assert environ == {"REFLECTION_API_KEY": SECRET}


def test_existing_value_and_missing_file_are_left_alone(tmp_path):
    environ = {"REFLECTION_API_KEY": "already"}
    assert envfile.load_env_key(tmp_path / "nope", environ) is False
    assert environ == {"REFLECTION_API_KEY": "already"}
    other = {}
    assert envfile.load_env_key(tmp_path / "nope", other) is False and other == {}


def test_reads_other_key_names(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"HF_TOKEN={SECRET}\n", encoding="utf-8")
    environ = {}
    assert envfile.load_env_key(env, environ, "HF_TOKEN") is True
    assert environ == {"HF_TOKEN": SECRET}
