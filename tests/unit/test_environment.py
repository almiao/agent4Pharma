import os

import pytest

from agent4pharma.runtime.environment import load_model_environment, load_wechat_environment


def test_wechat_dotenv_is_scoped_and_keeps_process_environment(tmp_path, monkeypatch):
    for key in ("PHARMA_WECHAT_APP_ID", "PHARMA_WECHAT_APP_SECRET", "PHARMA_WECHAT_ACCOUNT_NAME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PHARMA_WECHAT_APP_ID", "process-account")
    path = tmp_path / ".env"
    path.write_text(
        'PHARMA_WECHAT_APP_ID=file-account\nPHARMA_WECHAT_APP_SECRET="test-secret"\n'
        "export PHARMA_WECHAT_ACCOUNT_NAME='测试公众号'\nUNRELATED_TEST_SECRET=ignore\n",
        encoding="utf-8-sig",
    )
    load_model_environment(path)
    assert "PHARMA_WECHAT_APP_SECRET" not in os.environ
    load_wechat_environment(path)
    assert os.environ["PHARMA_WECHAT_APP_ID"] == "process-account"
    assert os.environ["PHARMA_WECHAT_APP_SECRET"] == "test-secret"
    assert os.environ["PHARMA_WECHAT_ACCOUNT_NAME"] == "测试公众号"
    assert "UNRELATED_TEST_SECRET" not in os.environ


def test_bad_dotenv_does_not_expose_value(tmp_path, monkeypatch):
    monkeypatch.delenv("PHARMA_WECHAT_APP_SECRET", raising=False)
    path = tmp_path / ".env"
    path.write_text('PHARMA_WECHAT_APP_SECRET="test-secret', encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_wechat_environment(path)
    assert "test-secret" not in str(error.value)
