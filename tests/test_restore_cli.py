import restore


def test_parse_args_defaults():
    ns = restore._parse_args([])
    assert ns.object_name is None and ns.list is False and ns.local is None and ns.force is False


def test_parse_args_object_and_force():
    ns = restore._parse_args(["backup_x.zip", "--force"])
    assert ns.object_name == "backup_x.zip" and ns.force is True


def test_parse_args_local_and_list():
    ns = restore._parse_args(["--local", "backups", "--list"])
    assert ns.local == "backups" and ns.list is True
