import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    database_url: str
    tdx_root: Path
    host: str
    port: int
    mcp_port: int


def settings():
    load_dotenv(ROOT / '.env', override=False)
    url = os.getenv('QUANT_DATABASE_URL', '')
    if not url:
        raise RuntimeError('请先配置 .env 中的 QUANT_DATABASE_URL')
    return Settings(url, Path(os.getenv('QUANT_TDX_ROOT', 'D:/tdx')),
                    os.getenv('QUANT_HOST', '127.0.0.1'), int(os.getenv('QUANT_PORT', '8765')),
                    int(os.getenv('QUANT_MCP_PORT', '8766')))
