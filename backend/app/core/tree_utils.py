"""通用树构建工具，对应若依的 TreeSelect.java（部门树、菜单树下拉选择都用这个结构）"""


def build_tree_select(nodes: list[dict]) -> list[dict]:
    """
    nodes: 每项至少有 id / parent_id / label / disabled。
    返回 [{"id":, "label":, "disabled":, "children": [...]}]，children 为空时不出现该字段。
    """
    id_set = {n["id"] for n in nodes}
    node_map = {n["id"]: {**n, "children": []} for n in nodes}

    roots = [node_map[n["id"]] for n in nodes if n["parent_id"] not in id_set]
    for n in nodes:
        parent = node_map.get(n["parent_id"])
        if parent is not None:
            parent["children"].append(node_map[n["id"]])

    if not roots:
        roots = list(node_map.values())

    def _clean(node: dict) -> dict:
        out = {"id": node["id"], "label": node["label"], "disabled": node["disabled"]}
        if node["children"]:
            out["children"] = [_clean(c) for c in node["children"]]
        return out

    return [_clean(r) for r in roots]
