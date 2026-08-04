from __future__ import annotations

import hashlib
import unittest

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.visual_kernel.fingerprint import (
    LayeredFingerprint,
    build_layered_fingerprint,
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


SPEC = digest("spec")
THEME = digest("theme")
IDENTITIES = {"kernel": digest("kernel-v1"), "elk": digest("elkjs-0.9.3"), "renderer": digest("svg-v1")}


def report_prior(gates: list[dict[str, str]], timelines: list[dict[str, str]], interactions: list[dict[str, str]]) -> str:
    ordered = lambda values: sorted(values, key=lambda item: (item["locale"].encode("utf-8"), item["variant"].encode("utf-8")))
    projection = {"gates": ordered(gates), "timelines": ordered(timelines), "interactions": ordered(interactions)}
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def fixture() -> dict[str, object]:
    scenes = [
        {"locale": "zh-Hans", "variant": "mobile", "sha256": digest("scene-zh-mobile"), "prior_sha256": SPEC},
        {"locale": "en", "variant": "desktop", "sha256": digest("scene-en-desktop"), "prior_sha256": SPEC},
    ]
    scene_by_key = {(item["locale"], item["variant"]): item["sha256"] for item in scenes}
    gates = [
        {"locale": "zh-Hans", "variant": "mobile", "sha256": digest("gate-zh-mobile"), "prior_sha256": scene_by_key[("zh-Hans", "mobile")]},
        {"locale": "en", "variant": "desktop", "sha256": digest("gate-en-desktop"), "prior_sha256": scene_by_key[("en", "desktop")]},
    ]
    gate_by_key = {(item["locale"], item["variant"]): item["sha256"] for item in gates}
    timelines = [
        {"locale": "zh-Hans", "variant": "mobile", "sha256": digest("timeline-zh-mobile"), "prior_sha256": gate_by_key[("zh-Hans", "mobile")]},
        {"locale": "en", "variant": "desktop", "sha256": digest("timeline-en-desktop"), "prior_sha256": gate_by_key[("en", "desktop")]},
    ]
    timeline_by_key = {(item["locale"], item["variant"]): item["sha256"] for item in timelines}
    interactions = [
        {"locale": "zh-Hans", "variant": "mobile", "sha256": digest("interaction-zh-mobile"), "prior_sha256": timeline_by_key[("zh-Hans", "mobile")]},
        {"locale": "en", "variant": "desktop", "sha256": digest("interaction-en-desktop"), "prior_sha256": timeline_by_key[("en", "desktop")]},
    ]
    prior = report_prior(gates, timelines, interactions)
    artifacts = [
        {"path": "assets/readme-showcase/zh-Hans/mobile.svg", "sha256": digest("svg-zh-mobile"), "prior_sha256": prior},
        {"path": "assets/readme-showcase/en/desktop.svg", "sha256": digest("svg-en-desktop"), "prior_sha256": prior},
    ]
    return {
        "spec_sha256": SPEC,
        "scenes": scenes,
        "theme_sha256": THEME,
        "identities": dict(IDENTITIES),
        "gates": gates,
        "timelines": timelines,
        "interactions": interactions,
        "artifacts": artifacts,
    }


def build(values: dict[str, object] | None = None) -> LayeredFingerprint:
    values = fixture() if values is None else values
    return build_layered_fingerprint(
        values["spec_sha256"],
        values["scenes"],
        values["theme_sha256"],
        values["identities"],
        values["gates"],
        values["timelines"],
        values["interactions"],
        values["artifacts"],
    )  # type: ignore[arg-type]


class LayeredFingerprintTests(unittest.TestCase):
    def assert_code(self, code: str, values: dict[str, object]) -> None:
        with self.assertRaises(ContractError) as raised:
            build(values)
        self.assertEqual(raised.exception.code, code)

    def test_permutations_are_canonical_and_hash_is_immutable(self) -> None:
        first = build()
        values = fixture()
        for key in ("scenes", "gates", "timelines", "interactions", "artifacts"):
            values[key] = list(reversed(values[key]))  # type: ignore[arg-type]
        values["identities"] = {"renderer": IDENTITIES["renderer"], "elk": IDENTITIES["elk"], "kernel": IDENTITIES["kernel"]}
        second = build(values)
        self.assertEqual(first.inventory_sha256, second.inventory_sha256)
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.sha256(), first.inventory_sha256)
        with self.assertRaises((AttributeError, TypeError)):
            first.scenes += ()  # type: ignore[misc]

    def test_projection_has_explicit_layer_order_and_no_workspace_or_clock_fields(self) -> None:
        result = build()
        self.assertEqual(
            [layer["name"] for layer in result.projection()["layers"]],
            ["spec", "scenes", "theme", "identities", "gates", "timelines", "interactions", "artifacts"],
        )
        self.assertNotIn("timestamp", result.as_dict())
        self.assertNotIn("workspace", result.as_dict())

    def test_single_layer_mutation_changes_inventory(self) -> None:
        baseline = build().inventory_sha256
        mutations = ("spec_sha256", "theme_sha256", "identities", "scenes", "gates", "timelines", "interactions", "artifacts")
        for layer in mutations:
            values = fixture()
            if layer == "spec_sha256":
                values[layer] = digest("spec-mutated")
                for record in values["scenes"]:  # type: ignore[index]
                    record["prior_sha256"] = values[layer]  # type: ignore[index]
            elif layer == "theme_sha256":
                values[layer] = digest("theme-mutated")
            elif layer == "identities":
                values[layer] = {**IDENTITIES, "renderer": digest("svg-v2")}
            elif layer == "scenes":
                values[layer][0]["sha256"] = digest("scene-mutated")  # type: ignore[index]
                for gate in values["gates"]:  # type: ignore[index]
                    matching = next(item for item in values["scenes"] if (item["locale"], item["variant"]) == (gate["locale"], gate["variant"]))  # type: ignore[index]
                    gate["prior_sha256"] = matching["sha256"]  # type: ignore[index]
            elif layer == "gates":
                values[layer][0]["sha256"] = digest("gate-mutated")  # type: ignore[index]
                for timeline in values["timelines"]:  # type: ignore[index]
                    matching = next(item for item in values["gates"] if (item["locale"], item["variant"]) == (timeline["locale"], timeline["variant"]))  # type: ignore[index]
                    timeline["prior_sha256"] = matching["sha256"]  # type: ignore[index]
            elif layer == "timelines":
                values[layer][0]["sha256"] = digest("timeline-mutated")  # type: ignore[index]
                for interaction in values["interactions"]:  # type: ignore[index]
                    matching = next(item for item in values["timelines"] if (item["locale"], item["variant"]) == (interaction["locale"], interaction["variant"]))  # type: ignore[index]
                    interaction["prior_sha256"] = matching["sha256"]  # type: ignore[index]
            elif layer == "interactions":
                values[layer][0]["sha256"] = digest("interaction-mutated")  # type: ignore[index]
            else:
                values[layer][0]["sha256"] = digest("artifact-mutated")  # type: ignore[index]
            if layer in {"gates", "timelines", "interactions", "scenes"}:
                prior = report_prior(values["gates"], values["timelines"], values["interactions"])  # type: ignore[arg-type]
                for artifact in values["artifacts"]:  # type: ignore[index]
                    artifact["prior_sha256"] = prior  # type: ignore[index]
            self.assertNotEqual(build(values).inventory_sha256, baseline, layer)

    def test_duplicate_missing_invalid_and_stale_bindings_fail_closed(self) -> None:
        duplicate = fixture()
        duplicate["scenes"] = [*duplicate["scenes"], dict(duplicate["scenes"][0])]  # type: ignore[index]
        self.assert_code("E_VISUAL_FINGERPRINT", duplicate)

        duplicate_path = fixture()
        duplicate_path["artifacts"] = [*duplicate_path["artifacts"], dict(duplicate_path["artifacts"][0])]  # type: ignore[index]
        self.assert_code("E_VISUAL_FINGERPRINT", duplicate_path)

        missing = fixture()
        missing["timelines"] = missing["timelines"][:1]  # type: ignore[index]
        self.assert_code("E_VISUAL_FINGERPRINT", missing)

        invalid = fixture()
        invalid["artifacts"][0]["sha256"] = "not-a-digest"  # type: ignore[index]
        self.assert_code("E_VISUAL_FINGERPRINT", invalid)

        stale = fixture()
        stale["gates"][0]["prior_sha256"] = digest("stale")  # type: ignore[index]
        self.assert_code("E_VISUAL_FINGERPRINT", stale)

        absolute = fixture()
        absolute["artifacts"][0]["path"] = "/private/tmp/relocated.svg"  # type: ignore[index]
        self.assert_code("E_VISUAL_FINGERPRINT", absolute)

        timestamp = fixture()
        timestamp["scenes"][0]["timestamp"] = "2026-08-04T00:00:00Z"  # type: ignore[index]
        self.assert_code("E_VISUAL_FINGERPRINT", timestamp)

        locale = fixture()
        locale["scenes"][0]["locale"] = "zh-CN"  # type: ignore[index]
        self.assert_code("E_VISUAL_FINGERPRINT", locale)

        identity = fixture()
        identity["identities"]["kernel"] = "kernel-v1"  # type: ignore[index]
        self.assert_code("E_VISUAL_FINGERPRINT", identity)


if __name__ == "__main__":
    unittest.main()
