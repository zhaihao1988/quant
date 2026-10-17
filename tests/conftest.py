import uuid
import pytest


@pytest.fixture(autouse=True)
def separate_runtime_exports_and_market_guard(tmp_path,monkeypatch):
    monkeypatch.setenv('QUANT_ARCHIVE_DIR',str(tmp_path/'archives'))
    monkeypatch.setenv('QUANT_BACKUP_ON_ARCHIVE','0')
    monkeypatch.setattr('quant.data_guard.DATA_LOCK',1000000+uuid.uuid4().int%1000000000)
