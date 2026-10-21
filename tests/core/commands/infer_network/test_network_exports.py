from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest
from xml.etree import ElementTree as ET

from andrea.core.commands.infer_network.commons.network_exports import (
    GEXF_NS,
    GRAPHML_NS,
    MERGED_NETWORK_REQUIRED_COLUMNS,
    export_network_gexf,
    export_network_graphml,
)


class NetworkExportPrecisionTests(unittest.TestCase):
    def test_xml_weights_and_score_attributes_roundtrip_distinct_binary64_values(
        self,
    ) -> None:
        scores = [
            "0.12345678912345678",
            "0.1234567891234568",
            "1.2345678912345678e-200",
            "1",
            "0",
        ]
        expected = [float(value) for value in scores]
        self.assertNotEqual(expected[0], expected[1])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            merged = root / "merged.csv"
            with merged.open("w", newline="") as stream:
                writer = csv.DictWriter(
                    stream, fieldnames=MERGED_NETWORK_REQUIRED_COLUMNS
                )
                writer.writeheader()
                writer.writerows(
                    {
                        "source": f"G{index}",
                        "target": "target",
                        "score": score,
                        "sign": "?",
                        "evidence": "association",
                        "context": "global",
                        "tool_id": "precise",
                    }
                    for index, score in enumerate(scores)
                )
            original = merged.read_bytes()
            graphml, gexf = root / "network.graphml", root / "network.gexf"
            self.assertEqual(export_network_graphml(merged, graphml), len(scores))
            self.assertEqual(export_network_gexf(merged, gexf), len(scores))
            edges = ET.parse(graphml).findall(f".//{{{GRAPHML_NS}}}edge")
            for key in ("edge_weight", "edge_score"):
                values = [
                    float(edge.find(f"{{{GRAPHML_NS}}}data[@key='{key}']").text)
                    for edge in edges
                ]
                self.assertEqual(values, expected)
            edges = ET.parse(gexf).findall(f".//{{{GEXF_NS}}}edge")
            self.assertEqual([float(edge.attrib["weight"]) for edge in edges], expected)
            values = [
                float(
                    edge.find(
                        f"{{{GEXF_NS}}}attvalues/{{{GEXF_NS}}}attvalue[@for='score']"
                    ).attrib["value"]
                )
                for edge in edges
            ]
            self.assertEqual(values, expected)
            self.assertEqual(merged.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
