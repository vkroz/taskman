"""Invalidate parent acceptance evidence when its contained work changes."""
from taskman.model.layout import parent_item_path
from taskman.model.names import parse_name, special_file_name
from taskman.model.yaml_io import read_file, write_file


def invalidate_ancestors(tasks_dir, path):
    parent = parent_item_path(path, tasks_dir)
    while parent:
        file = parent / special_file_name(parse_name(parent.name).item_id)
        data, body = read_file(file)
        data["revision"] = int(data.get("revision", 0)) + 1
        write_file(file, data, body)
        parent = parent_item_path(parent, tasks_dir)
