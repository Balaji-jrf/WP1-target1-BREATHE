import io
import json
import unittest
from unittest.mock import patch

import lambda_retrieval


class FigureUrlTests(unittest.TestCase):
    def test_signs_only_saved_figure_crops(self):
        document = {
            "document_id": "doc-1",
            "filename": "input.pdf",
            "s3_bucket": "test-bucket",
            "s3_key": "uploads/doc-1/input.pdf",
            "ocr_s3_key": "processed/doc-1/ocr.json",
            "status": "completed",
        }
        ocr = {
            "figures": [
                {
                    "crop_status": "saved",
                    "image_s3_key": "processed/doc-1/figures/figure.png",
                },
                {"crop_status": "failed"},
            ],
        }
        signed_url = lambda params: f"signed:{params['Key']}"

        with (
            patch.object(lambda_retrieval.table, "get_item", return_value={"Item": document}),
            patch.object(
                lambda_retrieval.s3,
                "generate_presigned_url",
                side_effect=lambda _operation, Params, ExpiresIn: signed_url(Params),
            ),
            patch.object(
                lambda_retrieval.s3,
                "get_object",
                return_value={"Body": io.BytesIO(json.dumps(ocr).encode())},
            ),
        ):
            result = lambda_retrieval._get_document("doc-1")

        self.assertEqual(
            result["figure_presigned_urls"],
            {"processed/doc-1/figures/figure.png": "signed:processed/doc-1/figures/figure.png"},
        )
        self.assertEqual(
            result["ocr_presigned_url"],
            "signed:processed/doc-1/ocr.json",
        )


if __name__ == "__main__":
    unittest.main()
