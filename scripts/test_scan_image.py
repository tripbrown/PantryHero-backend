import argparse
import json
import sys

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--scan_kind", default="receipt")
    args = parser.parse_args()

    with open(args.image, "rb") as f:
        files = {"file": (args.image, f, "application/octet-stream")}
        data = {"scan_kind": args.scan_kind}
        response = requests.post("http://127.0.0.1:8000/scan", files=files, data=data)

    print("Status:", response.status_code)
    try:
        print(json.dumps(response.json(), indent=2))
    except json.JSONDecodeError:
        print(response.text)


if __name__ == "__main__":
    sys.exit(main())
