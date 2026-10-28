from datetime import datetime

from ina226_monitor.filename import build_base_name, sanitize, unique_base_name

DT = datetime(2026, 10, 28, 14, 30, 5)


def test_ac18_examples():
    assert build_base_name(DT, "REG-U3", "MAIN-RevB", "福井") == "20261028_143005_REG-U3_MAIN-RevB_福井"
    assert build_base_name(DT, "", "MAIN-RevB", "福井") == "20261028_143005_MAIN-RevB_福井"
    assert build_base_name(DT, "a/b:c", "MAIN-RevB", "福井") == "20261028_143005_a_b_c_MAIN-RevB_福井"


def test_all_empty_and_no_datetime_uses_now():
    assert build_base_name(None, "", "", "", now=DT) == "20261028_143005"


def test_sanitize():
    assert sanitize('a\\b/c:d*e?f"g<h>i|j k\tl') == "a_b_c_d_e_f_g_h_i_j_k_l"
    assert sanitize("name..") == "name"


def test_unique_base_name(tmp_path):
    assert unique_base_name(tmp_path, "x") == "x"
    (tmp_path / "x.png").write_bytes(b"")
    assert unique_base_name(tmp_path, "x") == "x_2"
    (tmp_path / "x_2.txt").write_bytes(b"")
    assert unique_base_name(tmp_path, "x") == "x_3"
