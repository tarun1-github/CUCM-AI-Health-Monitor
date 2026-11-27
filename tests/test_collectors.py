"""Tests for CUCM CLI output parsers and health collector."""

from unittest.mock import MagicMock

from backend.collectors.cpu_memory import parse_perf_memory, parse_perf_processor, parse_process_load
from backend.collectors.cucm_health import CUCMHealthCollector
from backend.collectors.disk_health import parse_diskusage_common
from backend.collectors.network_health import parse_diagnose_test, parse_network_eth0
from backend.collectors.service_health import parse_service_list
from backend.collectors.system_health import parse_show_hardware, parse_show_status, parse_show_version_active
from backend.models.health import HealthStatus


def test_parse_system_and_cpu_memory_outputs():
    ver = parse_show_version_active("Active Master Version: 15.0.1.10000-14")
    assert ver["cucm_version"] == "15.0.1.10000-14"

    status = parse_show_status(
        "Host Name : cucm-pub-01\nUptime : 14 days, 3:22\nCPU Idle : 78.5%\n"
        "Memory Total : 16384M\nMemory Used : 12288M\n"
        "Common 80G 15G 65G (81%)\n"
    )
    assert status["hostname"] == "cucm-pub-01"
    assert status["cpu_utilization_pct"] == 21.5
    assert status["memory_utilization_pct"] == 75.0
    assert status["disk_utilization_pct"] == 81.0

    hw = parse_show_hardware("Platform Type : VMware\nProcessor Count : 4\n")
    assert hw["hardware_model"] == "VMware"
    assert hw["cpu_count"] == 4

    load = parse_process_load("load average: 1.25, 1.10, 0.95\n%Cpu(s): 12.0 us, 82.0 id\nMiB Mem : 16000 total, 4000 free, 12000 used")
    assert load["load_average_1m"] == 1.25
    assert load["cpu_utilization_pct"] == 18.0
    assert load["memory_utilization_pct"] == 75.0

    assert parse_perf_processor("% Idle Time = 65.0")["cpu_utilization_pct"] == 35.0
    assert parse_perf_memory("% Memory Used = 62.5")["memory_utilization_pct"] == 62.5


def test_parse_disk_and_network():
    disk = parse_diskusage_common("/dev/sda6  76G  69G  7G  91% /common")
    assert disk["disk_utilization_pct"] == 91.0
    assert disk["disk_total_gb"] == 76.0

    net = parse_network_eth0("IP Address : 10.197.206.141\nIP Mask : 255.255.255.0\nGateway : 10.197.206.1\nPrimary DNS : 10.197.1.10")
    assert net["ip_address"] == "10.197.206.141"
    assert net["gateway"] == "10.197.206.1"

    diags = parse_diagnose_test("test - dns_forward : Passed\ntest - ntp_reachability : Failed")
    assert len(diags) == 2
    assert diags[1]["result"] == "Failed"


def test_parse_service_list():
    raw = """
    Cisco CallManager[STARTED]
    Cisco Tomcat[STARTED]
    Cisco Tftp[STOPPED]
    Cisco Dialed Number Analyzer[NOT ACTIVATED]
    """
    services = parse_service_list(raw, critical_services={"Cisco CallManager", "Cisco Tftp"})
    assert len(services) == 4
    tftp = next(s for s in services if s.name == "Cisco Tftp")
    assert tftp.health == HealthStatus.CRITICAL


def test_collector_with_mocked_ssh_and_axl(tmp_path, monkeypatch):
    mock_ssh = MagicMock()
    mock_ssh.is_connected.return_value = True
    mock_ssh.get_prompt.return_value = "admin:"
    mock_ssh.send_command.side_effect = lambda cmd, timeout=45: {
        "show version active": "Active Master Version: 15.0.1.10000-14",
        "show status": "Host Name : cucm-pub\nCPU Idle : 80.0%\nCommon 80G 40G 40G (50%)",
        "utils service list": "Cisco CallManager[STARTED]\nCisco Tomcat[STARTED]",
    }.get(cmd, "")

    mock_axl = MagicMock()
    mock_axl.get_cucm_version.return_value = "15.0.1.10000-14"
    mock_axl.get_cluster_nodes.return_value = [{"name": "cucm-pub", "node_role": "Publisher"}]

    collector = CUCMHealthCollector(ssh_client=mock_ssh, axl_client=mock_axl)
    snap = collector.collect(command_ids=["show_version_active", "show_status", "utils_service_list"])
    assert snap.connectivity.ssh_connected is True
    assert snap.connectivity.axl_connected is True
    assert snap.system.hostname == "cucm-pub"
    assert len(snap.services) == 2


def test_cucm15_live_format_regressions():
    """Regression tests for real CUCM 15.0.1 CLI output formats."""
    status = parse_show_status(
        "Host Name          : UCM15-HQ-PUB\n"
        "CPU Idle:   20.92%  System:   38.27%    User:   23.47%\n"
        "Memory Total:        7870204K\n"
        "        Free:         142800K\n"
        "        Used:        6103260K\n"
        "Disk/active         19760356K        4005496K       15535932K (80%)\n"
        "Disk/logging        68788520K       44207724K       21040780K (33%)\n"
    )
    assert status["memory_utilization_pct"] == 77.55
    assert status["disk_utilization_pct"] == 33.0
    assert len(status["disk_partitions"]) == 2

    hw = parse_show_hardware(
        "HW Platform       : VMware Virtual Machine\n"
        "Processors        : 2\n"
        "Memory            : 8192 MBytes\n"
        "/dev/sda6      ext4       66G   21G   43G  33% /common\n"
    )
    assert hw["hardware_model"] == "VMware Virtual Machine"
    assert hw["cpu_count"] == 2
    assert hw["disk_total_gb"] == 66.0
    assert hw["disk_used_gb"] == 21.0

    perf_cpu = parse_perf_processor(
        "    0               -> Idle Percentage                = 65\n"
        "    _Total          -> % CPU Time                     = 31\n"
        "    _Total          -> Idle Percentage                = 69\n"
    )
    assert perf_cpu["cpu_idle_pct"] == 69.0
    assert perf_cpu["cpu_utilization_pct"] == 31.0

    perf_mem = parse_perf_memory("                    -> % Mem Used                     = 81\n")
    assert perf_mem["memory_utilization_pct"] == 81.0

    net = parse_network_eth0(
        "IP Address   : 10.197.206.141     IP Mask    : 255.255.255.000\n"
        "Primary      : 10.106.44.6        Secondary  : Not Configured\n"
        "Gateway      : 10.197.206.1 on Ethernet 0\n"
    )
    assert net["dns_primary"] == "10.106.44.6"
    assert "dns_secondary" not in net

    diags = parse_diagnose_test(
        "test - tomcat : 2 second(s) left\n"
        "test - tomcat : 0 second(s) left\n"
        "test - tomcat : Passed\n"
        "skip - disk_files : This module must be run directly and off hours\n"
    )
    assert len(diags) == 2
    assert diags[0] == {"test": "tomcat", "result": "Passed"}

    svcs = parse_service_list(
        "Cisco Certificate Enrollment Service[STOPPED]  Component is not running\n"
        "Cisco CallManager[STARTED]\n",
        critical_services={"Cisco CallManager"},
    )
    assert len(svcs) == 2
    assert svcs[0].name == "Cisco Certificate Enrollment Service"
    assert svcs[0].status == "STOPPED"
    assert svcs[0].health == HealthStatus.WARNING
