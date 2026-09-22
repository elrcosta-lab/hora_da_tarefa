"""StorageProvider — bytes de imagem fora da memória (SPECS §8.2, RNF-09/11).

Backends: `local` (disco, dev/testes) e `s3` (MinIO/prod, S3-compatible via boto3).
Chaves opacas `original/<uuid>.jpg`; sem path traversal. Falta de objeto → KeyError.
"""
import os
from pathlib import Path


class StorageProvider:
    def put(self, key: str, data: bytes, content_type: str) -> str:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in Path(key).parts:
        raise ValueError(f"chave de storage inválida: {key!r}")
    return key


class LocalStorage(StorageProvider):
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        _check_key(key)
        p = self.root / key
        if self.root not in p.resolve().parents and p.resolve() != self.root:
            raise ValueError(f"chave fora da raiz: {key!r}")
        return p

    def put(self, key: str, data: bytes, content_type: str) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise KeyError(key)
        return p.read_bytes()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()


class S3Storage(StorageProvider):
    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str):
        import boto3  # lazy: só necessário no backend s3 (prod)

        self.bucket = bucket
        self._client = boto3.client(
            "s3", endpoint_url=endpoint,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        )
        try:
            self._client.head_bucket(Bucket=bucket)
        except Exception:
            self._client.create_bucket(Bucket=bucket)

    def put(self, key: str, data: bytes, content_type: str) -> str:
        _check_key(key)
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key: str) -> bytes:
        _check_key(key)
        try:
            resp = self._client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            if "NoSuchKey" in type(exc).__name__ or "404" in str(exc) or "Not Found" in str(exc):
                raise KeyError(key) from exc
            # botocore ClientError com Code NoSuchKey
            code = getattr(getattr(exc, "response", {}), "get", lambda *a: None)("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404", "NotFound"):
                raise KeyError(key) from exc
            raise
        return resp["Body"].read()

    def delete(self, key: str) -> None:
        _check_key(key)
        self._client.delete_object(Bucket=self.bucket, Key=key)


def get_storage() -> StorageProvider:
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()
    if backend == "s3":
        return S3Storage(
            endpoint=os.environ.get("S3_ENDPOINT", "http://minio:9000"),
            bucket=os.environ.get("S3_BUCKET", "hora-tarefa-images"),
            access_key=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
            secret_key=os.environ.get("S3_SECRET_KEY", "minioadmin123"),
        )
    root = os.environ.get("STORAGE_LOCAL_DIR")
    if not root:
        root = Path(__file__).resolve().parents[2] / "data" / "images"
    return LocalStorage(root)


def retention_days() -> int:
    try:
        return max(1, int(os.environ.get("RETENTION_IMAGE_DAYS", "90")))
    except ValueError:
        return 90
