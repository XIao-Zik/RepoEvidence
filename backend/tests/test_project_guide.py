import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def test_project_guide_maps_product_features_and_safe_modification_areas() -> None:
    guide = (ROOT / "docs/PROJECT_GUIDE.md").read_text(encoding="utf-8")

    for heading in (
        "代码变更与快照刷新",
        "架构地图",
        "哪些地方适合你参与修改",
        "修改的标准流程",
    ):
        assert heading in guide
