"""Dify可选配置及按应用密钥加载，不连接数据库。
标签：[架构组] [P1]
"""
import pytest
from app.config.settings import Settings


def test_dify_unconfigured_does_not_block_startup(monkeypatch):
    monkeypatch.delenv('DIFY_BASE_URL', raising=False)
    monkeypatch.delenv('DIFY_API_KEY', raising=False)
    value = Settings(_env_file=None)
    assert value.DIFY_BASE_URL == 'http://127.0.0.1/v1'
    assert value.DIFY_API_KEY == ''


@pytest.mark.parametrize('raw,expected', [('', ''), ('  ', '  '), ('test-key', 'test-key')])
def test_dify_environment_keys(monkeypatch, raw, expected):
    monkeypatch.setenv('DIFY_API_KEY', raw)
    assert Settings(_env_file=None).DIFY_API_KEY == expected


def test_dify_keys_load_from_dotenv(monkeypatch, tmp_path):
    monkeypatch.delenv('DIFY_BASE_URL', raising=False)
    monkeypatch.delenv('DIFY_API_KEY', raising=False)
    env = tmp_path / '.env'
    env.write_text('DIFY_BASE_URL=http://localhost/v1\nDIFY_API_KEY=test-key\n', encoding='utf-8')
    value = Settings(_env_file=env)
    assert value.DIFY_BASE_URL == 'http://localhost/v1'
    assert value.DIFY_API_KEY == 'test-key'


def test_main_imports_without_starting_database():
    import main
    assert main.app is not None
