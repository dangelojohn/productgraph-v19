from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


ADDONS_DIR = Path(__file__).resolve().parents[1] / "addons"


def _tag_name(element):
    return element.tag.rsplit("}", 1)[-1]


class KanbanFieldPreambleTest(unittest.TestCase):
    def test_rendered_kanban_fields_are_declared_before_templates(self):
        failures = []
        for path in sorted(ADDONS_DIR.rglob("*.xml")):
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError:
                continue

            for kanban in root.iter():
                if _tag_name(kanban) != "kanban":
                    continue

                declared = []
                templates = None
                for child in list(kanban):
                    child_tag = _tag_name(child)
                    if child_tag == "templates":
                        templates = child
                        break
                    if child_tag == "field" and child.get("name"):
                        declared.append(child.get("name"))

                if templates is None:
                    continue

                rendered = [
                    field.get("name")
                    for field in templates.iter()
                    if _tag_name(field) == "field" and field.get("name")
                ]
                missing = [name for name in dict.fromkeys(rendered) if name not in declared]
                if missing:
                    failures.append(
                        f"{path.relative_to(ADDONS_DIR)}: missing kanban field preamble "
                        f"declarations for {', '.join(missing)}"
                    )

        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
