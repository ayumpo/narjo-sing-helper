from narjo_sing.separation import cpu_name


def test_x86_uses_the_model_name():
    cpuinfo = "processor\t: 0\nvendor_id\t: GenuineIntel\nmodel name\t: 12th Gen Intel(R) Core(TM) i5-12500T\n"
    assert cpu_name(cpuinfo, "x86_64", 12) == "12th Gen Intel(R) Core(TM) i5-12500T"


def test_docker_on_a_mac_says_apple_silicon():
    cpuinfo = "processor\t: 0\nBogoMIPS\t: 48.00\nCPU implementer\t: 0x61\nCPU part\t: 0x000\n"
    assert cpu_name(cpuinfo, "aarch64", 6) == "Apple silicon, 6 cores"


def test_other_arm_boards_name_the_architecture():
    cpuinfo = "processor\t: 0\nCPU implementer\t: 0x41\nCPU part\t: 0xd08\n"
    assert cpu_name(cpuinfo, "aarch64", 4) == "aarch64, 4 cores"


def test_nothing_known():
    assert cpu_name("", "", None) == "unknown"
