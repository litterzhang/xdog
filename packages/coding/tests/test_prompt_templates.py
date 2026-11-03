from xdog.agent.compaction import SUMMARY_TEMPLATE
from xdog.coding.core.prompt_templates import COMPACTION_SUMMARY_TEMPLATE, HOST_CONTRACT


def test_host_contract_and_actual_compaction_template():
    assert 'coding agent' in HOST_CONTRACT
    assert 'host-reported configuration' in HOST_CONTRACT
    assert 'underlying model identity' in HOST_CONTRACT
    assert 'not granted by' in HOST_CONTRACT
    assert 'already\nauthorized action' in HOST_CONTRACT
    assert '<system-reminder>' in HOST_CONTRACT
    assert COMPACTION_SUMMARY_TEMPLATE == SUMMARY_TEMPLATE
    assert 'Actual summary' in COMPACTION_SUMMARY_TEMPLATE.format(summary='Actual summary')
