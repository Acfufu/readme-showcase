import shutil
import unittest
from pathlib import Path

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel.raster import render_svg


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "visual" / "hero-ok.svg"


class RenderSvgTest(unittest.TestCase):
    @unittest.skipUnless(
        shutil.which("resvg") or shutil.which("rsvg-convert"), "no rasterizer installed"
    )
    def test_renders_900px_png(self):
        out = Path(__file__).resolve().parent / "rendered-900.png"
        try:
            render_svg(str(FIXTURE), str(out), width=900)
            self.assertTrue(out.exists())
            self.assertGreater(out.stat().st_size, 100)
        finally:
            out.unlink(missing_ok=True)

    def test_missing_dependency_raises_contract_error(self):
        # Uses the REAL fixture so E_RASTER_INPUT is not hit first: the bogus
        # rasterizer name exercises the dependency branch (subprocess.run
        # raises FileNotFoundError, which _run must convert to ContractError).
        with self.assertRaises(ContractError) as caught:
            render_svg(str(FIXTURE), "/tmp/out.png", width=900,
                       rasterizer="definitely-missing-binary")
        self.assertEqual(caught.exception.code, "E_RASTER_DEPENDENCY")
