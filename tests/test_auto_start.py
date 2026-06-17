import pytest
import sys
from unittest.mock import MagicMock, patch
from cli.macagent import _ensure_server_running

def test_ensure_server_running_when_ok():
    client = MagicMock()
    client.health.return_value = {"ok": True}
    
    # Should return immediately without sys.exit
    _ensure_server_running(client, no_auto_start=False)
    assert client.health.call_count == 1

@patch("cli.macagent.subprocess.Popen")
@patch("cli.macagent.time.sleep")
def test_ensure_server_running_auto_starts(mock_sleep, mock_popen):
    client = MagicMock()
    # First call fails, second call succeeds
    client.health.side_effect = [Exception("Connection refused"), {"ok": True}]
    
    _ensure_server_running(client, no_auto_start=False)
    
    assert mock_popen.call_count == 1
    assert client.health.call_count == 2
    assert mock_sleep.call_count == 1

def test_ensure_server_running_no_auto_start():
    client = MagicMock()
    client.health.side_effect = Exception("Connection refused")
    
    with pytest.raises(SystemExit) as exc_info:
        _ensure_server_running(client, no_auto_start=True)
        
    assert exc_info.value.code == 1
