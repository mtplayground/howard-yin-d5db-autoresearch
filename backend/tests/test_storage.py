import unittest
from io import BytesIO

from botocore.exceptions import ClientError

from app.services.storage import ObjectNotFoundError, ObjectStorageClient, StorageConfig


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.last_put: dict[str, object] | None = None

    def put_object(self, **request: object) -> None:
        self.last_put = request
        self.objects[(str(request["Bucket"]), str(request["Key"]))] = bytes(request["Body"])  # type: ignore[arg-type]

    def get_object(self, **request: object) -> dict[str, BytesIO]:
        key = (str(request["Bucket"]), str(request["Key"]))
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": BytesIO(self.objects[key])}

    def head_object(self, **request: object) -> None:
        key = (str(request["Bucket"]), str(request["Key"]))
        if key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")

    def generate_presigned_url(self, operation: str, *, Params: dict[str, str], ExpiresIn: int) -> str:
        return f"https://objects.example/{Params['Bucket']}/{Params['Key']}?op={operation}&ttl={ExpiresIn}"


class ObjectStorageClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake_s3 = FakeS3Client()
        self.client = ObjectStorageClient(
            StorageConfig(
                bucket="bucket",
                prefix="workspace/artifacts/",
                region="auto",
                endpoint_url="https://objects.example",
                access_key_id="access",
                secret_access_key="secret",
            ),
            s3_client=self.fake_s3,  # type: ignore[arg-type]
        )

    def test_upload_scopes_key_and_sets_content_length(self) -> None:
        ref = self.client.upload_bytes(
            "runs/1/log.txt",
            b"hello",
            content_type="text/plain",
            checksum_sha256="abc123",
        )

        self.assertEqual(ref.key, "workspace/artifacts/runs/1/log.txt")
        self.assertEqual(ref.byte_size, 5)
        self.assertEqual(ref.checksum_sha256, "abc123")
        self.assertEqual(self.fake_s3.last_put["Key"], "workspace/artifacts/runs/1/log.txt")
        self.assertEqual(self.fake_s3.last_put["ContentLength"], 5)
        self.assertEqual(self.fake_s3.last_put["ContentType"], "text/plain")
        self.assertEqual(self.fake_s3.last_put["Metadata"], {"checksum-sha256": "abc123"})

    def test_existing_prefixed_key_is_not_double_prefixed(self) -> None:
        key = self.client.storage_key("workspace/artifacts/papers/paper.pdf")

        self.assertEqual(key, "workspace/artifacts/papers/paper.pdf")

    def test_download_and_exists_use_scoped_key(self) -> None:
        self.client.upload_bytes("figures/chart.png", b"png")

        self.assertTrue(self.client.exists("figures/chart.png"))
        self.assertEqual(self.client.download_bytes("figures/chart.png"), b"png")

    def test_missing_download_raises_not_found(self) -> None:
        with self.assertRaises(ObjectNotFoundError):
            self.client.download_bytes("missing.txt")

    def test_rejects_parent_directory_segments(self) -> None:
        with self.assertRaises(ValueError):
            self.client.storage_key("../outside.txt")


if __name__ == "__main__":
    unittest.main()
