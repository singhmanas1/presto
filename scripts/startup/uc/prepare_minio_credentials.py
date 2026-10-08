#!/usr/bin/env python3
"""Create the local bucket and give Unity Catalog a one-hour key it can hand out.

Unity Catalog 0.5 returns this key when a client asks for temporary
credentials. The coordinator asks for it on s3:// tables. Spark uses the same
key to write the managed table. The key expires after one hour.
"""

import json
from pathlib import Path

import boto3
from botocore.client import Config

ENDPOINT = "http://127.0.0.1:9000"
ROOT_KEY = "minioadmin"
ROOT_SECRET = "minioadmin"
BUCKET = "warehouse"
PROPERTIES = Path("/home/nvidia/Presto/uc/server.properties")
BEGIN = "# BEGIN s3 simulation"
END = "# END s3 simulation"


def client(service):
    return boto3.client(
        service,
        endpoint_url=ENDPOINT,
        aws_access_key_id=ROOT_KEY,
        aws_secret_access_key=ROOT_SECRET,
        region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}),
    )


def main():
    s3 = client("s3")
    try:
        s3.create_bucket(Bucket=BUCKET)
        print(f"created bucket {BUCKET}")
    except s3.exceptions.BucketAlreadyOwnedByYou:
        print(f"bucket {BUCKET} already exists")
    except Exception as error:
        code = getattr(getattr(error, "response", {}).get("Error", {}), "get", lambda *_: "")("Code")
        if code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            print(f"bucket {BUCKET} already exists")
        else:
            raise

    policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {"Effect": "Allow", "Action": ["s3:*"], "Resource": ["arn:aws:s3:::*"]}
            ],
        }
    )
    credentials = client("sts").assume_role(
        RoleArn="arn:aws:iam::000000000000:role/uc",
        RoleSessionName="uc",
        DurationSeconds=3600,
        Policy=policy,
    )["Credentials"]

    block = "\n".join(
        [
            BEGIN,
            "s3.bucketPath.0=s3://warehouse",
            "s3.region.0=us-east-1",
            "s3.awsRoleArn.0=",
            f"s3.accessKey.0={credentials['AccessKeyId']}",
            f"s3.secretKey.0={credentials['SecretAccessKey']}",
            f"s3.sessionToken.0={credentials['SessionToken']}",
            END,
            "",
        ]
    )
    text = PROPERTIES.read_text()
    if BEGIN in text:
        before, rest = text.split(BEGIN, 1)
        _, after = rest.split(END, 1)
        text = before + block + after.lstrip("\n")
    else:
        text = text.rstrip() + "\n" + block
    PROPERTIES.write_text(text)
    print("Unity Catalog can vend a MinIO key for s3://warehouse")


if __name__ == "__main__":
    main()
