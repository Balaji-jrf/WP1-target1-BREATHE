import io
import unittest
from unittest.mock import patch

import lambda_starter
import pymupdf
from lambda_starter import _blocks_to_analysis, _crop_figures


def geometry():
    return {
        "BoundingBox": {
            "Left": 0.1,
            "Top": 0.2,
            "Width": 0.3,
            "Height": 0.1,
        }
    }


class BlocksToAnalysisTests(unittest.TestCase):
    def test_extracts_text_layout_tables_and_figures(self):
        blocks = [
            {"Id": "page-1", "BlockType": "PAGE", "Page": 1},
            {
                "Id": "line-1",
                "BlockType": "LINE",
                "Page": 1,
                "Text": "Annual report",
                "Confidence": 98.5,
                "Geometry": geometry(),
            },
            {
                "Id": "title-1",
                "BlockType": "LAYOUT_TITLE",
                "Page": 1,
                "Geometry": geometry(),
                "Relationships": [{"Type": "CHILD", "Ids": ["line-1"]}],
            },
            {
                "Id": "table-1",
                "BlockType": "TABLE",
                "Page": 1,
                "Geometry": geometry(),
                "Relationships": [{"Type": "CHILD", "Ids": ["cell-1"]}],
            },
            {
                "Id": "cell-1",
                "BlockType": "CELL",
                "RowIndex": 1,
                "ColumnIndex": 1,
                "Geometry": geometry(),
                "Relationships": [{"Type": "CHILD", "Ids": ["word-1"]}],
            },
            {"Id": "word-1", "BlockType": "WORD", "Text": "Revenue"},
            {
                "Id": "figure-1",
                "BlockType": "LAYOUT_FIGURE",
                "Page": 1,
                "Geometry": geometry(),
            },
            {"Id": "page-2", "BlockType": "PAGE", "Page": 2},
        ]

        analysis = _blocks_to_analysis(blocks)

        self.assertEqual(analysis["total_pages"], 2)
        self.assertEqual(analysis["elements"][0]["text"], "Annual report")
        self.assertEqual(analysis["elements"][0]["bbox"], [0.1, 0.2, 0.4, 0.3])
        self.assertEqual(analysis["layout"][0]["type"], "title")
        self.assertEqual(analysis["layout"][0]["text"], "Annual report")
        self.assertEqual(analysis["tables"][0]["rows"], [["Revenue"]])
        self.assertEqual(analysis["figures"][0]["bbox"], [0.1, 0.2, 0.4, 0.3])


class FigureCropTests(unittest.TestCase):
    def test_saves_figure_crop_and_records_s3_key(self):
        document = pymupdf.open()
        page = document.new_page(width=200, height=300)
        page.draw_rect(pymupdf.Rect(20, 60, 100, 180), color=(1, 0, 0), fill=(1, 0, 0))
        source_bytes = document.tobytes()
        document.close()
        figures = [{
            "figure_id": "figure-0001",
            "page": 1,
            "bbox": [0.1, 0.2, 0.5, 0.6],
            "crop_status": "pending",
        }]

        with (
            patch.object(lambda_starter, "S3_BUCKET", "test-bucket"),
            patch.object(
                lambda_starter.s3,
                "get_object",
                return_value={"Body": io.BytesIO(source_bytes)},
            ),
            patch.object(lambda_starter.s3, "put_object") as put_object,
        ):
            result = _crop_figures(
                "uploads/doc-1/input.pdf",
                "doc-1",
                "input.pdf",
                figures,
            )

        self.assertEqual(result[0]["crop_status"], "saved")
        self.assertEqual(
            result[0]["image_s3_key"],
            "processed/doc-1/figures/page-0001-figure-0001.png",
        )
        self.assertEqual(put_object.call_args.kwargs["ContentType"], "image/png")
        crop = pymupdf.Pixmap(put_object.call_args.kwargs["Body"])
        self.assertEqual((crop.width, crop.height), (120, 180))

    def test_crops_raster_image_sources(self):
        image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 80), False)
        image.clear_with(0x336699)
        source_bytes = image.tobytes("png")
        figures = [{"page": 1, "bbox": [0.1, 0.1, 0.5, 0.5]}]

        with (
            patch.object(lambda_starter, "S3_BUCKET", "test-bucket"),
            patch.object(
                lambda_starter.s3,
                "get_object",
                return_value={"Body": io.BytesIO(source_bytes)},
            ),
            patch.object(lambda_starter.s3, "put_object") as put_object,
        ):
            result = _crop_figures(
                "uploads/doc-1/input.png",
                "doc-1",
                "input.png",
                figures,
            )
        self.assertEqual(result[0]["crop_status"], "saved")
        self.assertEqual(
            put_object.call_args.kwargs["Key"],
            "processed/doc-1/figures/page-0001-figure-0001.png",
        )
        crop = pymupdf.Pixmap(put_object.call_args.kwargs["Body"])
        self.assertGreater(crop.width, 0)
        self.assertGreater(crop.height, 0)
        self.assertLess(crop.width, 100)
        self.assertLess(crop.height, 80)

    def test_marks_invalid_figure_crop_failed(self):
        document = pymupdf.open()
        document.new_page(width=100, height=100)
        source_bytes = document.tobytes()
        document.close()
        figures = [{"page": 1, "bbox": [0.8, 0.8, 0.2, 0.2]}]

        with (
            patch.object(lambda_starter, "S3_BUCKET", "test-bucket"),
            patch.object(
                lambda_starter.s3,
                "get_object",
                return_value={"Body": io.BytesIO(source_bytes)},
            ),
            patch.object(lambda_starter.s3, "put_object") as put_object,
        ):
            result = _crop_figures("uploads/doc-1/input.pdf", "doc-1", "input.pdf", figures)

        self.assertEqual(result[0]["crop_status"], "failed")
        self.assertNotIn("image_s3_key", result[0])
        put_object.assert_not_called()


if __name__ == "__main__":
    unittest.main()
