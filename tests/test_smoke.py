import wire_domain


def test_has_version():
    assert isinstance(wire_domain.__version__, str)
    assert wire_domain.__version__.count(".") >= 1
