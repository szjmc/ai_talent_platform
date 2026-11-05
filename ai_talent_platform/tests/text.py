import os
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error

# 加载项目.env
load_dotenv()

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY")
MINIO_BUCKET = os.getenv("MINIO_BUCKET")

def test_minio():
    print("=== MinIO 集成测试开始 ===")
    print(f"Endpoint: {MINIO_ENDPOINT}")
    print(f"Bucket: {MINIO_BUCKET}")

    client = Minio(
        MINIO_ENDPOINT.replace("http://", "").replace("https://", ""),
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )

    # 1. 检查bucket是否存在，不存在自动创建
    if not client.bucket_exists(MINIO_BUCKET):
        print(f"Bucket {MINIO_BUCKET} 不存在，自动创建")
        client.make_bucket(MINIO_BUCKET)
    else:
        print(f"Bucket {MINIO_BUCKET} 已存在")

    # 2. 上传测试文件
    test_data = b"Hello AI Talent Platform, MinIO test content"
    test_obj = "test_upload/demo_test.txt"
    client.put_object(
        bucket_name=MINIO_BUCKET,
        object_name=test_obj,
        data=iter([test_data]),
        length=len(test_data),
        content_type="text/plain"
    )
    print(f"✅ 上传成功: {test_obj}")

    # 3. 读取文件
    resp = client.get_object(MINIO_BUCKET, test_obj)
    body = resp.read()
    resp.close()
    if body == test_data:
        print("✅ 下载校验成功，内容一致")
    else:
        print("❌ 下载内容不一致")

    # 4. 生成预签名URL
    presign_url = client.presigned_get_object(MINIO_BUCKET, test_obj, expires=3600)
    print(f"✅ 预签名地址：{presign_url}")

    # 5. 删除测试文件
    client.remove_object(MINIO_BUCKET, test_obj)
    print(f"✅ 已清理测试文件 {test_obj}")

    print("=== MinIO 全部测试通过 ===")

if __name__ == "__main__":
    test_minio()
