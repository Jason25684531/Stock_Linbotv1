"""Read-only Windows task capture (design D11): positional parsing of schtasks /query /fo csv /v, repository tasks only."""
from scripts import scheduler_status as ss

ACTION = str(ss.ROOT) + r'\execution\evening_run.bat'
SAMPLE = '\r\n'.join([
    r'"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time","Last Result","Author","Task To Run"',
    r'"PC","\StockAI_Evening","2026/11/9 18:30:00","Ready","Interactive only","2026/11/8 18:30:00","1","PC\Norlan","' + ACTION + ' "',
    r'"PC","\Other","N/A","Ready","Interactive only","N/A","0","x","C:\elsewhere\run.bat"',
]) + '\r\n'


def test_only_repository_tasks_are_kept_with_their_real_state():
    assert ss.parse(SAMPLE) == [{'name': r'\StockAI_Evening', 'next_run': '2026/11/9 18:30:00', 'status': 'Ready', 'logon_mode': 'Interactive only',
                                 'last_run': '2026/11/8 18:30:00', 'last_result': '1', 'action': ACTION}]
